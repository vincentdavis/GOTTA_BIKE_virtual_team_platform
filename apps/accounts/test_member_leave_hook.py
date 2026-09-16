"""The Discord bot reports a member leaving the moment it happens.

Before this, a departure was only noticed by the scheduled REST sync, so a rider who left the
server kept their session and API keys for up to ``SCHEDULER_SYNC_GUILD_MEMBERS_HOURS``. The bot
now calls ``POST /api/dbot/member_left/{discord_id}`` from its member-remove event, and
``apps.accounts.services.record_member_departure`` stamps ``GuildMember.date_left`` at once.

Because a stamp can now be newer than a member list, both syncs clear a departure only when
their list was read after it (``observed_at``); the bot's push sends its own ``observed_at``.
A Discord login that passes the live guild check still clears the stamp.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from constance.test import override_config
from django.apps import apps as django_apps
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services
from apps.accounts.membership import is_departed_member
from apps.accounts.models import GuildMember
from apps.accounts.services import apply_guild_member_sync, is_discord_snowflake, record_member_departure
from apps.dbot_api.api import _push_observed_at
from apps.tickets.models import Ticket
from apps.user_api.services import user_can_use_api

TEAM_ROLE = "555000000000000007"
RIDER_ID = "731000000000000001"
API_KEY = "s3cret-bot-key"
BOT_GUILD_ID = 42
BOT_USER_ID = "990000000000000001"
BOT_HEADERS = {
    "HTTP_X_API_KEY": API_KEY,
    "HTTP_X_GUILD_ID": str(BOT_GUILD_ID),
    "HTTP_X_DISCORD_USER_ID": BOT_USER_ID,
}
OTHER_IDS = [str(810000000000000000 + n) for n in range(20)]


def _member(discord_id):
    """Build one normalized member payload entry.

    Returns:
        The dict apply_guild_member_sync and the bot push expect.

    """
    return {
        "discord_id": str(discord_id),
        "username": f"member{discord_id}",
        "display_name": "",
        "nickname": "",
        "avatar_hash": "",
        "roles": [],
        "joined_at": None,
        "is_bot": False,
    }


def _rest_sync(ids, **kwargs):
    """Run the sync the way the REST task does.

    Returns:
        The sync result.

    """
    return apply_guild_member_sync([_member(i) for i in ids], source="discord_api", authoritative=True, **kwargs)


def _report_left(client, discord_id=RIDER_ID, body=None, **headers):
    """POST the bot's member-left report.

    Returns:
        The response.

    """
    sent = {**BOT_HEADERS, **headers}
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=BOT_GUILD_ID):
        return client.post(
            f"/api/dbot/member_left/{discord_id}",
            {} if body is None else body,
            content_type="application/json",
            **sent,
        )


def _bot_push(client, ids, **extra):
    """POST a member list to the bot's sync endpoint.

    Returns:
        The response.

    """
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=BOT_GUILD_ID):
        return client.post(
            "/api/dbot/sync_guild_members",
            {"members": [_member(i) for i in ids], **extra},
            content_type="application/json",
            **BOT_HEADERS,
        )


def _row(discord_id=RIDER_ID):
    return GuildMember.objects.get(discord_id=discord_id)


def _left_tickets():
    return Ticket.objects.filter(guild_member__isnull=False)


def _is_signed_in(client):
    return "_auth_user_id" in client.session


@pytest.fixture(autouse=True)
def _team_role_grants_membership(db):
    # Constance is database-backed, so every test here needs the database.
    with override_config(PERM_TEAM_MEMBER_ROLES=f'["{TEAM_ROLE}"]', PERM_ROLES_REQUIRED_USE_API="[]"):
        yield


@pytest.fixture
def rider(db, user_model):
    """Make a rider whose team_member access comes from a Discord role.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username="leave_rider",
        email="leave_rider@example.test",
        discord_id=RIDER_ID,
        discord_roles={TEAM_ROLE: "Team Member"},
    )


@pytest.fixture
def synced_guild(rider):
    """Sync a guild that includes the rider, so the rider has an active, linked row.

    Returns:
        The member ids, the rider's included.

    """
    ids = [RIDER_ID, *OTHER_IDS]
    _rest_sync(ids)
    if _row().user_id != rider.pk or _row().date_left is not None:
        pytest.fail("the rider should have an active, linked row")
    return ids


# --- the endpoint's gate ------------------------------------------------------------------


@pytest.mark.django_db
def test_a_report_without_the_key_is_refused(client, synced_guild):
    response = _report_left(client, HTTP_X_API_KEY="wrong")

    assert response.status_code == 401
    assert _row().date_left is None
    assert not _left_tickets().exists()


@pytest.mark.django_db
def test_a_report_with_no_key_header_is_refused(client, synced_guild):
    headers = {k: v for k, v in BOT_HEADERS.items() if k != "HTTP_X_API_KEY"}
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=BOT_GUILD_ID):
        response = client.post(f"/api/dbot/member_left/{RIDER_ID}", {}, content_type="application/json", **headers)

    assert response.status_code == 401
    assert _row().date_left is None


@pytest.mark.django_db
def test_a_report_for_another_guild_is_refused(client, synced_guild):
    response = _report_left(client, HTTP_X_GUILD_ID=str(BOT_GUILD_ID + 1))

    assert response.status_code == 401
    assert _row().date_left is None


@pytest.mark.django_db
@pytest.mark.parametrize("bad_id", ["abc", "12a", "-1", "1e5", "1" * 21])
def test_an_id_that_is_not_a_snowflake_is_refused(client, db, bad_id):
    response = _report_left(client, bad_id)

    assert response.status_code == 400
    assert "error" in response.json()
    assert not GuildMember.objects.exists()
    assert not Ticket.objects.exists()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("1" * 20, True),
        (RIDER_ID, True),
        ("", False),
        ("1" * 21, False),
        (" 1", False),
        ("1\n", False),
        ("\u0661\u0662\u0663", False),  # Arabic-Indic digits: str.isdigit() says yes
        ("\uff11\uff12", False),  # full-width digits
        (123, False),
        (None, False),
    ],
)
def test_only_ascii_digit_ids_are_snowflakes(value, expected):
    assert is_discord_snowflake(value) is expected


@pytest.mark.django_db
def test_the_service_refuses_a_bad_id_too(db):
    with pytest.raises(ValueError, match="ASCII digits"):
        record_member_departure("12a")
    assert not GuildMember.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("body", ['{"username": 5}', '{"is_bot": "not-a-bool"}'])
def test_a_body_of_the_wrong_shape_is_refused(client, synced_guild, body):
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=BOT_GUILD_ID):
        response = client.post(
            f"/api/dbot/member_left/{RIDER_ID}", body, content_type="application/json", **BOT_HEADERS
        )

    assert response.status_code == 422
    assert _row().date_left is None


# --- recording the departure --------------------------------------------------------------


@pytest.mark.django_db
def test_an_active_member_is_stamped_and_gets_one_ticket(client, rider, synced_guild):
    before = timezone.now()

    response = _report_left(client)

    assert response.status_code == 200
    assert response.json() == {"status": "departed", "created": False, "ticket_created": True}
    row = _row()
    assert row.date_left is not None
    assert row.date_left >= before
    assert row.user_id == rider.pk
    assert not services.is_never_seen(row)
    assert is_departed_member(rider)
    assert list(_left_tickets().values_list("guild_member_id", flat=True)) == [row.pk]


@pytest.mark.django_db
def test_a_second_report_only_moves_the_stamp_up(client, rider, synced_guild):
    _report_left(client)
    stamped = _row().date_left

    response = _report_left(client)

    assert response.status_code == 200
    assert response.json() == {"status": "already_departed", "created": False, "ticket_created": False}
    assert _row().date_left >= stamped
    assert _row().user_id == rider.pk
    assert is_departed_member(rider)
    assert _left_tickets().count() == 1


@pytest.mark.django_db
def test_a_report_ignores_the_body_for_a_known_member(client, synced_guild):
    _report_left(client, body={"username": "renamed", "display_name": "Renamed", "is_bot": True})

    row = _row()
    assert row.username == f"member{RIDER_ID}"
    assert row.display_name == ""
    assert row.is_bot is False


@pytest.mark.django_db
def test_an_unknown_member_gets_a_departed_row_that_is_not_never_seen(client, rider):
    response = _report_left(
        client, body={"username": "leaver", "display_name": "The Leaver", "avatar_hash": "abc123", "is_bot": False}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "departed", "created": True, "ticket_created": True}
    row = _row()
    assert (row.username, row.display_name, row.avatar_hash, row.is_bot) == ("leaver", "The Leaver", "abc123", False)
    assert row.user_id == rider.pk
    assert row.date_left > row.date_created
    # The bot watched them leave, so every never-seen check must say no.
    assert not services.is_never_seen(row)
    assert not GuildMember.objects.filter(services.never_seen_in_guild()).exists()
    assert services.annotate_never_seen(GuildMember.objects.all()).get().never_seen is False
    assert is_departed_member(rider)
    assert _left_tickets().get().guild_member_id == row.pk


@pytest.mark.django_db
def test_an_empty_body_creates_a_blank_row(client, db):
    response = _report_left(client, body={})

    # Nobody's account holds the id, so there is nothing to follow up and no ticket.
    assert response.json() == {"status": "departed", "created": True, "ticket_created": False}
    row = _row()
    assert (row.username, row.display_name, row.avatar_hash, row.is_bot, row.user_id) == ("", "", "", False, None)
    assert row.date_left is not None
    assert not services.is_never_seen(row)
    assert not Ticket.objects.exists()


@pytest.mark.django_db
def test_the_ticket_for_a_row_with_no_names_shows_only_the_id(client, rider):
    _report_left(client, body={})

    ticket = _left_tickets().get()
    # With no name to show, the ticket is titled by the id rather than left blank...
    assert ticket.title == f"Member left guild: Discord ID {RIDER_ID}"
    # ...and the details skip the empty name lines instead of printing `` or repeating the id.
    assert "Discord handle" not in ticket.details
    assert "Display name" not in ticket.details
    assert ticket.details.count(RIDER_ID) == 2  # the link text and the Discord ID line
    assert f"- **Discord ID:** `{RIDER_ID}`" in ticket.details
    assert f"- **Registered user:** [Discord ID {RIDER_ID}]" in ticket.details


@pytest.mark.django_db
def test_unknown_ids_with_no_account_get_a_row_but_no_ticket(client, db):
    """Join-and-leave churn must not fill the ticket queue."""
    for discord_id in OTHER_IDS:
        response = _report_left(client, discord_id, body={"username": "drive_by"})
        assert response.json() == {"status": "departed", "created": True, "ticket_created": False}

    assert GuildMember.objects.filter(date_left__isnull=False).count() == len(OTHER_IDS)
    assert not Ticket.objects.exists()


@pytest.mark.django_db
def test_an_unknown_id_an_unlinked_account_holds_still_gets_a_ticket(client, rider, user_model):
    user_model.objects.create_user(username="leave_twin", email="twin@example.test", discord_id=RIDER_ID)

    response = _report_left(client)

    assert response.json() == {"status": "departed", "created": True, "ticket_created": True}
    assert _row().user_id is None
    assert _left_tickets().get().guild_member_id == _row().pk


@pytest.mark.django_db
def test_null_fields_read_as_blank(client, db):
    response = _report_left(client, body={"username": None, "display_name": None, "avatar_hash": None, "is_bot": None})

    assert response.status_code == 200
    row = _row()
    assert (row.username, row.display_name, row.avatar_hash, row.is_bot) == ("", "", "", False)


@pytest.mark.django_db
def test_an_over_long_value_is_cut_to_the_field(client, db):
    response = _report_left(client, body={"username": "u" * 500})

    assert response.status_code == 200
    assert _row().username == "u" * GuildMember._meta.get_field("username").max_length


@pytest.mark.django_db
def test_a_second_report_for_a_new_row_adds_nothing(client, rider):
    _report_left(client)
    response = _report_left(client)

    assert response.json() == {"status": "already_departed", "created": False, "ticket_created": False}
    assert GuildMember.objects.count() == 1
    assert _left_tickets().count() == 1


@pytest.mark.django_db
def test_a_shared_discord_id_is_recorded_unlinked(client, rider, user_model):
    twin = user_model.objects.create_user(
        username="leave_twin",
        email="leave_twin@example.test",
        discord_id=RIDER_ID,
        discord_roles={TEAM_ROLE: "Team Member"},
    )

    _report_left(client)

    assert _row().user_id is None
    # The access rule reads the row by discord_id, so both accounts are out.
    assert is_departed_member(rider)
    assert is_departed_member(twin)


@pytest.mark.django_db
def test_an_account_already_linked_to_another_row_keeps_that_link(client, rider):
    old = GuildMember.objects.create(discord_id="731000000000000999", username="old_account", user=rider)

    _report_left(client)

    assert _row().user_id is None
    old.refresh_from_db()
    assert old.user_id == rider.pk
    assert is_departed_member(rider)


CLEANUP_LINE = "- Event signup: Some Race"


@pytest.mark.django_db
def test_the_ticket_does_not_blame_an_account_that_moved_to_another_discord_id(client, rider, synced_guild):
    """The rider signed in with a new Discord account; the old one then left the server.

    No sync has released the old row's link yet, but the account is still in the server
    under its new id, so its squads and signups are not this departure's to clean up.
    """
    rider.discord_id = "731000000000000777"
    rider.save(update_fields=["discord_id"])

    with patch("apps.tickets.services._member_cleanup_lines", return_value=[CLEANUP_LINE]) as cleanup:
        response = _report_left(client)

    assert response.json()["status"] == "departed"
    details = _left_tickets().get().details
    assert "Previously linked account" in details
    assert "not affected by this departure" in details
    assert "Registered user" not in details
    assert "App cleanup needed" not in details
    assert CLEANUP_LINE not in details
    cleanup.assert_not_called()
    assert not is_departed_member(rider)


@pytest.mark.django_db
def test_the_ticket_lists_cleanup_for_the_account_that_left(client, rider, synced_guild):
    with patch("apps.tickets.services._member_cleanup_lines", return_value=[CLEANUP_LINE]):
        _report_left(client)

    details = _left_tickets().get().details
    assert "- **Registered user:**" in details
    assert "Previously linked account" not in details
    assert "App cleanup needed" in details
    assert CLEANUP_LINE in details


@pytest.mark.django_db
def test_a_row_created_meanwhile_is_stamped_instead(rider):
    """A sync inserted the row between the lookup and the insert."""

    def racing_insert(discord_id, member_data):
        GuildMember.objects.create(discord_id=discord_id, username="from_sync", user=rider)
        raise IntegrityError("duplicate key value violates unique constraint")

    with patch.object(services, "_create_departed_member", side_effect=racing_insert):
        result = record_member_departure(RIDER_ID)

    assert result == {"status": "departed", "created": False, "ticket_created": True}
    row = _row()
    assert row.username == "from_sync"
    assert row.date_left is not None


@pytest.mark.django_db
def test_a_ticket_failure_still_records_the_departure(client, rider, synced_guild):
    with (
        patch("apps.tickets.services.create_member_left_ticket", side_effect=RuntimeError("boom")),
        patch.object(services, "logfire") as log,
    ):
        response = _report_left(client)

    assert response.json() == {"status": "departed", "created": False, "ticket_created": False}
    assert is_departed_member(rider)
    log.error.assert_called_once()


@pytest.mark.django_db
def test_the_departure_is_logged_with_ids_only(rider):
    with patch.object(services, "logfire") as log:
        record_member_departure(
            RIDER_ID, {"username": "secret_handle", "display_name": "Secret Name", "avatar_hash": "hash"}
        )
        record_member_departure(RIDER_ID)

    calls = log.info.call_args_list
    assert len(calls) == 2
    row = _row()
    assert calls[0].kwargs == {
        "source": "bot_event",
        "discord_id": RIDER_ID,
        "guild_member_id": row.pk,
        "user_id": rider.pk,
        "created": True,
        "ticket_created": True,
    }
    assert calls[1].kwargs == {
        "source": "bot_event",
        "discord_id": RIDER_ID,
        "guild_member_id": row.pk,
        "user_id": rider.pk,
        "stamp_moved": True,
    }
    for call in calls:
        for key, value in call.kwargs.items():
            assert "auth" not in key.lower()
            assert "auth" not in str(value).lower()
            assert "secret" not in str(value).lower()


# --- the effect on access -----------------------------------------------------------------


@pytest.mark.django_db
def test_the_rider_is_signed_out_on_the_next_request_after_the_report(client, rider, synced_guild):
    client.force_login(rider)
    assert client.get(reverse("team:links")).status_code == 200
    assert user_can_use_api(rider)

    response = _report_left(Client())

    assert response.status_code == 200
    page = client.get(reverse("team:links"))
    assert page.status_code == 302
    assert page["Location"].startswith(reverse("account_login"))
    assert not _is_signed_in(client)
    assert not user_can_use_api(rider)


@pytest.mark.django_db
def test_a_rider_unknown_to_the_platform_is_signed_out_too(client, rider):
    """No sync has listed them yet, so there was no row to stamp."""
    client.force_login(rider)
    assert client.get(reverse("team:links")).status_code == 200

    _report_left(Client())

    assert client.get(reverse("team:links")).status_code == 302
    assert not _is_signed_in(client)


@pytest.mark.django_db
def test_a_departed_staff_account_keeps_access(client, rider, synced_guild):
    rider.is_staff = True
    rider.save(update_fields=["is_staff"])
    client.force_login(rider)

    _report_left(Client())

    assert _row().date_left is not None
    assert client.get(reverse("team:links")).status_code == 200
    assert _is_signed_in(client)


# --- a list older than the stamp must not clear it ----------------------------------------


@pytest.mark.django_db
def test_a_rest_list_read_before_the_report_keeps_the_stamp(client, rider, synced_guild):
    observed_at = timezone.now()  # the fetch starts; the rider is still listed
    _report_left(client)  # then the rider leaves
    stamped = _row().date_left

    result = _rest_sync(synced_guild, observed_at=observed_at)

    assert result["rejoin_deferred"] == 1
    assert result["rejoined"] == 0
    assert result["left"] == 0
    assert _row().date_left == stamped
    assert is_departed_member(rider)


@pytest.mark.django_db
def test_a_rest_list_read_after_the_report_clears_the_stamp(client, rider, synced_guild):
    _report_left(client)
    _rest_sync(synced_guild, observed_at=_row().date_left)  # at the stamp: still kept
    assert is_departed_member(rider)

    result = _rest_sync(synced_guild)  # the rider is listed again, and the list is newer

    assert result["rejoined"] == 1
    assert result["rejoin_deferred"] == 0
    assert _row().date_left is None
    assert not is_departed_member(rider)


@pytest.mark.django_db
def test_a_stamp_landing_during_the_upsert_is_not_overwritten(rider, synced_guild):
    """The report arrives between the sync reading the row and saving it."""
    GuildMember.objects.filter(discord_id=RIDER_ID).update(user=None)
    real_release = services._release_user_link

    def report_mid_sync(user, *, keep_pk=None):
        record_member_departure(RIDER_ID)
        return real_release(user, keep_pk=keep_pk)

    with patch.object(services, "_release_user_link", side_effect=report_mid_sync):
        result = _rest_sync(synced_guild, observed_at=timezone.now())

    assert result["linked"] == 1
    row = _row()
    assert row.user_id == rider.pk
    assert row.date_left is not None
    assert is_departed_member(rider)


@pytest.mark.django_db
def test_a_bot_push_read_before_the_report_keeps_the_stamp(client, rider, synced_guild):
    observed_at = timezone.now()
    _report_left(client)

    response = _bot_push(client, synced_guild, observed_at=observed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

    assert response.status_code == 200
    body = response.json()
    assert body["rejoin_deferred"] == 1
    assert body["rejoined"] == 0
    assert is_departed_member(rider)


@pytest.mark.django_db
def test_a_second_leave_during_a_rest_sync_is_not_lost(client, rider, synced_guild):
    """Left, came back without signing in, then left again while a sync was running.

    The sync's list was read while the rider was back, so it still lists them. The second
    report moves the stamp past that list's time, so the list cannot clear it.
    """
    _report_left(client)  # first departure
    observed_at = timezone.now()  # the sync reads its list: the rider is back and listed
    response = _report_left(client)  # the rider leaves again before the sync applies its list

    result = _rest_sync(synced_guild, observed_at=observed_at)

    assert response.json() == {"status": "already_departed", "created": False, "ticket_created": False}
    assert result["rejoined"] == 0
    assert result["rejoin_deferred"] == 1
    assert _row().date_left is not None
    assert is_departed_member(rider)
    assert not user_can_use_api(rider)
    assert _left_tickets().count() == 1


@pytest.mark.django_db
def test_a_second_leave_during_a_bot_push_is_not_lost(client, rider, synced_guild):
    _report_left(client)
    observed_at = timezone.now()
    _report_left(client)

    response = _bot_push(client, synced_guild, observed_at=observed_at.isoformat())

    assert response.status_code == 200
    assert response.json()["rejoined"] == 0
    assert response.json()["rejoin_deferred"] == 1
    assert is_departed_member(rider)


@pytest.mark.django_db
def test_a_leave_reported_for_a_never_seen_row_makes_it_a_real_departure(client, rider):
    _rest_sync(OTHER_IDS)  # the rider is not listed: recorded as never seen
    row = _row()
    assert services.is_never_seen(row)
    assert row.user_id == rider.pk

    response = _report_left(client)

    assert response.json() == {"status": "already_departed", "created": False, "ticket_created": False}
    row = _row()
    assert row.date_left > row.date_created
    assert not services.is_never_seen(row)
    assert not GuildMember.objects.filter(services.never_seen_in_guild()).exists()
    assert is_departed_member(rider)
    assert not Ticket.objects.exists()


@pytest.mark.django_db
def test_a_bot_push_without_observed_at_still_brings_a_member_back(client, rider, synced_guild):
    _report_left(client)

    response = _bot_push(client, [RIDER_ID])

    assert response.json()["rejoined"] == 1
    assert response.json()["rejoin_deferred"] == 0
    assert not is_departed_member(rider)


@pytest.mark.django_db
@pytest.mark.parametrize("observed_at", ["not a time", 1726500000, "", None])
def test_a_bot_push_with_an_unusable_observed_at_uses_now(client, rider, synced_guild, observed_at):
    _report_left(client)

    response = _bot_push(client, [RIDER_ID], observed_at=observed_at)

    assert response.status_code == 200
    assert response.json()["rejoined"] == 1
    assert not is_departed_member(rider)


@pytest.mark.django_db
def test_a_bot_push_read_after_the_report_clears_the_stamp(client, rider, synced_guild):
    _report_left(client)
    later = _row().date_left + timedelta(microseconds=1)

    response = _bot_push(client, [RIDER_ID], observed_at=later.isoformat())

    assert response.json()["rejoined"] == 1
    assert not is_departed_member(rider)


def test_observed_at_is_read_as_utc_and_never_later_than_now():
    assert _push_observed_at("2026-01-02T03:04:05Z") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert _push_observed_at("2026-01-02T03:04:05") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert _push_observed_at("2026-01-02T05:04:05+02:00") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    before = timezone.now()
    clamped = _push_observed_at((before + timedelta(days=1)).isoformat())
    assert before <= clamped <= timezone.now()

    for unusable in (None, "", "garbage", 12, ["2026-01-02"]):
        got = _push_observed_at(unusable)
        assert before <= got <= timezone.now()


# --- the sync health report ---------------------------------------------------------------


def _sync_run(
    finished_at, *, status="SUCCESSFUL", return_value=None, task_path="apps.accounts.tasks.sync_guild_members"
):
    django_apps.get_model("django_tasks_database", "DBTaskResult").objects.create(
        task_path=task_path,
        status=status,
        finished_at=finished_at,
        run_after=finished_at,
        args_kwargs={"args": [], "kwargs": {}},
        queue_name="default",
        backend_name="default",
        priority=0,
        return_value={"status": "ok"} if return_value is None else return_value,
        exception_class_path="",
        traceback="",
    )


@pytest.mark.django_db
def test_the_health_report_times_the_sync_not_the_last_leave_report(client, rider, synced_guild):
    from apps.accounts.tasks import guild_member_sync_status

    now = timezone.now()
    completed = now - timedelta(hours=30)
    _sync_run(completed)
    _sync_run(now - timedelta(hours=2), status="FAILED", return_value={})  # refused
    _sync_run(now - timedelta(hours=1), return_value={"status": "skipped", "reason": "bot_token_not_configured"})
    _sync_run(now - timedelta(minutes=5), task_path="apps.accounts.tasks.guild_member_sync_status")
    _report_left(client)  # bumps date_modified to now

    report = guild_member_sync_status.call()

    assert report["last_sync"] == completed.isoformat()
    assert report["hours_since_sync"] == pytest.approx(30.0, abs=0.1)
    assert report["last_member_change"] == _row().date_modified.isoformat()


@pytest.mark.django_db
def test_the_health_report_shows_no_sync_when_none_has_completed(client, rider, synced_guild):
    from apps.accounts.tasks import guild_member_sync_status

    _report_left(client)

    report = guild_member_sync_status.call()

    assert report["last_sync"] is None
    assert report["hours_since_sync"] is None
    assert report["last_member_change"] is not None


# --- a live login still wins --------------------------------------------------------------


@pytest.mark.django_db
def test_a_discord_login_that_passes_the_live_check_clears_the_stamp(client, rider, synced_guild, discord_login):
    from allauth.socialaccount.models import SocialAccount

    rider.set_unusable_password()
    rider.save()
    SocialAccount.objects.create(user=rider, provider="discord", uid=RIDER_ID, extra_data={})
    _report_left(Client())
    assert is_departed_member(rider)

    discord_login(client, RIDER_ID)

    assert _is_signed_in(client)
    assert _row().date_left is None
    assert client.get(reverse("team:links")).status_code == 200
    assert _is_signed_in(client)

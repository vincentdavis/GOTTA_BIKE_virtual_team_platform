"""The guild sync now decides who is signed out, so it must not trust a short member list.

``GuildMember.date_left`` used to cost a member-left ticket. Since it became an access
decision (``apps.accounts.membership``), an empty or truncated member list -- a cold bot
cache, a pagination break -- would sign out every rider it left out and stop their API keys.
So:

- Only the paginated REST task's list (``authoritative=True``) decides who has left. The bot's
  push comes from its gateway cache, which can be partial after a restart, so it only refreshes
  the members it lists.
- ``apply_guild_member_sync`` refuses to stamp departures for an empty list, or for more than
  ``MASS_DEPARTURE_FLOOR`` rows and ``MASS_DEPARTURE_SHARE`` of the active ones at once, unless
  an admin confirms it.
- A row changed after the list was read (a rider who rejoined and signed in meanwhile) is left
  for the next run.
- A refusal opens one Membership ticket and fails the task run, so somebody sees it.

The other half: an account the sync has never listed used to stay row-less, and so allowed,
for good. A sync that passes the check now records each one as departed -- under its own
limit, measured against the Discord-linked accounts -- and those rows are labelled "Never seen
in the server" rather than passed off as departures.
"""

import csv
import io
from datetime import timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest
from constance.test import override_config
from django.apps import apps as django_apps
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services
from apps.accounts.membership import clear_departure, is_departed_member
from apps.accounts.models import GuildMember
from apps.accounts.services import (
    MASS_DEPARTURE_FLOOR,
    GuildSyncRefusedError,
    apply_guild_member_sync,
    fetch_guild_members_from_discord,
)
from apps.accounts.tasks import sync_guild_members
from apps.tickets.models import Ticket
from apps.tickets.services import SYNC_REFUSAL_TICKET_TITLE
from apps.user_api.services import user_can_use_api

TEAM_ROLE = "555000000000000003"
RIDER_ID = "730000000000000001"
API_KEY = "s3cret-bot-key"
BOT_GUILD_ID = 42
BOT_HEADERS = {"HTTP_X_API_KEY": API_KEY, "HTTP_X_GUILD_ID": str(BOT_GUILD_ID), "HTTP_X_DISCORD_USER_ID": "1"}


def _member(discord_id):
    """Build one normalized member payload entry.

    Returns:
        The dict apply_guild_member_sync expects.

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


def _guild(count, start=800000000000000000):
    """Build a guild's worth of member ids.

    Returns:
        ``count`` distinct Discord ids as strings.

    """
    return [str(start + n) for n in range(count)]


def _rest_sync(members, **kwargs):
    """Run the sync the way the REST task does: Discord's complete member list.

    Returns:
        The sync result.

    """
    return apply_guild_member_sync(members, source="discord_api", authoritative=True, **kwargs)


def _bot_push(client, members):
    """POST a member list to the bot's sync endpoint.

    Returns:
        The response.

    """
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=BOT_GUILD_ID):
        return client.post(
            "/api/dbot/sync_guild_members", {"members": members}, content_type="application/json", **BOT_HEADERS
        )


def _refusal_tickets():
    return Ticket.objects.filter(title=SYNC_REFUSAL_TICKET_TITLE)


def _member_left_tickets():
    return Ticket.objects.filter(guild_member__isnull=False)


@pytest.fixture(autouse=True)
def _team_role_grants_membership():
    with override_config(PERM_TEAM_MEMBER_ROLES=f'["{TEAM_ROLE}"]', PERM_ROLES_REQUIRED_USE_API="[]"):
        yield


@pytest.fixture
def rider(db, user_model):
    """Make a rider whose team_member access comes from a Discord role.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username="sync_rider",
        email="sync_rider@example.test",
        discord_id=RIDER_ID,
        discord_roles={TEAM_ROLE: "Team Member"},
    )


@pytest.fixture
def synced_guild(rider):
    """Sync a 40-member guild that includes the rider.

    Returns:
        The member ids, the rider's included.

    """
    ids = [RIDER_ID, *_guild(39)]
    _rest_sync([_member(i) for i in ids])
    return ids


@pytest.fixture
def outsiders(db, user_model):
    """Make Discord-linked accounts that no member list will contain.

    Returns:
        A function taking a count and returning that many new users.

    """

    def make(count, start=740000000000000000):
        return [
            user_model.objects.create_user(
                username=f"outsider{n}",
                email=f"outsider{n}@example.test",
                discord_id=str(start + n),
                discord_roles={TEAM_ROLE: "Team Member"},
            )
            for n in range(count)
        ]

    return make


def _active():
    return GuildMember.objects.filter(date_left__isnull=True).count()


# --- the sanity check ---------------------------------------------------------------------


@pytest.mark.django_db
def test_an_empty_member_list_signs_nobody_out(client, rider, synced_guild):
    client.force_login(rider)

    with patch("apps.accounts.services.logfire") as log:
        result = _rest_sync([])

    assert result["departures_refused"] == "empty_member_list"
    assert result["left"] == 0
    assert result["departures_skipped"] == len(synced_guild)
    assert _active() == len(synced_guild)
    assert not _member_left_tickets().exists()
    log.error.assert_called_once()
    assert client.get(reverse("team:links")).status_code == 200
    assert user_can_use_api(rider)


@pytest.mark.django_db
def test_an_empty_member_list_is_refused_even_when_confirmed(synced_guild):
    result = _rest_sync([], allow_mass_departure=True)

    assert result["departures_refused"] == "empty_member_list"
    assert _active() == len(synced_guild)


@pytest.mark.django_db
def test_a_truncated_member_list_signs_nobody_out(client, rider, synced_guild):
    client.force_login(rider)
    # A pagination break: only the first ten members made it into the list.
    kept = [i for i in synced_guild if i != RIDER_ID][:10]

    result = _rest_sync([_member(i) for i in kept])

    assert result["departures_refused"] == "mass_departure"
    assert result["left"] == 0
    assert result["departures_skipped"] == len(synced_guild) - len(kept)
    assert not GuildMember.objects.filter(date_left__isnull=False).exists()
    assert not _member_left_tickets().exists()
    assert not is_departed_member(rider)
    assert client.get(reverse("team:links")).status_code == 200


@pytest.mark.django_db
def test_a_refused_sync_still_refreshes_the_members_it_received(synced_guild):
    kept = synced_guild[:5]
    GuildMember.objects.filter(discord_id=kept[0]).update(username="stale")

    _rest_sync([_member(i) for i in kept])

    assert GuildMember.objects.get(discord_id=kept[0]).username == f"member{kept[0]}"


@pytest.mark.django_db
def test_an_ordinary_departure_is_still_stamped(client, rider, synced_guild):
    client.force_login(rider)
    others = [i for i in synced_guild if i != RIDER_ID]

    result = _rest_sync([_member(i) for i in others])

    assert result["departures_evaluated"] is True
    assert result["departures_refused"] == ""
    assert result["left"] == 1
    assert Ticket.objects.filter(guild_member__discord_id=RIDER_ID).count() == 1
    assert not _refusal_tickets().exists()
    assert client.get(reverse("team:links")).status_code == 302
    assert not user_can_use_api(rider)


@pytest.mark.django_db
def test_up_to_the_floor_leaves_at_once_in_a_small_guild(db):
    """The share alone would refuse any departure from a handful of members."""
    ids = _guild(MASS_DEPARTURE_FLOOR + 1)
    _rest_sync([_member(i) for i in ids])

    result = _rest_sync([_member(ids[0])])

    assert result["departures_refused"] == ""
    assert result["left"] == MASS_DEPARTURE_FLOOR


@pytest.mark.django_db
def test_one_past_the_floor_is_held_back(db):
    ids = _guild(MASS_DEPARTURE_FLOOR + 2)
    _rest_sync([_member(i) for i in ids])

    result = _rest_sync([_member(ids[0])])

    assert result["departures_refused"] == "mass_departure"
    assert result["left"] == 0


@pytest.mark.django_db
def test_a_confirmed_mass_departure_is_stamped(synced_guild):
    kept = synced_guild[:10]

    result = _rest_sync([_member(i) for i in kept], allow_mass_departure=True)

    assert result["departures_refused"] == ""
    assert result["left"] == len(synced_guild) - len(kept)
    assert _active() == len(kept)


# --- a rider who came back while the list was being read ----------------------------------


@pytest.fixture
def departed_rider(rider, synced_guild):
    """Mark the rider departed with an ordinary sync, and close the ticket it filed.

    Returns:
        The member ids without the rider.

    """
    others = [i for i in synced_guild if i != RIDER_ID]
    _rest_sync([_member(i) for i in others])
    assert is_departed_member(rider)
    Ticket.objects.update(status=Ticket.Status.CLOSED)
    return others


@pytest.mark.django_db
def test_a_rider_who_rejoined_during_the_fetch_is_not_signed_out_again(rider, departed_rider):
    """The list was read before they rejoined; their login's live check is newer."""
    observed_at = timezone.now()  # the fetch starts, after the earlier stamp
    assert clear_departure(RIDER_ID) == 1  # the Discord login during the fetch

    result = _rest_sync([_member(i) for i in departed_rider], observed_at=observed_at)

    assert result["left"] == 0
    assert result["departures_deferred"] == 1
    assert result["departures_refused"] == ""
    assert not is_departed_member(rider)
    assert not Ticket.objects.filter(status=Ticket.Status.NEW).exists()


@pytest.mark.django_db
def test_the_next_run_still_catches_a_rider_who_did_not_really_come_back(rider, departed_rider):
    observed_at = timezone.now()
    clear_departure(RIDER_ID)
    _rest_sync([_member(i) for i in departed_rider], observed_at=observed_at)

    result = _rest_sync([_member(i) for i in departed_rider])

    assert result["left"] == 1
    assert is_departed_member(rider)


@pytest.mark.django_db
def test_the_stamp_skips_a_row_touched_after_it_was_read(synced_guild):
    """The window between reading the departing rows and stamping each one."""
    observed_at = timezone.now()
    row = GuildMember.objects.get(discord_id=synced_guild[1])
    GuildMember.objects.filter(pk=row.pk).update(date_modified=observed_at + timedelta(seconds=1))

    assert services._stamp_departures([row], observed_at=observed_at) == 0
    row.refresh_from_db()
    assert row.date_left is None
    assert not _member_left_tickets().exists()


# --- the refusal is visible -----------------------------------------------------------------


@pytest.mark.django_db
def test_a_refusal_opens_one_ticket_and_refreshes_it(synced_guild):
    _rest_sync([_member(i) for i in synced_guild[:10]])
    first = _refusal_tickets().get()
    assert first.category == Ticket.Category.MEMBERSHIP
    assert first.priority == Ticket.Priority.HIGH
    assert first.status == Ticket.Status.NEW
    assert first.submitted_by is None
    assert "**Departures held back:** 30" in first.details

    _rest_sync([_member(i) for i in synced_guild[:5]])

    ticket = _refusal_tickets().get()
    assert ticket.pk == first.pk
    assert "**Departures held back:** 35" in ticket.details
    assert "held back:** 30" not in ticket.details


@pytest.mark.django_db
def test_a_refusal_ticket_in_progress_is_refreshed_not_duplicated(synced_guild):
    _rest_sync([_member(i) for i in synced_guild[:10]])
    _refusal_tickets().update(status=Ticket.Status.IN_PROGRESS, priority=Ticket.Priority.URGENT)

    _rest_sync([_member(i) for i in synced_guild[:10]])

    ticket = _refusal_tickets().get()
    assert ticket.status == Ticket.Status.IN_PROGRESS
    assert ticket.priority == Ticket.Priority.URGENT


@pytest.mark.django_db
def test_a_closed_refusal_ticket_is_not_reopened(synced_guild):
    _rest_sync([_member(i) for i in synced_guild[:10]])
    _refusal_tickets().update(status=Ticket.Status.CLOSED)

    _rest_sync([_member(i) for i in synced_guild[:10]])

    assert _refusal_tickets().count() == 2
    assert _refusal_tickets().filter(status=Ticket.Status.NEW).count() == 1


@pytest.mark.django_db
def test_the_refusal_ticket_says_how_to_confirm(synced_guild):
    _rest_sync([_member(i) for i in synced_guild[:10]])

    details = _refusal_tickets().get().details
    assert "Accept a mass departure" in details
    assert reverse("config_section_page", args=["background_tasks"]) in details
    assert "**Members in Discord's list:** 10" in details
    assert "**Active members before the sync:** 40" in details


@pytest.mark.django_db
def test_the_empty_list_ticket_does_not_offer_the_confirmation(synced_guild):
    _rest_sync([])

    details = _refusal_tickets().get().details
    assert "never acted on, even when confirmed" in details
    assert "tick **Accept a mass departure**" not in details


@pytest.mark.django_db
def test_a_ticket_failure_does_not_undo_the_sync(synced_guild):
    kept = synced_guild[:10]
    GuildMember.objects.filter(discord_id=kept[0]).update(username="stale")

    with patch("apps.tickets.services.open_sync_refusal_ticket", side_effect=RuntimeError("db down")):
        result = _rest_sync([_member(i) for i in kept])

    assert result["departures_refused"] == "mass_departure"
    assert GuildMember.objects.get(discord_id=kept[0]).username == f"member{kept[0]}"


# --- the drivers --------------------------------------------------------------------------


def _patched_fetch(members):
    return (
        override_config(DISCORD_BOT_TOKEN="bot-token", GUILD_ID=BOT_GUILD_ID),  # noqa: S106
        patch("apps.accounts.services.fetch_guild_members_from_discord", return_value=members),
    )


@pytest.mark.django_db
def test_the_task_stamps_departures_from_the_rest_list(rider, synced_guild):
    config_patch, fetch_patch = _patched_fetch([_member(i) for i in synced_guild if i != RIDER_ID])

    with config_patch, fetch_patch:
        result = sync_guild_members.call()

    assert result["status"] == "ok"
    assert result["left"] == 1
    assert is_departed_member(rider)


@pytest.mark.django_db
def test_the_task_fails_a_refused_run_and_can_be_confirmed(synced_guild):
    kept = synced_guild[:10]
    GuildMember.objects.filter(discord_id=kept[0]).update(username="stale")
    config_patch, fetch_patch = _patched_fetch([_member(i) for i in kept])

    with config_patch, fetch_patch:
        with pytest.raises(GuildSyncRefusedError, match="30 departure") as refused:
            sync_guild_members.call()
        assert "Accept a mass departure" in str(refused.value)
        assert _active() == len(synced_guild)
        assert GuildMember.objects.get(discord_id=kept[0]).username == f"member{kept[0]}"
        confirmed = sync_guild_members.call(allow_mass_departure=True)

    assert confirmed["status"] == "ok"
    assert _active() == len(kept)


@pytest.mark.django_db
def test_a_refused_run_is_recorded_as_failed_with_the_upserts_kept(synced_guild, admin_authed_client):
    """The worker's own bookkeeping, so the Run Now page shows the refusal."""
    from django_tasks_db.management.commands.db_worker import Worker

    newcomer = "800000000000009999"
    config_patch, fetch_patch = _patched_fetch([_member(i) for i in [*synced_guild[:10], newcomer]])
    queued = sync_guild_members.enqueue()
    results = django_apps.get_model("django_tasks_database", "DBTaskResult")
    worker = Worker(
        queue_names=["*"],
        interval=0,
        batch=True,
        backend_name="default",
        startup_delay=False,
        max_tasks=None,
        worker_id="test-worker",
        excluded_queue_names=[],
    )

    with config_patch, fetch_patch:
        worker.run_task(results.objects.get(id=queued.id))

    record = results.objects.get(id=queued.id)
    assert record.status == "FAILED"
    assert record.exception_class_path == "apps.accounts.services.GuildSyncRefusedError"
    assert GuildMember.objects.filter(discord_id=newcomer, date_left__isnull=True).exists()
    assert _active() == len(synced_guild) + 1
    assert _refusal_tickets().count() == 1

    page = admin_authed_client.get(reverse("config_section_page", args=["background_tasks"]), secure=True)
    body = page.content.decode()
    row = body[body.index(">sync_guild_members<") :]
    row = row[: row.index("</tr>")]
    assert ">Failed<" in row
    # The reason is on the page that holds the fix, not only in the ticket queue.
    assert "Last run held back sign-outs" in row
    assert "30 departure(s)" in row
    assert "Accept a mass departure" in row


@pytest.mark.django_db
def test_other_failures_show_no_exception_text(admin_authed_client):
    """Another task's exception can quote URLs or data, so only the refusal's own message shows."""
    results = django_apps.get_model("django_tasks_database", "DBTaskResult")
    results.objects.create(
        task_path="apps.accounts.tasks.sync_guild_members",
        args_kwargs={"args": [], "kwargs": {}},
        status="FAILED",
        finished_at=timezone.now(),
        run_after=timezone.now(),
        backend_name="default",
        queue_name="default",
        exception_class_path="httpx.HTTPStatusError",
        traceback="httpx.HTTPStatusError: Client error for url https://example.test/secret-channel\n",
    )

    page = admin_authed_client.get(reverse("config_section_page", args=["background_tasks"]), secure=True)
    body = page.content.decode()

    assert "secret-channel" not in body
    assert "Last run held back sign-outs" not in body


@pytest.mark.django_db
def test_the_run_now_page_passes_the_confirmation(admin_authed_client):
    from gotta_bike_platform.task_registry import TASK_REGISTRY

    task = MagicMock()
    with patch.dict(TASK_REGISTRY["sync_guild_members"], {"task": task}):
        admin_authed_client.post(
            reverse("config_trigger_task"), {"task_name": "sync_guild_members", "allow_mass_departure": "on"}
        )
        admin_authed_client.post(reverse("config_trigger_task"), {"task_name": "sync_guild_members"})

    assert [c.kwargs for c in task.enqueue.call_args_list] == [
        {"allow_mass_departure": True},
        {"allow_mass_departure": False},
    ]


# --- the bot push is not trusted with departures ------------------------------------------


@pytest.mark.django_db
def test_the_bot_push_cannot_empty_the_guild(client, synced_guild):
    response = _bot_push(client, [])

    assert response.status_code == 200
    body = response.json()
    assert body["departures_evaluated"] is False
    assert body["departures_refused"] == ""
    assert body["left"] == 0
    assert _active() == len(synced_guild)
    assert not Ticket.objects.exists()


@pytest.mark.django_db
def test_the_bot_push_never_stamps_a_departure(client, rider, synced_guild):
    """A cold cache that lost a chunk: small enough to pass the count check, so not checked at all."""
    client.force_login(rider)
    partial = [_member(i) for i in synced_guild if i != RIDER_ID]

    response = _bot_push(client, partial)

    assert response.json()["left"] == 0
    assert not is_departed_member(rider)
    assert not Ticket.objects.exists()
    assert client.get(reverse("team:links")).status_code == 200


@pytest.mark.django_db
def test_the_bot_push_records_no_unseen_account(client, rider):
    response = _bot_push(client, [_member(i) for i in _guild(3)])

    assert response.status_code == 200
    assert not GuildMember.objects.filter(discord_id=RIDER_ID).exists()
    assert not is_departed_member(rider)


@pytest.mark.django_db
def test_the_bot_push_still_brings_a_member_back(client, rider, departed_rider):
    response = _bot_push(client, [_member(RIDER_ID)])

    assert response.json()["rejoined"] == 1
    assert not is_departed_member(rider)


@pytest.mark.django_db
def test_the_bot_push_keeps_its_response_shape(client, synced_guild):
    response = _bot_push(client, [_member(i) for i in synced_guild])

    assert set(response.json()) == {
        "created",
        "updated",
        "rejoined",
        "left",
        "linked",
        "departures_evaluated",
        "departures_refused",
        "departures_skipped",
        "total_received",
        "total_active",
    }


@pytest.mark.django_db
def test_a_non_authoritative_sync_leaves_departures_alone(synced_guild):
    result = apply_guild_member_sync([_member(synced_guild[0])], source="bot_webhook")

    assert result["departures_evaluated"] is False
    assert result["left"] == 0
    assert result["unseen_recorded"] == 0
    assert _active() == len(synced_guild)


def _page(ids, *, drop_last_user=False):
    body = [{"user": {"id": i, "username": f"m{i}"}, "roles": [], "joined_at": None} for i in ids]
    if drop_last_user:
        body[-1].pop("user")
    return httpx.Response(200, json=body, request=httpx.Request("GET", "https://discord.test/"))


@pytest.mark.django_db
def test_a_full_page_without_a_cursor_fails_the_fetch():
    """Stopping there would hand the sync a list with the rest of the guild missing."""
    client = MagicMock()
    client.__enter__.return_value.get.return_value = _page(["1", "2"], drop_last_user=True)

    with (
        patch.object(services, "GUILD_MEMBER_PAGE_SIZE", 2),
        patch("apps.accounts.services.httpx.Client", return_value=client),
        patch("apps.accounts.services.logfire"),
        pytest.raises(ValueError, match="without a user id"),
    ):
        fetch_guild_members_from_discord(1, "bot-token")


@pytest.mark.django_db
def test_a_short_last_page_ends_the_fetch_normally():
    client = MagicMock()
    client.__enter__.return_value.get.side_effect = [_page(["1", "2"]), _page(["3"])]

    with (
        patch.object(services, "GUILD_MEMBER_PAGE_SIZE", 2),
        patch("apps.accounts.services.httpx.Client", return_value=client),
    ):
        members = fetch_guild_members_from_discord(1, "bot-token")

    assert [m["discord_id"] for m in members] == ["1", "2", "3"]


# --- accounts no sync has ever listed -----------------------------------------------------


@pytest.mark.django_db
def test_a_rider_who_left_before_any_sync_saw_them_is_signed_out(client, rider):
    """Signed in, left the server, and never appeared in a member list: no row, until now."""
    client.force_login(rider)
    assert not GuildMember.objects.filter(discord_id=RIDER_ID).exists()
    assert client.get(reverse("team:links")).status_code == 200

    result = _rest_sync([_member(i) for i in _guild(20)])

    assert result["unseen_recorded"] == 1
    row = GuildMember.objects.get(discord_id=RIDER_ID)
    assert row.date_left is not None
    assert row.user == rider
    assert is_departed_member(rider)
    assert not user_can_use_api(rider)
    assert client.get(reverse("team:links")).status_code == 302
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_an_unseen_account_files_no_ticket(rider):
    _rest_sync([_member(i) for i in _guild(3)])

    assert not Ticket.objects.exists()


@pytest.mark.django_db
def test_a_listed_rider_is_not_recorded_as_unseen(rider):
    result = _rest_sync([_member(RIDER_ID)])

    assert result["unseen_recorded"] == 0
    assert GuildMember.objects.get(discord_id=RIDER_ID).date_left is None


@pytest.mark.django_db
def test_recording_is_idempotent(rider):
    _rest_sync([_member(i) for i in _guild(3)])
    result = _rest_sync([_member(i) for i in _guild(3)])

    assert result["unseen_recorded"] == 0
    assert GuildMember.objects.filter(discord_id=RIDER_ID).count() == 1


@pytest.mark.django_db
def test_a_refused_sync_records_nobody(rider):
    result = _rest_sync([], allow_mass_departure=True)

    assert result["unseen_recorded"] == 0
    assert not GuildMember.objects.exists()


@pytest.mark.django_db
def test_a_rider_who_signed_in_after_the_list_was_read_is_left_alone(rider):
    """Their login's live guild check is newer than the list."""
    observed_at = timezone.now() - timedelta(minutes=1)
    rider.last_login = timezone.now()
    rider.save(update_fields=["last_login"])

    result = _rest_sync([_member(i) for i in _guild(3)], observed_at=observed_at)

    assert result["unseen_recorded"] == 0
    assert not is_departed_member(rider)


@pytest.mark.django_db
def test_an_account_created_after_the_list_was_read_is_left_alone(rider):
    observed_at = rider.date_joined - timedelta(seconds=1)

    result = _rest_sync([_member(i) for i in _guild(3)], observed_at=observed_at)

    assert result["unseen_recorded"] == 0


@pytest.mark.django_db
def test_a_rider_who_signed_in_before_the_list_was_read_is_recorded(rider):
    rider.last_login = timezone.now() - timedelta(hours=1)
    rider.save(update_fields=["last_login"])

    result = _rest_sync([_member(i) for i in _guild(3)])

    assert result["unseen_recorded"] == 1


@pytest.mark.django_db
def test_a_local_account_is_never_recorded(user_model):
    user_model.objects.create_user(username="local", email="local@example.test")

    result = _rest_sync([_member(i) for i in _guild(3)])

    assert result["unseen_recorded"] == 0
    assert GuildMember.objects.count() == 3


@pytest.mark.django_db
def test_an_existing_link_to_an_old_account_is_kept(rider):
    """The rider moved to a new Discord account; the old row keeps the link, the new one is unlinked."""
    old = GuildMember.objects.create(discord_id="730000000000000999", username="old", user=rider)

    _rest_sync([_member(old.discord_id), *[_member(i) for i in _guild(3)]])

    old.refresh_from_db()
    assert old.user == rider
    new = GuildMember.objects.get(discord_id=RIDER_ID)
    assert new.date_left is not None
    assert new.user is None
    assert is_departed_member(rider)


@pytest.mark.django_db
def test_a_shared_discord_id_gets_one_unlinked_row(rider, user_model):
    twin = user_model.objects.create_user(username="twin", email="twin@example.test", discord_id=RIDER_ID)

    result = _rest_sync([_member(i) for i in _guild(3)])

    assert result["unseen_recorded"] == 1
    row = GuildMember.objects.get(discord_id=RIDER_ID)
    assert row.user is None
    assert is_departed_member(rider)
    assert is_departed_member(twin)


@pytest.mark.django_db
def test_the_record_is_logged_with_ids_only(rider):
    with patch("apps.accounts.services.logfire") as log:
        _rest_sync([_member(i) for i in _guild(3)])

    recorded = next(c for c in log.info.call_args_list if "no guild sync has listed" in c.args[0])
    assert recorded.kwargs == {"count": 1, "user_ids": [rider.pk]}


@pytest.mark.django_db
def test_a_recorded_rider_who_is_back_is_cleared_by_the_next_sync(rider):
    _rest_sync([_member(i) for i in _guild(3)])
    assert is_departed_member(rider)

    result = _rest_sync([_member(RIDER_ID), *[_member(i) for i in _guild(3)]])

    assert result["rejoined"] == 1
    assert not is_departed_member(rider)


# --- the unseen sweep has its own limit ---------------------------------------------------


@pytest.mark.django_db
def test_the_unseen_sweep_is_held_back_past_the_limit(client, outsiders):
    """An empty GuildMember table passes the departure check with nothing departing."""
    accounts = outsiders(MASS_DEPARTURE_FLOOR + 1)
    client.force_login(accounts[0])

    with patch("apps.accounts.services.logfire") as log:
        result = _rest_sync([_member(i) for i in _guild(1)])

    assert result["departures_refused"] == ""
    assert result["unseen_refused"] == "mass_departure"
    assert result["unseen_skipped"] == len(accounts)
    assert result["unseen_recorded"] == 0
    assert GuildMember.objects.count() == 1
    assert not any(is_departed_member(a) for a in accounts)
    assert client.get(reverse("team:links")).status_code == 200
    log.error.assert_called_once()
    details = _refusal_tickets().get().details
    assert f"**Accounts never seen in the server, held back:** {len(accounts)}" in details


@pytest.mark.django_db
def test_the_unseen_sweep_up_to_the_floor_goes_ahead(outsiders):
    accounts = outsiders(MASS_DEPARTURE_FLOOR)

    result = _rest_sync([_member(i) for i in _guild(1)])

    assert result["unseen_refused"] == ""
    assert result["unseen_recorded"] == MASS_DEPARTURE_FLOOR
    assert all(is_departed_member(a) for a in accounts)
    assert not _refusal_tickets().exists()


@pytest.mark.django_db
def test_the_unseen_limit_counts_discord_linked_accounts(outsiders, user_model):
    """Twelve of 300 linked accounts is within the 5% share, however few rows there are."""
    outsiders(12)
    user_model.objects.bulk_create(
        user_model(username=f"member{n}", email=f"m{n}@example.test", discord_id=str(760000000000000000 + n))
        for n in range(288)
    )
    listed = [str(760000000000000000 + n) for n in range(288)]

    result = _rest_sync([_member(i) for i in listed])

    assert result["unseen_refused"] == ""
    assert result["unseen_recorded"] == 12


@pytest.mark.django_db
def test_a_confirmed_unseen_sweep_goes_past_the_limit(outsiders):
    accounts = outsiders(MASS_DEPARTURE_FLOOR + 1)

    result = _rest_sync([_member(i) for i in _guild(1)], allow_mass_departure=True)

    assert result["unseen_refused"] == ""
    assert result["unseen_recorded"] == len(accounts)


@pytest.mark.django_db
def test_the_unseen_sweep_never_runs_on_an_empty_list(outsiders):
    outsiders(3)

    result = _rest_sync([], allow_mass_departure=True)

    assert result["departures_refused"] == "empty_member_list"
    assert result["unseen_recorded"] == 0
    assert not GuildMember.objects.exists()


@pytest.mark.django_db
def test_a_refused_unseen_sweep_fails_the_task(outsiders):
    outsiders(MASS_DEPARTURE_FLOOR + 1)
    config_patch, fetch_patch = _patched_fetch([_member(i) for i in _guild(1)])

    with config_patch, fetch_patch, pytest.raises(GuildSyncRefusedError, match="no sync has ever listed"):
        sync_guild_members.call()

    assert GuildMember.objects.count() == 1


# --- never-seen rows are labelled, not passed off as departures ----------------------------


@pytest.fixture
def mixed_rows(rider, user_model):
    """Build one real departure, one never-seen account and one active member.

    Returns:
        ``(departed_id, never_seen_id, active_id)``.

    """
    departed_id, active_id = _guild(2)
    GuildMember.objects.create(discord_id=departed_id, username="gone")
    user_model.objects.create_user(username="gone_user", email="gone@example.test", discord_id=departed_id)
    GuildMember.objects.filter(discord_id=departed_id).update(date_modified=timezone.now() - timedelta(days=1))
    _rest_sync([_member(active_id)])
    return departed_id, RIDER_ID, active_id


@pytest.mark.django_db
def test_the_marker_tells_the_two_departed_rows_apart(mixed_rows):
    departed_id, never_seen_id, active_id = mixed_rows

    flags = dict(services.annotate_never_seen(GuildMember.objects.all()).values_list("discord_id", "never_seen"))

    assert flags == {departed_id: False, never_seen_id: True, active_id: False}


@pytest.mark.django_db
def test_a_never_seen_rider_who_comes_back_and_leaves_is_a_real_departure(rider):
    _rest_sync([_member(i) for i in _guild(3)])
    _rest_sync([_member(RIDER_ID), *[_member(i) for i in _guild(3)]])
    GuildMember.objects.filter(discord_id=RIDER_ID).update(date_modified=timezone.now() - timedelta(days=1))

    _rest_sync([_member(i) for i in _guild(3)])

    row = services.annotate_never_seen(GuildMember.objects.filter(discord_id=RIDER_ID)).get()
    assert row.date_left is not None
    assert row.never_seen is False


@pytest.mark.django_db
def test_discord_review_labels_never_seen_rows(client, membership_admin, mixed_rows):
    departed_id, never_seen_id, _ = mixed_rows
    client.force_login(membership_admin)
    url = reverse("team:discord_review")

    page = client.get(url, secure=True)
    left = client.get(url, {"left_status": "left"}, secure=True)
    never = client.get(url, {"left_status": "never_seen"}, secure=True)

    assert page.status_code == 200
    assert page.content.decode().count(">Never seen in the server</span>") == 1
    assert [m.discord_id for m in left.context["members"]] == [departed_id]
    assert [m.discord_id for m in never.context["members"]] == [never_seen_id]


@pytest.mark.django_db
def test_discord_review_csv_labels_never_seen_rows(client, membership_admin, mixed_rows):
    departed_id, never_seen_id, active_id = mixed_rows
    client.force_login(membership_admin)

    body = client.get(reverse("team:discord_review_export"), secure=True).content.decode()

    status = {row["Discord ID"]: row["Status"] for row in csv.DictReader(io.StringIO(body))}
    assert status == {departed_id: "Left", never_seen_id: "Never seen in the server", active_id: "Active"}


@pytest.mark.django_db
def test_the_guild_member_admin_list_and_csv_label_never_seen_rows(client, superuser, mixed_rows):
    departed_id, never_seen_id, active_id = mixed_rows
    client.force_login(superuser)

    listing = client.get(reverse("admin:accounts_guildmember_changelist"), secure=True)
    ids = list(GuildMember.objects.filter(discord_id__in=mixed_rows).values_list("pk", flat=True))
    export = client.post(
        reverse("admin:accounts_guildmember_changelist"),
        {"action": "export_csv", "_selected_action": ids},
        secure=True,
    )

    assert listing.status_code == 200
    assert "Never seen in the server" in listing.content.decode()
    status = {row["Discord ID"]: row["Status"] for row in csv.DictReader(io.StringIO(export.content.decode()))}
    assert status == {departed_id: "Left", never_seen_id: "Never seen in the server", active_id: "Active"}


@pytest.mark.django_db
def test_the_sync_health_report_does_not_count_never_seen_rows_as_left(mixed_rows):
    from apps.accounts.tasks import guild_member_sync_status

    report = guild_member_sync_status.call()

    assert report["left_members"] == 1
    assert report["never_seen_members"] == 1


@pytest.mark.django_db
def test_the_admin_comparison_matches_departed_accounts_by_discord_id(client, superuser, rider, user_model):
    """Unlinked departed rows used to fall out of every category."""
    shared_id, departed_id, active_id = _guild(3, start=770000000000000000)
    user_model.objects.create_user(username="twin_a", email="ta@example.test", discord_id=shared_id)
    user_model.objects.create_user(username="twin_b", email="tb@example.test", discord_id=shared_id)
    GuildMember.objects.create(discord_id=departed_id, username="gone")
    user_model.objects.create_user(username="gone_user", email="gone@example.test", discord_id=departed_id)
    GuildMember.objects.filter(discord_id=departed_id).update(date_modified=timezone.now() - timedelta(days=1))
    _rest_sync([_member(active_id)])
    assert GuildMember.objects.get(discord_id=departed_id).user is None
    client.force_login(superuser)
    url = reverse("admin:accounts_guildmember_comparison")

    summary = client.get(url, secure=True)
    left = client.get(url, {"status": "left"}, secure=True)
    never = client.get(url, {"status": "never_seen"}, secure=True)

    assert summary.context["left_count"] == 1
    assert summary.context["never_seen_count"] == 2
    assert summary.context["discord_users_no_guild_count"] == 0
    assert [m.discord_id for m in left.context["display_members"]] == [departed_id]
    assert {m.discord_id for m in never.context["display_members"]} == {shared_id, RIDER_ID}
    left_body = left.content.decode()
    assert "gone_user" in left_body
    assert '<span style="color: red;">Left ' in left_body
    never_body = never.content.decode()
    assert "Never seen in the server (recorded" in never_body
    assert "(and 1 more sharing this Discord ID; not linked)" in never_body

"""Per-event timezone/region Discord roles granted from a rider's signup selection."""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad, SquadMember
from apps.events.timezone_roles import (
    is_enabled,
    mapped_roles,
    parse_role_map,
    role_columns,
    roles_for_selection,
    roles_to_drop,
)

OPTIONS = ["US EAST", "US WEST", "EMEA West"]
ROLE_MAP = {"US EAST": "111", "EMEA West": "333"}


@pytest.fixture
def event(db) -> Event:
    """Build an event with region options mapped to roles.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today - timedelta(days=1), end_date=today + timedelta(days=7),
        visible=True, signups_open=True, timezone_options=list(OPTIONS),
        timezone_role_map=dict(ROLE_MAP),
    )


@pytest.mark.django_db
def test_an_empty_map_means_the_feature_is_off(event) -> None:
    """Opt-in per event with no extra flag."""
    event.timezone_role_map = {}

    assert mapped_roles(event) == {}
    assert is_enabled(event) is False
    assert roles_for_selection(event, ["US EAST"]) == []


@pytest.mark.django_db
def test_only_options_that_still_exist_are_honoured(event) -> None:
    """An admin can rename or delete an option after riders signed up."""
    event.timezone_role_map = {**ROLE_MAP, "APAC": "999"}  # option since removed

    assert "APAC" not in mapped_roles(event)
    assert roles_for_selection(event, ["APAC"]) == []


@pytest.mark.django_db
def test_blank_and_zero_role_ids_are_dropped(event) -> None:
    event.timezone_role_map = {"US EAST": "", "US WEST": "0", "EMEA West": "  333  "}

    assert mapped_roles(event) == {"EMEA West": "333"}


@pytest.mark.django_db
def test_selecting_two_regions_earns_both_roles(event) -> None:
    """Picking two regions is a claim to race in both."""
    assert roles_for_selection(event, ["US EAST", "EMEA West"]) == ["111", "333"]


@pytest.mark.django_db
def test_unmapped_and_unknown_selections_earn_nothing(event) -> None:
    assert roles_for_selection(event, ["US WEST"]) == []  # option exists, no role mapped
    assert roles_for_selection(event, ["ATLANTIS"]) == []
    assert roles_for_selection(event, None) == []


@pytest.mark.django_db
def test_a_role_kept_by_another_option_is_never_dropped(event) -> None:
    """Two labels can point at one role; deselecting one must not revoke it."""
    event.timezone_role_map = {"US EAST": "111", "US WEST": "111"}

    assert roles_to_drop(event, before=["US EAST", "US WEST"], after=["US WEST"]) == []
    assert roles_to_drop(event, before=["US EAST", "US WEST"], after=[]) == ["111"]


@pytest.mark.django_db
def test_role_columns_follow_option_order(event) -> None:
    cols = role_columns(event, {"111": "$US-East", "333": "$EMEA-West"})

    assert [c["option"] for c in cols] == ["US EAST", "EMEA West"]
    assert cols[0]["name"] == "$US-East"


@pytest.mark.django_db
def test_parse_role_map_ignores_options_the_event_does_not_have(event) -> None:
    """A crafted POST cannot resurrect a removed option."""
    post = {
        "tz_role_map__US EAST": "111",
        "tz_role_map__US WEST": "  ",       # blank -> omitted
        "tz_role_map__EMEA West": "0",      # "(none)" -> omitted
        "tz_role_map__APAC": "999",         # not an option -> ignored
    }

    assert parse_role_map(post, OPTIONS) == {"US EAST": "111"}


# --- signup flow -------------------------------------------------------------------


@pytest.fixture
def rider(user_model):
    """Build a rider with Discord linked.

    Returns:
        The rider user.

    """
    return user_model.objects.create_user(
        username="rider", email="rider@example.test", discord_id="900",
        permission_overrides={"team_member": True},
    )


@pytest.fixture
def discord(monkeypatch):
    """Record Discord role add/remove calls instead of making them.

    Returns:
        A dict with "added" and "removed" role-id lists.

    """
    calls = {"added": [], "removed": []}
    monkeypatch.setattr("apps.events.views.add_discord_role",
                        lambda did, rid: calls["added"].append(rid) or True)
    monkeypatch.setattr("apps.events.views.remove_discord_role",
                        lambda did, rid: calls["removed"].append(rid) or True)
    return calls


@pytest.mark.django_db
def test_signup_grants_the_roles_for_the_picked_regions(client, event, rider, discord) -> None:
    client.force_login(rider)

    client.post(reverse("events:event_signup", args=[event.pk]),
                {"signup_timezone": ["US EAST", "EMEA West"]})

    assert discord["added"] == ["111", "333"]
    rider.refresh_from_db()
    assert "111" in rider.discord_roles


@pytest.mark.django_db
def test_signup_grants_nothing_when_the_event_has_no_map(client, event, rider, discord) -> None:
    event.timezone_role_map = {}
    event.save(update_fields=["timezone_role_map"])
    client.force_login(rider)

    client.post(reverse("events:event_signup", args=[event.pk]), {"signup_timezone": ["US EAST"]})

    assert discord["added"] == []


@pytest.mark.django_db
def test_editing_a_signup_swaps_the_roles(client, event, rider, discord) -> None:
    client.force_login(rider)
    client.post(reverse("events:event_signup", args=[event.pk]), {"signup_timezone": ["US EAST"]})
    discord["added"].clear()

    client.post(reverse("events:event_signup_edit", args=[event.pk]), {"signup_timezone": ["EMEA West"]})

    assert discord["added"] == ["333"]
    assert discord["removed"] == ["111"]


@pytest.mark.django_db
def test_a_squad_region_role_is_never_revoked_by_a_signup_edit(client, event, rider, discord) -> None:
    """A timezone role and a squad region role can be the same Discord role."""
    squad = Squad.objects.create(event=event, name="Squad A", region_role=111)
    SquadMember.objects.create(squad=squad, user=rider, status=SquadMember.Status.MEMBER)
    client.force_login(rider)
    client.post(reverse("events:event_signup", args=[event.pk]), {"signup_timezone": ["US EAST"]})

    client.post(reverse("events:event_signup_edit", args=[event.pk]), {"signup_timezone": ["EMEA West"]})

    assert discord["removed"] == []


@pytest.mark.django_db
def test_another_events_map_keeps_the_role(client, event, rider, discord, user_model) -> None:
    """The rider still earns the role through a signup they kept elsewhere."""
    today = date.today()
    other = Event.objects.create(
        title="Other", start_date=today, end_date=today + timedelta(days=7), visible=True,
        signups_open=True, timezone_options=list(OPTIONS), timezone_role_map={"US EAST": "111"},
    )
    EventSignup.objects.create(event=other, user=rider, status=EventSignup.Status.REGISTERED,
                               signup_timezone=["US EAST"])
    client.force_login(rider)
    client.post(reverse("events:event_signup", args=[event.pk]), {"signup_timezone": ["US EAST"]})

    client.post(reverse("events:event_signup_edit", args=[event.pk]), {"signup_timezone": []})

    assert discord["removed"] == []


@pytest.mark.django_db
def test_withdrawing_does_not_strip_the_role(client, event, rider, discord) -> None:
    """Matches the existing convention for event_role and team_discord_role."""
    client.force_login(rider)
    client.post(reverse("events:event_signup", args=[event.pk]), {"signup_timezone": ["US EAST"]})

    signup = EventSignup.objects.get(event=event, user=rider)
    client.post(reverse("events:event_signup_withdraw", args=[event.pk, signup.pk]))

    assert discord["removed"] == []


# --- manage roles page -------------------------------------------------------------


@pytest.mark.django_db
def test_manage_roles_shows_a_region_column_set(client, event, rider, user_model) -> None:
    EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED,
                               signup_timezone=["US EAST"])
    admin = user_model.objects.create_user(
        username="ra", email="ra@example.test",
        permission_overrides={"team_member": True, "event_admin": True, "assign_roles": True},
    )
    client.force_login(admin)

    resp = client.get(reverse("events:manage_roles", args=[event.pk]))
    body = resp.content.decode()

    assert [c["option"] for c in resp.context["timezone_roles"]] == ["US EAST", "EMEA West"]
    assert "Region columns" in body      # the optional column-set checkbox
    assert "tz-col" in body
    assert "data-tz-toggle" in body


@pytest.mark.django_db
def test_manage_roles_marks_regions_the_rider_did_not_pick(client, event, rider, user_model) -> None:
    """Lets an admin see role granted vs. stated availability at a glance."""
    EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED,
                               signup_timezone=["US EAST"])
    admin = user_model.objects.create_user(
        username="ra2", email="ra2@example.test",
        permission_overrides={"team_member": True, "event_admin": True, "assign_roles": True},
    )
    client.force_login(admin)

    resp = client.get(reverse("events:manage_roles", args=[event.pk]))
    statuses = {s["option"]: s for s in resp.context["enriched_signups"][0]["timezone_role_status"]}

    assert statuses["US EAST"]["selected"] is True
    assert statuses["EMEA West"]["selected"] is False


@pytest.mark.django_db
def test_no_region_columns_when_the_event_has_no_map(client, event, rider, user_model) -> None:
    event.timezone_role_map = {}
    event.save(update_fields=["timezone_role_map"])
    EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED)
    admin = user_model.objects.create_user(
        username="ra3", email="ra3@example.test",
        permission_overrides={"team_member": True, "event_admin": True, "assign_roles": True},
    )
    client.force_login(admin)

    body = client.get(reverse("events:manage_roles", args=[event.pk])).content.decode()

    assert "Region columns" not in body


@pytest.mark.django_db
def test_the_toggle_endpoint_accepts_a_mapped_region_role(client, event, rider, user_model, discord) -> None:
    """The shared event-role toggle now covers coordinator AND region roles."""
    EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED)
    admin = user_model.objects.create_user(
        username="ra4", email="ra4@example.test",
        permission_overrides={"team_member": True, "event_admin": True, "assign_roles": True},
    )
    client.force_login(admin)

    client.post(reverse("events:event_toggle_coordinator_role", args=[event.pk, rider.pk, 111]))

    assert discord["added"] == ["111"]


@pytest.mark.django_db
def test_the_toggle_endpoint_still_rejects_an_unmanaged_role(client, event, rider, user_model, discord) -> None:
    EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED)
    admin = user_model.objects.create_user(
        username="ra5", email="ra5@example.test",
        permission_overrides={"team_member": True, "event_admin": True, "assign_roles": True},
    )
    client.force_login(admin)

    client.post(reverse("events:event_toggle_coordinator_role", args=[event.pk, rider.pk, 424242]))

    assert discord["added"] == []


@pytest.mark.django_db
def test_event_edit_page_offers_a_select_per_saved_option(client, event, user_model) -> None:
    """Rendered from the saved options, so an admin adds a region, saves, then maps it."""
    from apps.team.models import DiscordRole

    DiscordRole.objects.create(role_id="111", name="$US-East", position=1)
    admin = user_model.objects.create_user(
        username="ea", email="ea@example.test",
        permission_overrides={"team_member": True, "event_admin": True},
    )
    client.force_login(admin)

    resp = client.get(reverse("events:event_edit", args=[event.pk]))
    body = resp.content.decode()

    assert [r["option"] for r in resp.context["timezone_role_rows"]] == OPTIONS
    assert 'name="tz_role_map__US EAST"' in body
    assert "Regional roles on signup" in body


@pytest.mark.django_db
def test_saving_the_event_persists_the_role_map(client, event, user_model) -> None:
    admin = user_model.objects.create_user(
        username="ea2", email="ea2@example.test",
        permission_overrides={"team_member": True, "event_admin": True},
    )
    client.force_login(admin)

    client.post(reverse("events:event_edit", args=[event.pk]), {
        "title": event.title,
        "start_date": event.start_date.isoformat(),
        "end_date": event.end_date.isoformat(),
        "timezone_options": '["US EAST", "US WEST", "EMEA West"]',
        "tz_role_map__US EAST": "111",
        "tz_role_map__US WEST": "0",
        "tz_role_map__EMEA West": "333",
    })

    event.refresh_from_db()
    assert event.timezone_role_map == {"US EAST": "111", "EMEA West": "333"}


@pytest.mark.django_db
def test_removing_an_option_drops_its_mapping_on_save(client, event, user_model) -> None:
    """The map is parsed against the options just saved, so it cannot keep a dead key."""
    admin = user_model.objects.create_user(
        username="ea3", email="ea3@example.test",
        permission_overrides={"team_member": True, "event_admin": True},
    )
    client.force_login(admin)

    client.post(reverse("events:event_edit", args=[event.pk]), {
        "title": event.title,
        "start_date": event.start_date.isoformat(),
        "end_date": event.end_date.isoformat(),
        "timezone_options": '["US EAST"]',            # EMEA West removed
        "tz_role_map__US EAST": "111",
        "tz_role_map__EMEA West": "333",              # ignored: no longer an option
    })

    event.refresh_from_db()
    assert event.timezone_role_map == {"US EAST": "111"}

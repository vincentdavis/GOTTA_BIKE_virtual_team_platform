"""My Events page: member-count icon, icon-only channel links, and the manage-squads gear."""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import (
    AvailabilityGrid,
    AvailabilitySlotSelection,
    Event,
    EventSignup,
    Squad,
    SquadMember,
)

MEMBER_ICON_PATH = "M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z"
DISCORD_MARK_PATH_START = "M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515"


@pytest.fixture
def event(db) -> Event:
    """Build a currently-running visible event.

    Returns:
        An event whose date range includes today.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=7),
        visible=True,
    )


def _join(event: Event, squad: Squad, user) -> None:
    """Sign a user up to an event and put them in one of its squads."""
    EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)


@pytest.mark.django_db
def test_member_count_badge_carries_a_person_icon(client, event, team_member) -> None:
    """The squad member count is prefixed with a person icon so it reads as a head count."""
    squad = Squad.objects.create(event=event, name="Squad A")
    _join(event, squad, team_member)
    client.force_login(team_member)

    body = client.get(reverse("events:my_events")).content.decode()

    assert MEMBER_ICON_PATH in body
    assert 'title="1 member"' in body


def _scheduled_race(squad: Squad, user, **selection_kwargs) -> AvailabilitySlotSelection:
    """Give a squad one published grid with a single upcoming race the user is selected for.

    Returns:
        The slot selection.

    """
    today = date.today()
    grid = AvailabilityGrid.objects.create(
        squad=squad, title="Week 1", start_date=today, end_date=today + timedelta(days=6),
        start_time="16:00", end_time="22:00", slot_duration=30,
        status=AvailabilityGrid.Status.PUBLISHED, grid_timezone="UTC",
    )
    selection = AvailabilitySlotSelection.objects.create(
        grid=grid, name="Race 1", slot_date=today + timedelta(days=2), slot_time="18:30",
        **selection_kwargs,
    )
    selection.selected_users.add(user)
    return selection


def _grid_pk(squad: Squad):
    """Return the pk of the squad's only availability grid.

    Returns:
        The grid's UUID primary key.

    """
    return AvailabilityGrid.objects.get(squad=squad).pk


@pytest.mark.django_db
def test_scheduled_race_rider_count_uses_the_same_person_icon(client, event, team_member) -> None:
    """A scheduled race shows the person icon plus a bare count, matching the squad badge."""
    squad = Squad.objects.create(event=event, name="Squad A")
    _join(event, squad, team_member)
    _scheduled_race(squad, team_member)
    client.force_login(team_member)

    body = client.get(reverse("events:my_events")).content.decode()

    # Two person icons now: one for the squad head count, one for the race rider count.
    assert body.count(MEMBER_ICON_PATH) == 2
    assert 'title="1 rider"' in body
    assert not re.search(r">\s*1 rider\s*<", body)


@pytest.mark.django_db
def test_race_status_badge_drops_below_the_title_on_phones(client, event, team_member) -> None:
    """Stacking under sm gives a long race name the full card width instead of two thirds."""
    squad = Squad.objects.create(event=event, name="Squad A")
    _join(event, squad, team_member)
    _scheduled_race(squad, team_member, status=AvailabilitySlotSelection.Status.CONFIRMED)
    client.force_login(team_member)

    body = client.get(reverse("events:my_events")).content.decode()

    assert "flex flex-col items-start gap-2 sm:flex-row sm:justify-between" in body
    # The old always-inline row is gone.
    assert "flex items-start justify-between gap-2" not in body


@pytest.mark.django_db
def test_calendar_links_are_icon_buttons_with_tooltips(client, event, team_member) -> None:
    """The two calendar actions become icons so the link row stays short on a phone."""
    squad = Squad.objects.create(event=event, name="Squad A")
    _join(event, squad, team_member)
    _scheduled_race(squad, team_member, thread_link="https://example.test/thread")
    client.force_login(team_member)

    body = client.get(reverse("events:my_events")).content.decode()

    for tip in ("Add to Google Calendar", "Download .ics"):
        assert f'data-tip="{tip}"' in body
        assert f'aria-label="{tip}"' in body
    assert body.count("btn-xs btn-ghost btn-square") == 2
    # The emoji-prefixed text links are gone; the named links beside them stay text.
    assert "📅" not in body
    assert not re.search(r">\s*Google Calendar\s*<", body)


@pytest.mark.django_db
def test_thread_link_is_the_discord_mark_plus_thread(client, event, team_member) -> None:
    """"Discord thread" becomes the Discord logo followed by "Thread"."""
    squad = Squad.objects.create(event=event, name="Squad A")
    _join(event, squad, team_member)
    _scheduled_race(squad, team_member, thread_link="https://example.test/thread")
    client.force_login(team_member)

    body = client.get(reverse("events:my_events")).content.decode()

    assert DISCORD_MARK_PATH_START in body
    assert re.search(r">\s*Thread\s*<", body)
    assert not re.search(r">\s*Discord thread\s*<", body)


@pytest.mark.django_db
def test_channel_links_are_icon_only_with_tooltips(client, event, team_member) -> None:
    """Channel / audio / squad-link buttons drop their text labels for tooltips."""
    squad = Squad.objects.create(
        event=event, name="Squad A",
        discord_channel_id=111, audio_channel_id=222, url="https://example.test/squad",
    )
    _join(event, squad, team_member)
    client.force_login(team_member)

    body = client.get(reverse("events:my_events")).content.decode()

    for tip in ("Discord channel", "Audio channel", "Squad link"):
        assert f'data-tip="{tip}"' in body
        assert f'aria-label="{tip}"' in body
    assert body.count("btn-xs btn-outline btn-square") == 3
    # The old inline text labels are gone; the tooltip carries the meaning now.
    assert not re.search(r">\s*Channel\s*<", body)
    assert not re.search(r">\s*Audio\s*<", body)
    assert not re.search(r">\s*Link\s*<", body)


@pytest.mark.django_db
def test_squad_card_carries_its_own_availability_menu(client, event, team_member) -> None:
    """Each squad row gets a calendar dropdown showing only that squad's grids."""
    squad = Squad.objects.create(event=event, name="Squad A")
    _join(event, squad, team_member)
    _scheduled_race(squad, team_member)
    client.force_login(team_member)

    resp = client.get(reverse("events:my_events"))
    body = resp.content.decode()

    assert resp.context["events_data"][0]["squads"][0]["ended_key"] == f"s{squad.pk}"
    assert 'aria-label="Availability"' in body
    # A multi-line {# #} comment renders as visible text; the partial must use a comment tag.
    assert "squad_data dict" not in body
    assert "Context:" not in body
    # The respond link is now rendered twice: event-wide menu and the squad's own menu.
    respond = reverse(
        "events:availability_respond",
        kwargs={"event_pk": event.pk, "squad_pk": squad.pk, "grid_pk": _grid_pk(squad)},
    )
    # Quote-terminated so the results URL, which extends this path, isn't also counted.
    assert body.count(f'href="{respond}"') == 2


@pytest.mark.django_db
def test_the_two_availability_menus_get_separate_ended_keys(client, event, team_member) -> None:
    """A shared key would make one menu's "Show ended" toggle flip rows in the other."""
    squad = Squad.objects.create(event=event, name="Squad A")
    _join(event, squad, team_member)
    today = date.today()
    AvailabilityGrid.objects.create(
        squad=squad, title="Past week",
        start_date=today - timedelta(days=9), end_date=today - timedelta(days=2),  # ended
        start_time="16:00", end_time="22:00", slot_duration=30,
        status=AvailabilityGrid.Status.PUBLISHED, grid_timezone="UTC",
    )
    client.force_login(team_member)

    body = client.get(reverse("events:my_events")).content.decode()

    assert f"av-ended-e{event.pk}" in body
    assert f"av-ended-s{squad.pk}" in body
    assert body.count("Show ended (1)") == 2  # one toggle per menu


@pytest.mark.django_db
def test_availability_menu_offers_new_availability_to_squad_managers(client, event, team_member) -> None:
    """A captain gets a "+ New availability" row pointing at the squad availability builder."""
    squad = Squad.objects.create(event=event, name="Squad A")
    squad.captains.add(team_member)
    _join(event, squad, team_member)
    client.force_login(team_member)

    resp = client.get(reverse("events:my_events"))
    body = resp.content.decode()

    # No grids exist yet, but the menu still opens so the captain can create the first one.
    assert resp.context["events_data"][0]["has_availability_grids"] is False
    assert resp.context["events_data"][0]["can_create_availability"] is True
    assert "New availability" in body
    # Once per menu (event-wide + this squad's). The old "Manage Squad" button that made a
    # third copy of this URL was removed as a duplicate.
    assert body.count(reverse("events:squad_availability", args=[event.pk, squad.pk])) == 2
    assert "Manage Squad" not in body


@pytest.mark.django_db
def test_plain_member_gets_no_new_availability_link(client, event, team_member) -> None:
    """The builder 403s a non-captain, so the row must not be offered to them."""
    squad = Squad.objects.create(event=event, name="Squad A")
    _join(event, squad, team_member)
    client.force_login(team_member)

    resp = client.get(reverse("events:my_events"))
    body = resp.content.decode()

    assert resp.context["events_data"][0]["can_create_availability"] is False
    assert "New availability" not in body
    assert reverse("events:squad_availability", args=[event.pk, squad.pk]) not in body


@pytest.mark.django_db
def test_squad_captain_sees_the_manage_squads_gear(client, event, team_member) -> None:
    """A squad captain can open squad manage, so the gear links there."""
    squad = Squad.objects.create(event=event, name="Squad A")
    squad.captains.add(team_member)
    _join(event, squad, team_member)
    client.force_login(team_member)

    resp = client.get(reverse("events:my_events"))
    body = resp.content.decode()

    assert resp.context["events_data"][0]["can_view_squad_manage"] is True
    assert reverse("events:squad_manage", args=[event.pk]) in body
    assert 'data-tip="Manage squads"' in body


@pytest.mark.django_db
def test_plain_rider_does_not_see_the_manage_squads_gear(client, event, team_member) -> None:
    """A rider with no squad leadership gets no link to a page that would 403 them."""
    squad = Squad.objects.create(event=event, name="Squad A")
    _join(event, squad, team_member)
    client.force_login(team_member)

    resp = client.get(reverse("events:my_events"))
    body = resp.content.decode()

    assert resp.context["events_data"][0]["can_view_squad_manage"] is False
    assert reverse("events:squad_manage", args=[event.pk]) not in body
    assert 'data-tip="Manage squads"' not in body


@pytest.mark.django_db
def test_event_admin_sees_the_gear_without_any_squad(client, event, event_admin) -> None:
    """The gear does not depend on the availability dropdown or on squad membership."""
    EventSignup.objects.create(event=event, user=event_admin, status=EventSignup.Status.REGISTERED)
    client.force_login(event_admin)

    resp = client.get(reverse("events:my_events"))
    body = resp.content.decode()

    assert resp.context["events_data"][0]["can_view_squad_manage"] is True
    assert resp.context["events_data"][0]["has_availability_grids"] is False
    assert reverse("events:squad_manage", args=[event.pk]) in body

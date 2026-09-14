"""An event with ``visible`` off should not be shown to team members anywhere.

The switch used to guard only the event list. Every other surface answered to an id or
borrowed the event's rows onto another page -- and the scheduled-races pages went
furthest, naming a hidden event, its race, and every rider selected for it down to their
zwid, to any plain member. These tests pin the rule down on each surface, and pin the
exception with it: the people organising the event still see it, or a draft could never
be built before it is announced.
"""

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

TODAY = date.today()


def _event_with_race(user, *, visible: bool, title: str = "Secret Squirrel Cup"):
    """Build an event holding one scheduled race with ``user`` selected for it."""
    event = Event.objects.create(
        title=title,
        start_date=TODAY,
        end_date=TODAY + timedelta(days=7),
        visible=visible,
    )
    squad = Squad.objects.create(event=event, name="Covert Squad")
    grid = AvailabilityGrid.objects.create(
        squad=squad,
        start_date=TODAY,
        end_date=TODAY + timedelta(days=7),
        start_time="18:00",
        end_time="20:00",
        slot_duration=30,
        status=AvailabilityGrid.Status.PUBLISHED,
    )
    selection = AvailabilitySlotSelection.objects.create(
        grid=grid,
        name="Undisclosed Race",
        slot_date=TODAY + timedelta(days=2),
        slot_time="18:30",
        status=AvailabilitySlotSelection.Status.CONFIRMED,
    )
    selection.selected_users.add(user)
    return event, squad


@pytest.fixture
def selected_rider(user_model, db):
    """A rider picked for a race, with a name and a zwid a card would print."""
    return user_model.objects.create_user(
        username="selected_rider",
        password="pw",  # noqa: S106
        first_name="Hidden",
        last_name="Racer",
        zwid=8675309,
    )


@pytest.mark.django_db
def test_all_races_withholds_every_part_of_a_hidden_events_race(auth_client, selected_rider) -> None:
    _event_with_race(selected_rider, visible=False)

    body = auth_client.get(reverse("events:all_races")).content.decode()

    # Four separate disclosures on one card, so four separate assertions: the event, the
    # race, who is riding it, and the id that identifies them off this site.
    assert "Secret Squirrel Cup" not in body
    assert "Undisclosed Race" not in body
    assert "Hidden Racer" not in body
    assert "8675309" not in body


@pytest.mark.django_db
def test_all_races_still_lists_a_visible_events_race(auth_client, selected_rider) -> None:
    _event_with_race(selected_rider, visible=True, title="Open Cup")

    body = auth_client.get(reverse("events:all_races")).content.decode()

    assert "Open Cup" in body
    assert "Undisclosed Race" in body


@pytest.mark.django_db
def test_all_races_keeps_a_hidden_event_for_someone_organising_it(client, event_admin, selected_rider) -> None:
    _event_with_race(selected_rider, visible=False)
    client.force_login(event_admin)

    body = client.get(reverse("events:all_races")).content.decode()

    assert "Secret Squirrel Cup" in body


@pytest.mark.django_db
def test_event_detail_is_not_found_for_a_hidden_event(auth_client, selected_rider) -> None:
    event, _ = _event_with_race(selected_rider, visible=False)

    response = auth_client.get(reverse("events:event_detail", args=[event.pk]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_event_races_page_is_not_found_for_a_hidden_event(auth_client, selected_rider) -> None:
    event, _ = _event_with_race(selected_rider, visible=False)

    response = auth_client.get(reverse("events:event_all_races", args=[event.pk]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_an_event_admin_can_still_open_a_hidden_event(client, event_admin, selected_rider) -> None:
    event, _ = _event_with_race(selected_rider, visible=False)
    client.force_login(event_admin)

    assert client.get(reverse("events:event_detail", args=[event.pk])).status_code == 200
    assert client.get(reverse("events:event_all_races", args=[event.pk])).status_code == 200


@pytest.mark.django_db
def test_a_squad_captain_can_still_open_their_hidden_event(client, team_member, selected_rider) -> None:
    # A captain is building the squad before the event is announced; taking the page away
    # would leave them nothing to build it on.
    event, squad = _event_with_race(selected_rider, visible=False)
    squad.captains.add(team_member)
    client.force_login(team_member)

    assert client.get(reverse("events:event_detail", args=[event.pk])).status_code == 200


@pytest.mark.django_db
def test_a_plain_member_of_another_squad_still_cannot_open_it(client, team_member, selected_rider) -> None:
    # Captaincy is per event: leading a squad elsewhere is not a key to this one.
    event, _ = _event_with_race(selected_rider, visible=False)
    other = Event.objects.create(title="Other", start_date=TODAY, end_date=TODAY + timedelta(days=1))
    Squad.objects.create(event=other, name="Elsewhere").captains.add(team_member)
    client.force_login(team_member)

    assert client.get(reverse("events:event_detail", args=[event.pk])).status_code == 404


@pytest.mark.django_db
def test_my_events_drops_a_hidden_event_the_rider_signed_up_for(client, team_member) -> None:
    event, squad = _event_with_race(team_member, visible=False)
    EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)
    client.force_login(team_member)

    body = client.get(reverse("events:my_events")).content.decode()

    # Signed up or not, the event is hidden -- and its page now answers 404, so listing it
    # here would only hand the rider a dead link.
    assert "Secret Squirrel Cup" not in body
    assert "Covert Squad" not in body


@pytest.mark.django_db
def test_my_events_still_lists_a_visible_signup(client, team_member) -> None:
    event, squad = _event_with_race(team_member, visible=True, title="Open Cup")
    EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)
    client.force_login(team_member)

    body = client.get(reverse("events:my_events")).content.decode()

    assert "Open Cup" in body

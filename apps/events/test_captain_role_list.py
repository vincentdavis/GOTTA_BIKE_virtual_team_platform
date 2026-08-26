"""Captain Roles are nominated on Role Setup, and squads may only pick from that list.

Third list to work this way, after Regional/Group Coordinators and Region Roles. The
captain role is handed to a squad's captain and vice-captain, so letting a squad point it
at any prefixed role was the same latitude the region role had.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.forms import EventRoleSetupForm, SquadForm
from apps.events.models import Event, Squad
from apps.team.models import DiscordRole

PREFIX = "$"
DIV1_CPT = "601"
DIV2_CPT = "602"
NOT_CAPTAIN = "603"


@pytest.fixture
def roles(db):
    """Create prefixed Discord roles to choose from."""
    DiscordRole.objects.create(role_id=DIV1_CPT, name=f"{PREFIX} Div 1 Captain", position=1)
    DiscordRole.objects.create(role_id=DIV2_CPT, name=f"{PREFIX} Div 2 Captain", position=2)
    DiscordRole.objects.create(role_id=NOT_CAPTAIN, name=f"{PREFIX} Something Else", position=3)


@pytest.fixture
def event(roles) -> Event:
    """Build an event nominating two captain roles.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7),
        prefixes=[PREFIX], captain_role_ids=[DIV1_CPT, DIV2_CPT],
    )


def _squad_form(event, **overrides):
    """Build a bound SquadForm wired the way the views wire it.

    Returns:
        The bound form.

    """
    data = {"name": "Div 1", "gender": "COED"}
    data.update(overrides)
    return SquadForm(
        data,
        event_prefixes=event.prefixes or [],
        coordinator_role_ids=event.coordinator_role_ids or [],
        region_role_ids=event.region_role_ids or [],
        captain_role_ids=event.captain_role_ids or [],
        event=event,
    )


@pytest.mark.django_db
def test_role_setup_accepts_prefixed_captain_roles(event) -> None:
    """The event nominates which roles squads may use as a captain role."""
    form = EventRoleSetupForm(
        {"prefixes": [PREFIX], "head_captain_role_id": "0", "event_role": "0",
         "captain_role_ids": [DIV1_CPT, DIV2_CPT]},
        instance=event,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["captain_role_ids"] == [DIV1_CPT, DIV2_CPT]


@pytest.mark.django_db
def test_role_setup_refuses_an_off_prefix_captain_role(event) -> None:
    """Defence in depth -- the rendered list is prefixed, a POST need not be."""
    DiscordRole.objects.create(role_id="999", name="No Prefix Here", position=4)
    form = EventRoleSetupForm(
        {"prefixes": [PREFIX], "head_captain_role_id": "0", "event_role": "0",
         "captain_role_ids": ["999"]},
        instance=event,
    )

    assert not form.is_valid()
    assert "captain_role_ids" in form.errors


@pytest.mark.django_db
def test_the_squad_picker_offers_only_the_events_captain_roles(event) -> None:
    """A prefixed role that was not nominated must not be selectable."""
    values = {str(v) for v, _ in _squad_form(event).fields["discord_captain_role"].widget.choices}

    assert values == {"0", DIV1_CPT, DIV2_CPT}
    assert NOT_CAPTAIN not in values


@pytest.mark.django_db
def test_a_squad_can_use_a_nominated_captain_role(event) -> None:
    """The normal case still works."""
    form = _squad_form(event, discord_captain_role=DIV1_CPT)

    assert form.is_valid(), form.errors
    assert form.cleaned_data["discord_captain_role"] == int(DIV1_CPT)


@pytest.mark.django_db
def test_a_captain_role_off_the_list_is_refused(event) -> None:
    """Carrying the event prefix is no longer enough on its own."""
    form = _squad_form(event, discord_captain_role=NOT_CAPTAIN)

    assert not form.is_valid()
    assert "discord_captain_role" in form.errors


@pytest.mark.django_db
def test_an_event_with_no_captain_roles_disables_the_picker(roles) -> None:
    """Nothing nominated means nothing selectable."""
    today = date.today()
    bare = Event.objects.create(
        title="Bare", start_date=today, end_date=today + timedelta(days=7), prefixes=[PREFIX]
    )
    form = SquadForm(
        {"name": "Div 1", "gender": "COED"},
        event_prefixes=[PREFIX], coordinator_role_ids=[], region_role_ids=[],
        captain_role_ids=[], event=bare,
    )

    assert form.fields["discord_captain_role"].widget.attrs.get("disabled") is True


@pytest.mark.django_db
def test_a_captain_role_since_removed_is_dropped_not_offered_back(event) -> None:
    """Otherwise every unrelated edit to that squad would be blocked."""
    squad = Squad.objects.create(event=event, name="Div 1", discord_captain_role=int(NOT_CAPTAIN))
    form = SquadForm(
        instance=squad, initial={"discord_captain_role": NOT_CAPTAIN},
        event_prefixes=[PREFIX], coordinator_role_ids=[], region_role_ids=[],
        captain_role_ids=[DIV1_CPT, DIV2_CPT], event=event,
    )

    assert form.initial["discord_captain_role"] == "0"


@pytest.mark.django_db
def test_the_setup_page_renders_the_captain_selector(client, event, superuser) -> None:
    """The control has to actually be on the page."""
    client.force_login(superuser)

    body = client.get(reverse("events:event_role_setup", args=[event.pk])).content.decode()

    assert "Captain Roles" in body
    assert 'id="captain-list"' in body


@pytest.mark.django_db
def test_the_seeding_migration_skips_off_prefix_roles(roles) -> None:
    """0068 seeded region roles unfiltered and made Role Setup un-saveable.

    An off-prefix id in the list is rejected by clean() while the checkbox list hides
    anything off-prefix, so there is no way to untick it. This seed filters by the
    event's prefixes; a squad using an off-prefix captain role is a misconfiguration for
    the "By Squad" tab to surface, not something to bless silently.
    """
    import importlib

    from django.apps import apps as django_apps

    DiscordRole.objects.create(role_id="704", name="/OFF Prefix Captain", position=9)
    today = date.today()
    legacy = Event.objects.create(
        title="Legacy", start_date=today, end_date=today + timedelta(days=7), prefixes=[PREFIX]
    )
    Squad.objects.create(event=legacy, name="Good", discord_captain_role=int(DIV1_CPT))
    Squad.objects.create(event=legacy, name="Bad", discord_captain_role=704)

    migration = importlib.import_module("apps.events.migrations.0069_add_event_captain_role_ids")
    migration.seed_captain_role_ids(django_apps, None)

    legacy.refresh_from_db()
    assert legacy.captain_role_ids == [DIV1_CPT]

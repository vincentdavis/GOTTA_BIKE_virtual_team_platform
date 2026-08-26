"""Region Roles are chosen on Role Setup, and squads may only pick from that list.

Previously a squad's Region Role could be any role carrying an event prefix. Since the
region role is auto-assigned to riders when they join the squad, that let a squad hand out
access through an arbitrary role. It now works like the Regional Coordinator role: the
event nominates the allowed set, and the squad picks from it.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.forms import EventRoleSetupForm, SquadForm
from apps.events.models import Event, Squad
from apps.team.models import DiscordRole

PREFIX = "$"
WEST = "777"
EAST = "778"
NOT_REGION = "888"


@pytest.fixture
def roles(db):
    """Create prefixed Discord roles to choose from."""
    DiscordRole.objects.create(role_id=WEST, name=f"{PREFIX} West", position=1)
    DiscordRole.objects.create(role_id=EAST, name=f"{PREFIX} East", position=2)
    DiscordRole.objects.create(role_id=NOT_REGION, name=f"{PREFIX} Something Else", position=3)


@pytest.fixture
def event(roles) -> Event:
    """Build an event with prefixes and two allowed region roles.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7),
        prefixes=[PREFIX], region_role_ids=[WEST, EAST],
    )


def _squad_form(event, **overrides):
    """Build a bound SquadForm wired the way the views wire it.

    Returns:
        The bound form.

    """
    data = {"name": "West", "gender": "COED"}
    data.update(overrides)
    return SquadForm(
        data,
        event_prefixes=event.prefixes or [],
        coordinator_role_ids=event.coordinator_role_ids or [],
        region_role_ids=event.region_role_ids or [],
        event=event,
    )


@pytest.mark.django_db
def test_role_setup_accepts_prefixed_region_roles(event) -> None:
    """The event nominates which roles squads may use as a Region Role."""
    form = EventRoleSetupForm(
        {"prefixes": [PREFIX], "head_captain_role_id": "0", "event_role": "0",
         "region_role_ids": [WEST, EAST]},
        instance=event,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["region_role_ids"] == [WEST, EAST]


@pytest.mark.django_db
def test_role_setup_refuses_an_off_prefix_region_role(event) -> None:
    """Defence in depth -- the rendered list is prefixed, but a POST need not be."""
    DiscordRole.objects.create(role_id="999", name="No Prefix Here", position=4)
    form = EventRoleSetupForm(
        {"prefixes": [PREFIX], "head_captain_role_id": "0", "event_role": "0",
         "region_role_ids": ["999"]},
        instance=event,
    )

    assert not form.is_valid()
    assert "region_role_ids" in form.errors


@pytest.mark.django_db
def test_role_setup_refuses_an_unknown_role_id(event) -> None:
    """A role id that is not in DiscordRole at all."""
    form = EventRoleSetupForm(
        {"prefixes": [PREFIX], "head_captain_role_id": "0", "event_role": "0",
         "region_role_ids": ["123456"]},
        instance=event,
    )

    assert not form.is_valid()
    assert "region_role_ids" in form.errors


@pytest.mark.django_db
def test_the_squad_picker_offers_only_the_events_region_roles(event) -> None:
    """A prefixed role that is not a nominated region role must not be selectable."""
    values = {str(v) for v, _ in _squad_form(event).fields["region_role"].widget.choices}

    assert values == {"0", WEST, EAST}
    assert NOT_REGION not in values


@pytest.mark.django_db
def test_a_squad_can_use_a_nominated_region_role(event) -> None:
    """The normal case still works."""
    form = _squad_form(event, region_role=WEST)

    assert form.is_valid(), form.errors
    assert form.cleaned_data["region_role"] == int(WEST)


@pytest.mark.django_db
def test_an_event_with_no_region_roles_disables_the_picker(roles) -> None:
    """Nothing configured means nothing selectable, like the coordinator field."""
    today = date.today()
    bare = Event.objects.create(
        title="Bare", start_date=today, end_date=today + timedelta(days=7), prefixes=[PREFIX]
    )
    form = SquadForm(
        {"name": "West", "gender": "COED"},
        event_prefixes=[PREFIX], coordinator_role_ids=[], region_role_ids=[], event=bare,
    )

    assert form.fields["region_role"].widget.attrs.get("disabled") is True


@pytest.mark.django_db
def test_a_region_role_since_removed_from_the_event_is_dropped_not_offered_back(event) -> None:
    """Otherwise the whole squad form would be un-saveable.

    clean_region_role rejects the stored value, so re-offering it as the preselected
    option would block every unrelated edit to that squad. Same handling as the
    coordinator picker.
    """
    squad = Squad.objects.create(event=event, name="West", region_role=int(NOT_REGION))
    form = SquadForm(
        instance=squad, initial={"region_role": NOT_REGION},
        event_prefixes=[PREFIX], coordinator_role_ids=[], region_role_ids=[WEST, EAST], event=event,
    )

    assert form.initial["region_role"] == "0"
    assert NOT_REGION not in {str(v) for v, _ in form.fields["region_role"].widget.choices}


@pytest.mark.django_db
def test_the_setup_page_renders_the_region_selector(client, event, superuser) -> None:
    """The control has to actually be on the page."""
    client.force_login(superuser)

    body = client.get(reverse("events:event_role_setup", args=[event.pk])).content.decode()

    assert "Region Roles" in body
    assert 'id="region-list"' in body


@pytest.mark.django_db
def test_the_seeding_migration_keeps_region_roles_squads_already_use(roles) -> None:
    """Existing squads must not lose their region role when the rule tightens.

    Squads could previously pick any prefixed role. Migration 0068 seeds each event's
    allowed list from what its own squads already use, so nothing silently drops on the
    next edit. This runs the migration's own function against the live models.
    """
    import importlib

    from django.apps import apps as django_apps

    today = date.today()
    legacy = Event.objects.create(
        title="Legacy", start_date=today, end_date=today + timedelta(days=7), prefixes=[PREFIX]
    )
    Squad.objects.create(event=legacy, name="West", region_role=int(WEST))
    Squad.objects.create(event=legacy, name="East", region_role=int(EAST))
    Squad.objects.create(event=legacy, name="No region")
    assert legacy.region_role_ids == []

    migration = importlib.import_module("apps.events.migrations.0068_add_event_region_role_ids")
    migration.seed_region_role_ids(django_apps, None)

    legacy.refresh_from_db()
    assert legacy.region_role_ids == sorted([WEST, EAST])


@pytest.mark.django_db
def test_a_checked_off_prefix_role_stays_reachable_on_the_page(client, event, superuser) -> None:
    """Otherwise the form is permanently un-saveable.

    The list's JS hides roles that do not match the event's prefixes. A role that is
    checked but off-prefix is rejected by clean() on submit, so if it is also hidden there
    is no checkbox on screen to untick and no way to save the page at all. The list is
    rendered from the globally allowed prefixes, so the checkbox must exist server-side;
    keeping it visible when checked is handled in the template's filter.
    """
    client.force_login(superuser)
    DiscordRole.objects.create(role_id="555", name="/APAC B", position=9)
    event.region_role_ids = ["555"]
    event.save(update_fields=["region_role_ids"])

    body = client.get(reverse("events:event_role_setup", args=[event.pk])).content.decode()

    # The filter that keeps checked items visible has to be present...
    assert "matchesPrefix || checked" in body
    # ...and re-run when a box is toggled, so unticking hides it again.
    assert "applyFilter();" in body


@pytest.mark.django_db
def test_an_off_prefix_region_role_is_refused_at_save(event) -> None:
    """The server-side half of the same case: it must not be quietly accepted."""
    DiscordRole.objects.create(role_id="555", name="/APAC B", position=9)
    form = EventRoleSetupForm(
        {"prefixes": [PREFIX], "head_captain_role_id": "0", "event_role": "0",
         "region_role_ids": [WEST, "555"]},
        instance=event,
    )

    assert not form.is_valid()
    assert "region_role_ids" in form.errors

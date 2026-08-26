"""Guards on which Discord roles a squad may use.

A squad's roles are handed to riders as they join it. Pointing one at the event's head
captain role would therefore give every member of that squad event-wide control of
squads, Discord roles and eligibility -- so it is refused everywhere on the form, not
merely hidden from the pickers.
"""

from datetime import date, timedelta

import pytest

from apps.events.forms import SquadForm
from apps.events.models import Event
from apps.team.models import DiscordRole

HEAD_CAPTAIN = 700
SQUAD_ROLE = 800
COORD_ROLE = 900
PREFIX = "$"


@pytest.fixture
def event(db) -> Event:
    """Build an event with a head captain role, a prefix and a coordinator role.

    Returns:
        The event.

    """
    today = date.today()
    for role_id, name in ((HEAD_CAPTAIN, f"{PREFIX} Head Captain"),
                          (SQUAD_ROLE, f"{PREFIX} Div 1"),
                          (COORD_ROLE, f"{PREFIX} EMEA Coordinator")):
        DiscordRole.objects.create(role_id=str(role_id), name=name)
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7),
        head_captain_role_id=HEAD_CAPTAIN, prefixes=[PREFIX], coordinator_role_ids=[str(COORD_ROLE)],
    )


def _form(event, **overrides):
    """Build a bound SquadForm with the given role selections.

    Returns:
        The bound form.

    """
    data = {"name": "Div 1", "gender": "COED"}
    data.update(overrides)
    return SquadForm(
        data,
        event_prefixes=event.prefixes or [],
        coordinator_role_ids=event.coordinator_role_ids or [],
        event=event,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field",
    ["team_discord_role", "discord_captain_role", "region_role", "regional_coordinator_role"],
)
def test_the_head_captain_role_is_refused_on_every_squad_role_field(event, field) -> None:
    """A crafted POST is the real gate; the picker only hides it."""
    form = _form(event, **{field: str(HEAD_CAPTAIN)})

    assert not form.is_valid()
    assert "Head Captain role cannot be used" in str(form.errors[field])


@pytest.mark.django_db
def test_an_ordinary_prefixed_role_is_still_accepted(event) -> None:
    """The guard must not block the normal case."""
    form = _form(event, team_discord_role=str(SQUAD_ROLE))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["team_discord_role"] == SQUAD_ROLE


@pytest.mark.django_db
def test_the_head_captain_role_is_not_offered_in_the_squad_picker(event) -> None:
    """Refusing it at submit is correct but late; it should not be selectable either."""
    form = _form(event)
    # With a prefix configured the list is grouped into optgroups, so flatten it.
    values = set()
    for value, label in form.fields["team_discord_role"].widget.choices:
        if isinstance(label, (list, tuple)):
            values.update(str(pair[0]) for pair in label)
        else:
            values.add(str(value))

    assert str(SQUAD_ROLE) in values
    assert str(HEAD_CAPTAIN) not in values


@pytest.mark.django_db
def test_a_coordinator_role_outside_the_event_list_is_refused(event) -> None:
    """Pre-existing guard: the coordinator field is limited to the event's own roles."""
    DiscordRole.objects.create(role_id="901", name=f"{PREFIX} Other Coordinator")
    form = _form(event, regional_coordinator_role="901")

    assert not form.is_valid()
    assert "configured for this event" in str(form.errors["regional_coordinator_role"])


@pytest.mark.django_db
def test_a_configured_coordinator_role_is_accepted(event) -> None:
    """The normal coordinator case still works.

    Worth pinning alongside the head-captain guard: both now run in the same clean method,
    and the head-captain check is deliberately first so its clearer message wins when a
    role happens to be both.
    """
    form = _form(event, regional_coordinator_role=str(COORD_ROLE))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["regional_coordinator_role"] == COORD_ROLE


@pytest.mark.django_db
def test_an_event_with_no_head_captain_role_blocks_nothing(db) -> None:
    """head_captain_role_id defaults to 0, which must not match a real role."""
    DiscordRole.objects.create(role_id=str(SQUAD_ROLE), name=f"{PREFIX} Div 1")
    today = date.today()
    bare = Event.objects.create(
        title="No HC", start_date=today, end_date=today + timedelta(days=7), prefixes=[PREFIX]
    )
    form = SquadForm(
        {"name": "Div 1", "gender": "COED", "team_discord_role": str(SQUAD_ROLE)},
        event_prefixes=bare.prefixes or [], coordinator_role_ids=[], event=bare,
    )

    assert form.is_valid(), form.errors

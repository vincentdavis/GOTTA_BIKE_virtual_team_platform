"""Choosing captains on the squad edit form, and the roles that follow.

Discord calls are patched at the ``apps.events.views`` boundary, so nothing here
touches the network. Each test asserts on the recorded calls instead.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad, SquadMember
from apps.team.models import DiscordRole

CAPTAIN_ROLE = 4001
SQUAD_ROLE = 4002


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    # Squad role fields are prefix-validated, so the event needs a prefix and the
    # DiscordRole rows have to be named to match.
    DiscordRole.objects.create(role_id=str(CAPTAIN_ROLE), name="$Synthesis Captain")
    DiscordRole.objects.create(role_id=str(SQUAD_ROLE), name="$Synthesis")
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True,
        prefixes=["$"],
        # A squad's captain role is now chosen from the event's nominated list, so the
        # role these tests assign has to be on it.
        captain_role_ids=[str(CAPTAIN_ROLE)],
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad wired to both Discord roles.

    Returns:
        The squad.

    """
    return Squad.objects.create(
        event=event, name="Synthesis",
        discord_captain_role=CAPTAIN_ROLE, team_discord_role=SQUAD_ROLE,
    )


@pytest.fixture
def rider(user_model, event):
    """Register a rider for the event without putting them in a squad.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username="alice", email="alice@example.test", first_name="Alice", last_name="Rider",
        discord_id="900001",
    )
    EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)
    return user


@pytest.fixture
def discord_calls(monkeypatch):
    """Record role grants/removals instead of calling Discord.

    Returns:
        dict with "added" and "removed" lists of (user_id, role_id).

    """
    calls = {"added": [], "removed": []}
    monkeypatch.setattr(
        "apps.events.views._assign_discord_role",
        lambda user, role_id, label, **kw: calls["added"].append((user.pk, role_id)),
    )
    monkeypatch.setattr(
        "apps.events.views._unassign_discord_role",
        lambda user, role_id, **kw: calls["removed"].append((user.pk, role_id)),
    )
    monkeypatch.setattr("apps.events.views._assign_region_role", lambda user, squad, **kw: None)
    return calls


def _post(client, event, squad, **extra):
    """Submit the squad edit form with the minimum required fields.

    Returns:
        The response.

    """
    return client.post(
        reverse("events:squad_edit", args=[event.pk, squad.pk]),
        data={"name": squad.name, "gender": "COED",
              "discord_captain_role": CAPTAIN_ROLE, "team_discord_role": SQUAD_ROLE, **extra},
        follow=True,
    )


@pytest.mark.django_db
def test_picker_lists_event_signups_not_squad_members(client, event, squad, rider, event_admin) -> None:
    """The whole point: you can pick someone who isn't in the squad yet."""
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_edit", args=[event.pk, squad.pk])).content.decode()

    assert 'id="id_captains"' in body
    assert 'id="id_vice_captains"' in body
    assert "Alice Rider" in body
    assert not SquadMember.objects.filter(squad=squad, user=rider).exists()


@pytest.mark.django_db
def test_choosing_a_captain_grants_the_captain_role(client, event, squad, rider, event_admin, discord_calls) -> None:
    client.force_login(event_admin)

    _post(client, event, squad, captains=[rider.pk])

    assert list(squad.captains.all()) == [rider]
    assert (rider.pk, CAPTAIN_ROLE) in discord_calls["added"]


@pytest.mark.django_db
def test_captain_is_not_added_to_the_squad_unless_asked(client, event, squad, rider, event_admin,
                                                        discord_calls) -> None:
    """Leading a squad and racing for it are separate."""
    client.force_login(event_admin)

    _post(client, event, squad, captains=[rider.pk])

    assert not SquadMember.objects.filter(squad=squad, user=rider).exists()
    assert (rider.pk, SQUAD_ROLE) not in discord_calls["added"]


@pytest.mark.django_db
def test_the_checkbox_adds_them_to_the_squad_with_its_role(client, event, squad, rider, event_admin,
                                                           discord_calls) -> None:
    client.force_login(event_admin)

    _post(client, event, squad, captains=[rider.pk], captains_add_as_members="on")

    member = SquadMember.objects.get(squad=squad, user=rider)
    assert member.status == SquadMember.Status.MEMBER
    assert (rider.pk, SQUAD_ROLE) in discord_calls["added"]
    assert (rider.pk, CAPTAIN_ROLE) in discord_calls["added"]


@pytest.mark.django_db
def test_moving_captain_to_vice_captain_keeps_the_role(client, event, squad, rider, event_admin,
                                                       discord_calls) -> None:
    """Both roles map to the same Discord role, so the move must not revoke it."""
    squad.captains.add(rider)
    client.force_login(event_admin)

    _post(client, event, squad, vice_captains=[rider.pk])

    assert list(squad.vice_captains.all()) == [rider]
    assert list(squad.captains.all()) == []
    assert discord_calls["removed"] == []


@pytest.mark.django_db
def test_dropping_a_leader_revokes_the_role(client, event, squad, rider, event_admin, discord_calls) -> None:
    squad.captains.add(rider)
    client.force_login(event_admin)

    _post(client, event, squad)

    assert list(squad.captains.all()) == []
    assert (rider.pk, CAPTAIN_ROLE) in discord_calls["removed"]


@pytest.mark.django_db
def test_same_person_cannot_be_both(client, event, squad, rider, event_admin, discord_calls) -> None:
    client.force_login(event_admin)

    resp = _post(client, event, squad, captains=[rider.pk], vice_captains=[rider.pk])

    assert "Already listed as captain" in resp.content.decode()
    assert list(squad.captains.all()) == []


@pytest.mark.django_db
def test_an_ineligible_captain_is_warned_about_not_blocked(client, event, squad, rider, event_admin,
                                                           discord_calls) -> None:
    """A strong rider captaining a development squad is legitimate.

    require_zauth goes in the POST, not just on the model: the warning is computed from
    the squad as the form just saved it, and an omitted checkbox would clear it first.
    """
    client.force_login(event_admin)

    resp = _post(client, event, squad, captains=[rider.pk], require_zauth="on")
    body = " ".join(str(m) for m in resp.context["messages"])

    assert list(squad.captains.all()) == [rider]      # not blocked
    assert "zauth" in body                            # but warned


@pytest.mark.django_db
def test_captains_chosen_at_creation_are_saved(client, event, rider, event_admin, discord_calls) -> None:
    """save(commit=False) skips the m2m write; the create view has to call save_m2m()."""
    client.force_login(event_admin)

    client.post(
        reverse("events:squad_create", args=[event.pk]),
        data={"name": "New Squad", "gender": "COED",
              "discord_captain_role": CAPTAIN_ROLE, "captains": [rider.pk]},
    )

    created = Squad.objects.get(event=event, name="New Squad")
    assert list(created.captains.all()) == [rider]
    assert (rider.pk, CAPTAIN_ROLE) in discord_calls["added"]


@pytest.mark.django_db
def test_squad_card_groups_every_role_into_one_section(client, event, squad, event_admin) -> None:
    """All five, including region and coordinator, which had no display surface at all."""
    DiscordRole.objects.create(role_id="4003", name="$EMEA West")
    DiscordRole.objects.create(role_id="4004", name="$EMEA Coordinator")
    DiscordRole.objects.create(role_id="4005", name="$ZRL")
    squad.region_role = 4003
    squad.regional_coordinator_role = 4004
    squad.save(update_fields=["region_role", "regional_coordinator_role"])
    event.event_role = 4005
    event.save(update_fields=["event_role"])
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_assign_page", args=[event.pk])).content.decode()

    assert "Roles (5)" in body
    assert "Event:</span> @$ZRL" in body
    assert "Squad:</span> @$Synthesis" in body
    assert "Captain:</span> @$Synthesis Captain" in body
    assert "Region:</span> @$EMEA West" in body
    assert "Coordinator:</span> @$EMEA Coordinator" in body


@pytest.mark.django_db
def test_roles_section_counts_only_what_is_configured(client, event, squad, event_admin) -> None:
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_assign_page", args=[event.pk])).content.decode()

    assert "Roles (2)" in body      # squad + captain, from the fixture
    assert "Region:" not in body


@pytest.mark.django_db
def test_no_roles_section_when_none_are_configured(client, event, event_admin) -> None:
    """A squad with no roles should not grow a disclosure widget."""
    Squad.objects.create(event=event, name="Bare")
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_assign_page", args=[event.pk])).content.decode()

    assert "Roles (0)" not in body


@pytest.mark.django_db
def test_creating_a_squad_returns_to_manage_squads(client, event, event_admin, discord_calls) -> None:
    """Create used to land on the event page while edit returned to Manage Squads.

    Both forms are opened from Manage Squads, and it is where the next thing you do
    with a new squad lives -- assigning riders, setting availability.
    """
    client.force_login(event_admin)

    resp = client.post(
        reverse("events:squad_create", args=[event.pk]),
        data={"name": "Fresh Squad", "gender": "COED"},
    )

    assert resp.status_code == 302
    assert resp["Location"] == reverse("events:squad_manage", args=[event.pk])
    assert Squad.objects.filter(event=event, name="Fresh Squad").exists()


@pytest.mark.django_db
def test_editing_a_squad_returns_to_the_same_place(client, event, squad, event_admin, discord_calls) -> None:
    """Pinned alongside create so the two cannot drift apart again."""
    client.force_login(event_admin)

    resp = client.post(
        reverse("events:squad_edit", args=[event.pk, squad.pk]),
        data={"name": squad.name, "gender": "COED"},
    )

    assert resp.status_code == 302
    assert resp["Location"] == reverse("events:squad_manage", args=[event.pk])

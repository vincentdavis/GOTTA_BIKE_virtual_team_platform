"""Regional/group coordinators get the same squad management as the event head captain."""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad
from apps.events.views import _can_manage_event_squads, _can_manage_squad_availability, _can_view_squad_manage

COORD_ROLE = "700"


@pytest.fixture
def event(db) -> Event:
    """Build an event that has a coordinator role configured.

    Returns:
        An event whose coordinator_role_ids contains COORD_ROLE.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL",
        start_date=today,
        end_date=today + timedelta(days=7),
        visible=True,
        coordinator_role_ids=[COORD_ROLE],
    )


@pytest.fixture
def coordinator(user_model):
    """Build a team member holding the event's coordinator Discord role.

    Returns:
        A user with team_member and the coordinator role, but no event_admin.

    """
    return user_model.objects.create_user(
        username="coord",
        email="coord@example.test",
        permission_overrides={"team_member": True},
        discord_id="900900",
        discord_roles={COORD_ROLE: "Region Coordinator"},
    )


@pytest.fixture
def outsider(user_model):
    """Build a team member with an unrelated Discord role.

    Returns:
        A user who is neither admin, captain, nor coordinator.

    """
    return user_model.objects.create_user(
        username="outsider",
        email="outsider@example.test",
        permission_overrides={"team_member": True},
        discord_id="900901",
        discord_roles={"999": "Some Other Role"},
    )


@pytest.mark.django_db
def test_coordinator_passes_the_squad_management_gates(event, coordinator) -> None:
    squad = Squad.objects.create(event=event, name="Squad A")
    assert _can_manage_event_squads(coordinator, event) is True
    assert _can_manage_squad_availability(coordinator, squad) is True
    assert _can_view_squad_manage(coordinator, event) is True


@pytest.mark.django_db
def test_non_coordinator_still_blocked(event, outsider) -> None:
    squad = Squad.objects.create(event=event, name="Squad A")
    assert _can_manage_event_squads(outsider, event) is False
    assert _can_manage_squad_availability(outsider, squad) is False
    assert _can_view_squad_manage(outsider, event) is False


@pytest.mark.django_db
def test_coordinator_can_open_squad_manage_page(client, event, coordinator) -> None:
    Squad.objects.create(event=event, name="Squad A")
    client.force_login(coordinator)

    resp = client.get(reverse("events:squad_manage", args=[event.pk]))

    assert resp.status_code == 200
    assert resp.context["can_manage_all"] is True


@pytest.mark.django_db
def test_coordinator_can_open_squad_edit(client, event, coordinator) -> None:
    squad = Squad.objects.create(event=event, name="Squad A")
    client.force_login(coordinator)

    resp = client.get(reverse("events:squad_edit", args=[event.pk, squad.pk]))

    assert resp.status_code == 200


@pytest.mark.django_db
def test_coordinator_can_open_squad_create(client, event, coordinator) -> None:
    client.force_login(coordinator)

    resp = client.get(reverse("events:squad_create", args=[event.pk]))

    assert resp.status_code == 200


@pytest.mark.django_db
def test_coordinator_can_delete_a_squad(client, event, coordinator) -> None:
    squad = Squad.objects.create(event=event, name="Squad A")
    client.force_login(coordinator)

    client.post(reverse("events:squad_delete", args=[event.pk, squad.pk]))

    assert not Squad.objects.filter(pk=squad.pk).exists()


@pytest.mark.django_db
def test_outsider_cannot_delete_a_squad(client, event, outsider) -> None:
    squad = Squad.objects.create(event=event, name="Squad A")
    client.force_login(outsider)

    client.post(reverse("events:squad_delete", args=[event.pk, squad.pk]))

    assert Squad.objects.filter(pk=squad.pk).exists()


@pytest.mark.django_db
def test_coordinator_sees_add_squad_but_not_assign_riders(client, event, coordinator) -> None:
    """Assign Riders is event_admin-only, so a coordinator must not be shown a 403 button."""
    Squad.objects.create(event=event, name="Squad A")
    client.force_login(coordinator)

    resp = client.get(reverse("events:squad_manage", args=[event.pk]))
    body = resp.content.decode()

    assert resp.context["can_manage_all"] is True
    assert resp.context["can_assign_riders"] is False
    assert reverse("events:squad_create", args=[event.pk]) in body  # can create squads
    assert reverse("events:squad_assign_page", args=[event.pk]) not in body  # but not assign


@pytest.mark.django_db
def test_event_admin_sees_both_buttons(client, event, user_model) -> None:
    Squad.objects.create(event=event, name="Squad A")
    admin = user_model.objects.create_user(
        username="ea", email="ea@example.test",
        permission_overrides={"team_member": True, "event_admin": True},
    )
    client.force_login(admin)

    resp = client.get(reverse("events:squad_manage", args=[event.pk]))
    body = resp.content.decode()

    assert resp.context["can_assign_riders"] is True
    assert reverse("events:squad_assign_page", args=[event.pk]) in body
    assert reverse("events:squad_create", args=[event.pk]) in body


@pytest.mark.django_db
def test_actions_menu_groups_items_and_offers_help(client, event, user_model) -> None:
    """The header collapses to one primary action plus a grouped Actions menu."""
    Squad.objects.create(event=event, name="Squad A")
    admin = user_model.objects.create_user(
        username="ea2", email="ea2@example.test",
        permission_overrides={"team_member": True, "event_admin": True},
    )
    client.force_login(admin)

    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    for heading in ("View", "Reports", "Manage", "Help"):
        assert f'<li class="menu-title">{heading}</li>' in body
    assert "View event" in body  # replaces the old "Back to Event" link
    assert "&larr; Back to Event" not in body
    assert 'id="squad-help-modal"' in body
    assert "How this page works" in body


@pytest.mark.django_db
def test_unpermitted_menu_items_are_disabled_not_hidden(client, event, coordinator) -> None:
    """A coordinator lacks event_admin, so Assign riders shows greyed out rather than gone.

    Discord Roles is NOT among them: coordinators hold that gate now, since they already
    run squads event-wide and granting the squad role is the other half of the job.
    """
    Squad.objects.create(event=event, name="Squad A")
    client.force_login(coordinator)

    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    assert body.count('class="menu-disabled"') == 1  # Assign riders only
    assert "Assign riders" in body  # still listed, just not actionable
    assert f'href="/events/{event.pk}/discord-roles/"' in body  # a live link now


@pytest.mark.django_db
def test_squad_manage_offers_a_timezone_filter(client, event, coordinator) -> None:
    """The squad list exposes a timezone filter built from the squads actually present."""
    Squad.objects.create(event=event, name="Squad A", squad_timezone="US/Mountain")
    Squad.objects.create(event=event, name="Squad B", squad_timezone="Europe/London")
    Squad.objects.create(event=event, name="Squad C", squad_timezone="US/Mountain")  # duplicate
    Squad.objects.create(event=event, name="Squad D")  # no timezone set
    client.force_login(coordinator)

    resp = client.get(reverse("events:squad_manage", args=[event.pk]))

    # Deduplicated and sorted; blank timezones are omitted.
    assert list(resp.context["squad_timezones"]) == ["Europe/London", "US/Mountain"]
    body = resp.content.decode()
    assert 'id="filter-squad-timezone"' in body
    assert 'data-timezone="US/Mountain"' in body


@pytest.mark.django_db
def test_squad_manage_offers_a_gender_filter(client, event, coordinator) -> None:
    """The squad list exposes a gender filter built from the genders actually present."""
    Squad.objects.create(event=event, name="Squad A", gender="Female")
    Squad.objects.create(event=event, name="Squad B", gender="COED")
    Squad.objects.create(event=event, name="Squad C", gender="Female")  # duplicate
    Squad.objects.create(event=event, name="Squad D")  # no gender set
    client.force_login(coordinator)

    resp = client.get(reverse("events:squad_manage", args=[event.pk]))

    assert list(resp.context["squad_genders"]) == ["COED", "Female"]  # deduped + sorted
    body = resp.content.decode()
    assert 'id="filter-squad-gender"' in body
    assert 'data-gender="Female"' in body


@pytest.mark.django_db
def test_squad_manage_hides_gender_filter_when_unused(client, event, coordinator) -> None:
    Squad.objects.create(event=event, name="Squad A")
    client.force_login(coordinator)

    resp = client.get(reverse("events:squad_manage", args=[event.pk]))

    assert list(resp.context["squad_genders"]) == []
    assert 'id="filter-squad-gender"' not in resp.content.decode()


@pytest.mark.django_db
def test_squad_manage_hides_timezone_filter_when_unused(client, event, coordinator) -> None:
    """With no squad timezones set there is nothing to filter by, so no control renders."""
    Squad.objects.create(event=event, name="Squad A")
    client.force_login(coordinator)

    resp = client.get(reverse("events:squad_manage", args=[event.pk]))

    assert list(resp.context["squad_timezones"]) == []
    assert 'id="filter-squad-timezone"' not in resp.content.decode()


@pytest.mark.django_db
def test_coordinator_role_only_applies_to_its_own_event(user_model, coordinator) -> None:
    """Holding a coordinator role grants nothing on an event that doesn't list it."""
    today = date.today()
    other_event = Event.objects.create(
        title="Other", start_date=today, end_date=today + timedelta(days=7),
        visible=True, coordinator_role_ids=[],
    )
    squad = Squad.objects.create(event=other_event, name="Squad Z")

    assert _can_manage_event_squads(coordinator, other_event) is False
    assert _can_manage_squad_availability(coordinator, squad) is False

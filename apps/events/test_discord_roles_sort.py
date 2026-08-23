"""Default row order on the Discord Roles page.

The role columns are grouped by squad, so reading down the page should follow the squad
whose roles you are granting rather than jumping between them.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad, SquadMember


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True,
    )


def _rider(user_model, event, username, first, *squads):
    """Register a rider for the event and put them in the given squads.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(
        username=username, email=f"{username}@example.test", first_name=first, last_name="R",
        discord_id=f"d{username}",
    )
    EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)
    for squad in squads:
        SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _order(resp) -> list[str]:
    """Rider first names in rendered row order.

    Returns:
        List of first names.

    """
    return [e["user"].first_name for e in resp.context["enriched_signups"]]


@pytest.mark.django_db
def test_rows_are_grouped_by_squad(client, event, superuser, user_model) -> None:
    zulu = Squad.objects.create(event=event, name="Zulu", team_discord_role=111)
    alpha = Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    # Created out of order, so passing cannot be an accident of insertion order.
    _rider(user_model, event, "r1", "Zed", zulu)
    _rider(user_model, event, "r2", "Ann", alpha)
    _rider(user_model, event, "r3", "Bob", alpha)
    client.force_login(superuser)

    resp = client.get(reverse("events:discord_roles", args=[event.pk]))

    assert _order(resp) == ["Ann", "Bob", "Zed"]


@pytest.mark.django_db
def test_riders_in_no_squad_sort_last(client, event, superuser, user_model) -> None:
    """Leading with a block of blank Squads cells buries the rows that have roles."""
    alpha = Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    _rider(user_model, event, "r1", "Nobody")
    _rider(user_model, event, "r2", "Ann", alpha)
    client.force_login(superuser)

    resp = client.get(reverse("events:discord_roles", args=[event.pk]))

    assert _order(resp) == ["Ann", "Nobody"]


@pytest.mark.django_db
def test_ties_fall_back_to_rider_name(client, event, superuser, user_model) -> None:
    alpha = Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    _rider(user_model, event, "r1", "Carl", alpha)
    _rider(user_model, event, "r2", "Ann", alpha)
    client.force_login(superuser)

    resp = client.get(reverse("events:discord_roles", args=[event.pk]))

    assert _order(resp) == ["Ann", "Carl"]


@pytest.mark.django_db
def test_a_multi_squad_riders_cell_reads_in_a_stable_order(client, event, superuser, user_model) -> None:
    """The sort keys on the joined cell text, so that text must not vary between loads."""
    zulu = Squad.objects.create(event=event, name="Zulu", team_discord_role=111)
    alpha = Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    _rider(user_model, event, "r1", "Both", zulu, alpha)
    client.force_login(superuser)

    resp = client.get(reverse("events:discord_roles", args=[event.pk]))

    entry = resp.context["enriched_signups"][0]
    assert [s.name for s in entry["assigned_squads"]] == ["Alpha", "Zulu"]

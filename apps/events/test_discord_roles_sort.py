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


@pytest.mark.django_db
def test_the_name_and_squad_columns_are_pinned(client, event, superuser, user_model) -> None:
    """Both classes are load-bearing: table-pin-col-2 alone pins nothing in place.

    It offsets column 2 by --pin-col-2-left, which the page's script only sets, and
    column 1 only stays put because of table-pin-col. Dropping either silently loses
    the freeze on a table too wide to read without it.
    """
    alpha = Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    _rider(user_model, event, "r1", "Ann", alpha)
    client.force_login(superuser)

    body = client.get(reverse("events:discord_roles", args=[event.pk])).content.decode()

    assert "table-pin-col table-pin-col-2" in body
    assert "--pin-col-2-left" in body          # the script that supplies the offset


@pytest.mark.django_db
def test_the_legend_covers_every_cell_state(client, event, superuser, user_model) -> None:
    """Four states render in the grid, and a legend that omits one is worse than none."""
    alpha = Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    _rider(user_model, event, "r1", "Ann", alpha)
    event.timezone_options = ["EMEA"]
    event.timezone_role_map = {"EMEA": "777"}
    event.save(update_fields=["timezone_options", "timezone_role_map"])
    client.force_login(superuser)

    body = client.get(reverse("events:discord_roles", args=[event.pk])).content.decode()

    legend = body[body.index(">Legend<"):body.index('overflow-x-auto')]
    assert "Has the role" in legend
    assert "Missing the role" in legend
    assert "did not pick that region" in legend      # the faded cells
    assert "Not in that squad" in legend             # the dash
    # The symbols themselves, so the legend cannot drift from what the cells render.
    assert "&#10003;" in legend and "&#10007;" in legend


@pytest.mark.django_db
def test_the_faded_entry_is_dropped_without_region_columns(client, event, superuser, user_model) -> None:
    """Explaining a cell state this event cannot produce is just noise."""
    alpha = Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    _rider(user_model, event, "r1", "Ann", alpha)
    client.force_login(superuser)

    body = client.get(reverse("events:discord_roles", args=[event.pk])).content.decode()

    assert ">Legend<" in body
    assert "did not pick that region" not in body


@pytest.mark.django_db
def test_a_role_held_without_membership_is_flagged_not_hidden(client, event, superuser, user_model) -> None:
    """The cell used to hardcode has_role False for non-members, so this was invisible."""
    alpha = Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    rider = _rider(user_model, event, "r1", "Ann")          # registered, NOT in Alpha
    rider.discord_roles = {"222": "Alpha"}                   # but holds Alpha's role
    rider.save(update_fields=["discord_roles"])
    client.force_login(superuser)

    resp = client.get(reverse("events:discord_roles", args=[event.pk]))

    srs = resp.context["enriched_signups"][0]["squad_role_status"][0]
    assert srs["squad"].pk == alpha.pk
    assert srs["is_member"] is False
    assert srs["has_role"] is True                            # looked up, not assumed
    assert "text-warning" in resp.content.decode()


@pytest.mark.django_db
def test_a_withdrawn_rider_keeping_roles_is_listed(client, event, superuser, user_model) -> None:
    """Withdrawing does not strip roles, and drops the rider out of the main table."""
    alpha = Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    gone = _rider(user_model, event, "r1", "Gone", alpha)
    EventSignup.objects.filter(user=gone).update(status=EventSignup.Status.WITHDRAWN)
    gone.discord_roles = {"222": "Alpha"}
    gone.save(update_fields=["discord_roles"])
    client.force_login(superuser)

    resp = client.get(reverse("events:discord_roles", args=[event.pk]))

    assert [r["user"].pk for r in resp.context["stragglers"]] == [gone.pk]
    assert [h["label"] for h in resp.context["stragglers"][0]["held"]] == ["Alpha"]
    assert not resp.context["enriched_signups"]              # gone from the main table
    assert "Not registered, still holding roles" in resp.content.decode()


@pytest.mark.django_db
def test_a_withdrawn_rider_holding_nothing_is_not_listed(client, event, superuser, user_model) -> None:
    """Otherwise the section fills with every rider who ever withdrew."""
    Squad.objects.create(event=event, name="Alpha", team_discord_role=222)
    gone = _rider(user_model, event, "r1", "Gone")
    EventSignup.objects.filter(user=gone).update(status=EventSignup.Status.WITHDRAWN)
    client.force_login(superuser)

    resp = client.get(reverse("events:discord_roles", args=[event.pk]))

    assert resp.context["stragglers"] == []
    assert "Not registered, still holding roles" not in resp.content.decode()


@pytest.mark.django_db
def test_a_coordinator_can_open_the_page(client, event, user_model) -> None:
    """Coordinators already run squads event-wide; granting the role is the other half."""
    event.coordinator_role_ids = [555]
    event.save(update_fields=["coordinator_role_ids"])
    coord = user_model.objects.create_user(
        username="coord", email="coord@example.test",
        permission_overrides={"team_member": True},        # no assign_roles
        discord_roles={"555": "EMEA Coordinator"},
    )
    client.force_login(coord)

    assert client.get(reverse("events:discord_roles", args=[event.pk])).status_code == 200


@pytest.mark.django_db
def test_a_plain_team_member_still_cannot(client, event, user_model) -> None:
    event.coordinator_role_ids = [555]
    event.save(update_fields=["coordinator_role_ids"])
    plain = user_model.objects.create_user(
        username="plain2", email="plain2@example.test",
        permission_overrides={"team_member": True},
    )
    client.force_login(plain)

    assert client.get(reverse("events:discord_roles", args=[event.pk])).status_code == 403

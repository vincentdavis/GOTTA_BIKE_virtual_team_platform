"""The manage-squads cards' Stats section: zMAP, zMAP W/kg and vELO 2 across a squad's riders."""

import html
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventSignup, Squad, SquadMember
from apps.events.squad_stats import squad_stats
from apps.rider_data.models import RiderProfile


def _text(markup: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def _rows(body: str, squad: Squad) -> dict[str, list[str]]:
    """Read a squad's Stats table.

    Returns:
        Each row's label mapped to its cells' text, in column order.

    """
    block = re.search(rf'<div id="squad-stats-{squad.pk}"[^>]*>(.*?)</table>', body, re.S)
    assert block, "no Stats table for the squad"
    rows = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", block.group(1), re.S):
        cells = [_text(cell) for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
        rows[cells[0]] = cells[1:]
    return rows


class _Profile:
    def __init__(self, race):
        self.velo2 = {"race": race}


def test_each_figure_counts_only_the_riders_who_have_it():
    stats = squad_stats([
        {"z_map": Decimal("400.0"), "z_map_wkg": 5.0, "rider_profile": _Profile(600.4)},
        {"z_map": Decimal("330.0"), "z_map_wkg": 5.5, "rider_profile": _Profile(700)},
        {"z_map": Decimal("350.0"), "z_map_wkg": 5.0, "rider_profile": None},
        {"z_map": None, "z_map_wkg": None, "rider_profile": _Profile(None)},
    ])

    assert [figure.label for figure in stats.figures] == ["zMAP", "zMAP W/kg", "vELO 2"]
    assert stats.counts == [3, 3, 2]
    assert stats.rider_count == 4
    assert {row["label"]: row["values"] for row in stats.rows} == {
        "Min": ["330", "5.00", "600"],
        "Max": ["400", "5.50", "700"],
        "Median": ["350", "5.00", "650"],
        "Mean": ["360", "5.17", "650"],
        "Range (max-min)": ["70", "0.50", "100"],
    }


def test_a_figure_no_rider_has_is_blank():
    stats = squad_stats([{"z_map": Decimal("320.0"), "z_map_wkg": None, "rider_profile": None}])

    assert stats.has_data
    assert stats.counts == [1, 0, 0]
    assert next(row["values"] for row in stats.rows) == ["320", "", ""]


def test_no_figures_at_all_has_no_data():
    assert not squad_stats([{"z_map": None, "z_map_wkg": None, "rider_profile": None}]).has_data
    assert not squad_stats([]).has_data


@pytest.fixture
def event(db) -> Event:
    """Build a visible event with signups open.

    Returns:
        The event under test.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True, signups_open=True
    )


@pytest.fixture
def admin(user_model):
    """Create a superuser, who can manage every squad.

    Returns:
        The user.

    """
    return user_model.objects.create_superuser(username="admin", email="admin@example.test")


def _rider(user_model, name: str, *, z_map=None, weight_g=None, zwid=None, velo2=None):
    user = user_model.objects.create_user(
        username=name,
        email=f"{name}@example.test",
        first_name=name.title(),
        last_name="R",
        zwid=zwid,
        z_map=z_map,
        z_metrics_weight_grams=weight_g,
        permission_overrides={"team_member": True},
    )
    if zwid and velo2 is not None:
        now = timezone.now()
        RiderProfile.objects.create(
            zwid=zwid, payload={"velo2": {"race": velo2}}, fetched_at=now, last_requested_at=now
        )
    return user


@pytest.mark.django_db
def test_each_card_shows_its_squads_stats(client, event, admin, user_model):
    alpha = Squad.objects.create(event=event, name="Alpha")
    bravo = Squad.objects.create(event=event, name="Bravo")
    riders = [
        _rider(user_model, "ana", z_map=Decimal("400"), weight_g=80000, zwid=9001, velo2=600),
        _rider(user_model, "bo", z_map=Decimal("330"), weight_g=60000, zwid=9002, velo2=700),
        _rider(user_model, "cy", z_map=Decimal("350"), weight_g=70000),
        _rider(user_model, "di"),
    ]
    for rider in riders:
        SquadMember.objects.create(squad=alpha, user=rider, status=SquadMember.Status.MEMBER)

    client.force_login(admin)
    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    assert _rows(body, alpha) == {
        "Statistic": ["zMAP", "zMAP W/kg", "vELO 2"],
        "Min": ["330", "5.00", "600"],
        "Max": ["400", "5.50", "700"],
        "Median": ["350", "5.00", "650"],
        "Mean": ["360", "5.17", "650"],
        "Range (max-min)": ["70", "0.50", "100"],
        "Riders": ["3 of 4", "3 of 4", "2 of 4"],
    }
    # A squad with nobody in it says so rather than drawing an empty table.
    empty = re.search(rf'<div id="squad-stats-{bravo.pk}"[^>]*>(.*?)</div>', body, re.S)
    assert _text(empty.group(1)) == "No riders yet."


@pytest.mark.django_db
def test_adding_a_rider_refreshes_the_stats(client, event, admin, user_model):
    squad = Squad.objects.create(event=event, name="Alpha")
    SquadMember.objects.create(
        squad=squad,
        user=_rider(user_model, "ana", z_map=Decimal("400"), weight_g=80000),
        status=SquadMember.Status.MEMBER,
    )
    newcomer = _rider(user_model, "bo", z_map=Decimal("300"), weight_g=60000)
    signup = EventSignup.objects.create(event=event, user=newcomer)

    client.force_login(admin)
    manage_url = reverse("events:squad_manage", args=[event.pk])
    response = client.post(
        reverse("events:squad_assign", args=[event.pk]),
        {"signup_id": signup.pk, "squad_id": squad.pk},
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL=f"http://testserver{manage_url}",
    )
    body = response.content.decode()

    assert f'<div id="squad-stats-{squad.pk}" hx-swap-oob="true">' in body
    rows = _rows(body, squad)
    assert rows["Min"][0] == "300"
    assert rows["Max"][0] == "400"
    assert rows["Riders"] == ["2 of 2", "2 of 2", "0 of 2"]

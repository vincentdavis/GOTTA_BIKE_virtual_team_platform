"""The By Category tab: each squad's members counted on three scales from the rider profile cache.

Zwift's own category, vELO 1's tier and vELO 2's (``rider_stats.category_tables``). Every rider is
counted once by their mixed category and every woman once more by her women's, which the page
shows after a slash: "2 / 1" is two riders by the mixed scale and one woman by the women's.
"""

import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse

from apps.events import rider_stats
from apps.events.models import Event, Squad, SquadMember


def _squad(*members, pk=1):
    return SimpleNamespace(pk=pk, enriched_members=[{"rider_profile": p, "gender": g} for p, g in members])


def _table(tables, key):
    return next(table for table in tables if table["key"] == key)


def _cell(table, category, row=0):
    return table["rows"][row]["cells"][table["columns"].index(category)]


@pytest.fixture
def riders(rider_profile_factory):
    """Two men and a woman, with invented categories on every scale.

    Returns:
        Their profiles.

    """
    man_a = rider_profile_factory(
        category_open="B",
        category_racing="Gold",
        velo=1500.0,
        zwift_racing_score=400.0,
        velo2={"race": 600.0, "category": "Gold"},
    )
    man_b = rider_profile_factory(
        category_open="B",
        category_racing="Silver",
        velo=1300.0,
        zwift_racing_score=380.0,
        velo2={"race": 500.0, "category": "Silver"},
    )
    woman = rider_profile_factory(
        gender="F", category_open="C", category_women="B", category_racing="Silver", velo=1200.0,
        zwift_racing_score=330.0, category={"racing_women": "Gold"},
        velo2={"race": 450.0, "category": "Bronze", "category_women": "Silver"},
    )  # fmt: skip
    return man_a, man_b, woman


@pytest.mark.django_db
def test_every_rider_counts_by_mixed_and_every_woman_again_by_womens(riders):
    man_a, man_b, woman = riders
    tables = rider_stats.category_tables([_squad((man_a, "male"), (man_b, "male"), (woman, "female"))])

    assert [table["label"] for table in tables] == ["Zwift", "vELO 1", "vELO 2"]
    zwift, velo1, velo2 = tables
    assert (_cell(zwift, "B"), _cell(zwift, "C")) == ((2, 1), (1, 0))
    assert (_cell(velo1, "Gold"), _cell(velo1, "Silver")) == ((1, 1), (2, 0))
    assert (_cell(velo2, "Gold"), _cell(velo2, "Silver"), _cell(velo2, "Bronze")) == ((1, 0), (1, 1), (1, 0))
    for table in tables:
        assert table["rows"][0]["total"] == (3, 1)  # three riders, one of them a woman
        assert table["rows"][0]["none"] == (0, 0)
        assert table["has_women"] is True
    # Whole-number averages of each scale's own score.
    assert (zwift["rows"][0]["avg"], velo1["rows"][0]["avg"], velo2["rows"][0]["avg"]) == (370, 1333, 517)


@pytest.mark.django_db
def test_a_rider_with_no_category_counts_under_none(rider_profile_factory):
    """No profile at all, or none on that scale: the rider still counts, under None."""
    unrated = rider_profile_factory(category_racing="")
    tables = rider_stats.category_tables([_squad((None, "female"), (unrated, "male"))])

    velo1 = _table(tables, "velo1")
    assert velo1["rows"][0]["none"] == (2, 1)  # both riders; the woman again, with no women's tier
    assert velo1["rows"][0]["total"] == (2, 1)
    assert velo1["rows"][0]["avg"] == 1580  # the factory's rating; the rider with no profile has none


@pytest.mark.django_db
def test_a_mans_own_answer_beats_zauths(riders):
    """His zauth profile says F, but his own answer is male: no women's count."""
    _, _, woman = riders
    tables = rider_stats.category_tables([_squad((woman, "male"))])

    assert _cell(_table(tables, "velo1"), "Gold") == (0, 0)
    assert _table(tables, "velo1")["rows"][0]["total"] == (1, 0)
    assert _table(tables, "zwift")["has_women"] is False


@pytest.mark.django_db
def test_a_category_the_scale_does_not_list_gets_a_column(rider_profile_factory):
    """So no rider drops out of the counts."""
    odd = rider_profile_factory(category_open="X")
    zwift = _table(rider_stats.category_tables([_squad((odd, "male"))]), "zwift")

    assert zwift["columns"] == ["A+", "A", "B", "C", "D", "E", "X"]
    assert _cell(zwift, "X") == (1, 0)


# --- the page -------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_tab_counts_on_the_three_scales(client, event_admin, user_model, rider_profile_factory):
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True)
    squad = Squad.objects.create(event=event, name="Arawn")
    for username, gender, zwid in (("man", "male", 9100001), ("woman", "female", 9100002)):
        user = user_model.objects.create_user(
            username=username, email=f"{username}@example.test", gender=gender, zwid=zwid
        )
        SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    rider_profile_factory(zwid=9100001, category_racing="Gold")
    rider_profile_factory(zwid=9100002, gender="F", category_racing="Silver", category={"racing_women": "Gold"})
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()
    panel = body[body.index('data-tab-panel="category"') : body.index('data-tab-panel="races"')]

    assert re.findall(r'data-category-source="([a-z0-9]+)"', panel) == ["zwift", "velo1", "velo2"]
    assert '<div id="cat-table-zwift">' in panel
    assert '<div id="cat-table-velo1" hidden>' in panel
    assert "ZwiftPower" not in panel and "Avg Rank" not in panel
    velo1 = panel[panel.index('id="cat-table-velo1"') : panel.index('id="cat-table-velo2"')]
    assert "Each count is riders by mixed category / women by women's category." in velo1
    row = velo1[velo1.rindex("<tr", 0, velo1.index("Arawn")) :]
    cells = [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
    gold = 2 + rider_stats.CATEGORY_SCALES[1].order.index("Gold")  # after Squad and Timezone
    assert re.sub(r"\s+", " ", cells[gold]) == "1 / riders, 1 women's"

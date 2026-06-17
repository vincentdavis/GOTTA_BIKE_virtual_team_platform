"""Tests for the ladder planner: normalizer, scoring, comparisons, and opponent fetch."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from apps.ladder_planner.models import CourseProfile, LadderMatchup, LadderRider, Side
from apps.ladder_planner.services import compute, normalize, roster

# ----- fixtures / helpers ------------------------------------------------------------------------

API_RIDER = {
    "riderId": 87402,
    "name": "Test Rider [X]",
    "height": 170,
    "weight": 60.8,
    "zpCategory": "B",
    "zpFTP": 238,
    "power": {"wkg5": 12.6, "wkg60": 7.78, "wkg1200": 4.05, "w5": 761, "w60": 473, "w1200": 246},
    "race": {
        "current": {"rating": 1516.05, "mixed": {"category": "Sapphire", "number": 4}},
        "max30": {"rating": 1534.9},
        "max90": {"rating": 1573.4},
        "finishes": 18,
        "podiums": 7,
        "wins": 2,
    },
    "handicaps": {"profile": {"flat": 51.0, "rolling": 30.0, "hilly": -22.3, "mountainous": -61.4}},
    "velo": {
        "race": 624,
        "timeTrial": 600,
        "factors": {"endurance": 520, "pursuit": 594, "sprint": 731, "punch": 768, "climb": 628},
    },
    "phenotype": {"value": "Puncheur"},
}


def _zr_data(
    *,
    rating: float,
    handicaps: dict[str, float],
    wkg: dict[str, float] | None = None,
    w: dict[str, int] | None = None,
    name: str = "R",
) -> dict[str, Any]:
    """Build a minimal normalized rider dict for tests.

    Returns:
        A unified rider dict.

    """
    blank = normalize._blank()
    blank["name"] = name
    blank["rating_current"] = rating
    blank["handicaps"] = {**blank["handicaps"], **handicaps}
    if wkg:
        blank["wkg"] = {**blank["wkg"], **wkg}
    if w:
        blank["w"] = {**blank["w"], **w}
    return blank


def _make_matchup(user, **kwargs) -> LadderMatchup:
    defaults = {
        "created_by": user,
        "course_profile": CourseProfile.ROLLING,
        "our_team_name": "Us",
        "opponent_team_name": "Them",
    }
    defaults.update(kwargs)
    return LadderMatchup.objects.create(**defaults)


def _add(matchup, side, zr_data, *, racing=True, order=0):
    return LadderRider.objects.create(
        matchup=matchup,
        side=side,
        order=order,
        zwid=zr_data.get("zwid") or order + 1,
        name=zr_data["name"],
        zr_data=zr_data,
        is_racing=racing,
    )


# ----- normalizer --------------------------------------------------------------------------------


def test_from_api_flattens_nested_payload():
    data = normalize.from_api(API_RIDER)
    assert data["zwid"] == 87402
    assert data["weight_kg"] == pytest.approx(60.8)
    assert data["height_cm"] == 170
    assert data["zp_ftp"] == 238
    assert data["rating_current"] == pytest.approx(1516.05)
    assert data["rating_max90"] == pytest.approx(1573.4)
    assert data["handicaps"]["rolling"] == pytest.approx(30.0)
    assert data["w"]["60"] == 473
    assert data["wkg"]["1200"] == pytest.approx(4.05)
    assert data["phenotype"] == "Puncheur"
    # Missing durations are present as None, not absent.
    assert data["w"]["15"] is None
    # vELO2 discipline scores, rank and race stats.
    assert data["rank"] == "Sapphire"
    assert data["velo"]["race"] == pytest.approx(624)
    assert data["velo"]["punch"] == pytest.approx(768)
    assert data["velo"]["time_trial"] == pytest.approx(600)
    assert data["podiums"] == 7
    assert data["finishes"] == 18


def test_from_api_handles_missing_sections():
    data = normalize.from_api({"riderId": 1, "name": "Bare"})
    assert data["zwid"] == 1
    assert data["rating_current"] is None
    assert all(v is None for v in data["handicaps"].values())
    assert all(v is None for v in data["w"].values())


@pytest.mark.django_db
def test_from_zrrider_reads_model_fields():
    from apps.zwiftracing.models import ZRRider

    rider = ZRRider.objects.create(
        zwid=555,
        name="ZR Rider",
        weight=70,
        height=180,
        zp_ftp=300,
        zp_category="A",
        race_current_rating=1700,
        race_current_category="Emerald",
        race_finishes=20,
        race_podiums=5,
        handicap_rolling=25,
        power_w60=400,
        power_wkg60="5.7",
        phenotype_value="Sprinter",
        velo_race=662,
        velo_sprint=879,
    )
    data = normalize.from_zrrider(rider)
    assert data["zwid"] == 555
    assert data["rating_current"] == pytest.approx(1700.0)
    assert data["handicaps"]["rolling"] == pytest.approx(25.0)
    assert data["w"]["60"] == 400
    assert data["wkg"]["60"] == pytest.approx(5.7)
    assert data["phenotype"] == "Sprinter"
    assert data["rank"] == "Emerald"
    assert data["velo"]["race"] == pytest.approx(662)
    assert data["velo"]["sprint"] == pytest.approx(879)
    assert data["podiums"] == 5
    assert data["finishes"] == 20


# ----- projected score ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_projected_score_ranks_by_handicapped_velo(team_member):
    matchup = _make_matchup(team_member)
    # ours: 1500 + 50 = 1550 ; theirs: 1600 - 100 = 1500
    _add(matchup, Side.OURS, _zr_data(rating=1500, handicaps={"rolling": 50}, name="Ours"))
    _add(matchup, Side.OPPONENT, _zr_data(rating=1600, handicaps={"rolling": -100}, name="Theirs"))

    result = compute.projected_score(matchup)
    rows = result["rows"]
    assert [r["name"] for r in rows] == ["Ours", "Theirs"]  # ours sorts first (1550 > 1500)
    assert rows[0]["finish"] == 1 and rows[0]["points"] == 2
    assert rows[1]["finish"] == 2 and rows[1]["points"] == 1
    assert result["our_points"] == 2
    assert result["opp_points"] == 1
    assert result["favored"] == "Us"


@pytest.mark.django_db
def test_projected_score_profile_changes_ranking(team_member):
    matchup = _make_matchup(team_member, course_profile=CourseProfile.MOUNTAINOUS)
    # On mountainous: ours 1500-200=1300 ; theirs 1450+100=1550 -> theirs favored
    _add(matchup, Side.OURS, _zr_data(rating=1500, handicaps={"rolling": 50, "mountainous": -200}, name="Ours"))
    _add(matchup, Side.OPPONENT, _zr_data(rating=1450, handicaps={"rolling": -100, "mountainous": 100}, name="Theirs"))

    result = compute.projected_score(matchup)
    assert result["rows"][0]["name"] == "Theirs"
    assert result["favored"] == "Them"


@pytest.mark.django_db
def test_projected_score_excludes_non_racing_and_unrated(team_member):
    matchup = _make_matchup(team_member)
    _add(matchup, Side.OURS, _zr_data(rating=1500, handicaps={"rolling": 0}, name="Racing"))
    _add(matchup, Side.OURS, _zr_data(rating=1900, handicaps={"rolling": 0}, name="Benched"), racing=False, order=1)
    unrated = _zr_data(rating=1500, handicaps={"rolling": 0}, name="Unrated")
    unrated["rating_current"] = None
    _add(matchup, Side.OPPONENT, unrated)

    result = compute.projected_score(matchup)
    assert [r["name"] for r in result["rows"]] == ["Racing"]


# ----- power comparison --------------------------------------------------------------------------


@pytest.mark.django_db
def test_power_comparison_aggregates_and_advantage(team_member):
    matchup = _make_matchup(team_member)
    _add(matchup, Side.OURS, _zr_data(rating=1, handicaps={}, wkg={"60": 6.0}, name="O1"))
    _add(matchup, Side.OURS, _zr_data(rating=1, handicaps={}, wkg={"60": 4.0}, name="O2"), order=1)
    _add(matchup, Side.OPPONENT, _zr_data(rating=1, handicaps={}, wkg={"60": 5.0}, name="T1"))

    power = compute.power_comparison(matchup)
    wkg_table = next(t for t in power["tables"] if t["domain"] == "wkg")
    idx_1m = power["durations"].index("1m")
    by_label = {m["label"]: m for m in wkg_table["metrics"]}
    assert by_label["Average"]["ours"][idx_1m] == pytest.approx(5.0)  # mean(6,4)
    assert by_label["Max"]["ours"][idx_1m] == pytest.approx(6.0)
    assert by_label["Min"]["ours"][idx_1m] == pytest.approx(4.0)
    assert by_label["Average"]["opp"][idx_1m] == pytest.approx(5.0)
    assert by_label["Average"]["adv"][idx_1m] == pytest.approx(0.0)  # 5.0 - 5.0
    assert by_label["Max"]["adv"][idx_1m] == pytest.approx(1.0)  # 6.0 - 5.0
    # charts expose average + median series
    assert "wkg_average" in power["charts"]
    assert power["charts"]["wkg_average"]["ours"][idx_1m] == pytest.approx(5.0)


@pytest.mark.django_db
def test_top_riders_picks_best_across_sides(team_member):
    matchup = _make_matchup(team_member)
    _add(matchup, Side.OURS, _zr_data(rating=1, handicaps={}, w={"5": 700}, name="OurSprinter"))
    _add(matchup, Side.OPPONENT, _zr_data(rating=1, handicaps={}, w={"5": 900}, name="TheirSprinter"))

    groups = compute.top_riders(matchup)
    watts = next(g for g in groups if g["domain_label"] == "Power: Raw Watts")
    row_5s = next(r for r in watts["rows"] if r["duration"] == "5s")
    assert row_5s["name"] == "TheirSprinter"
    assert row_5s["value"] == 900


# ----- vELO2 & other stats -----------------------------------------------------------------------


@pytest.mark.django_db
def test_velo2_comparison_per_rider_and_advantage(team_member):
    matchup = _make_matchup(team_member)
    ours = _zr_data(rating=1, handicaps={}, name="Ours")
    ours["velo"] = {**ours["velo"], "race": 600, "sprint": 700}
    ours["rank"] = "Emerald"
    opp = _zr_data(rating=1, handicaps={}, name="Theirs")
    opp["velo"] = {**opp["velo"], "race": 500, "sprint": 900}
    _add(matchup, Side.OURS, ours)
    _add(matchup, Side.OPPONENT, opp)

    velo2 = compute.velo2_comparison(matchup)
    assert velo2["disciplines"][0] == "Race Score"
    race_idx = velo2["disciplines"].index("Race Score")
    sprint_idx = velo2["disciplines"].index("Sprint")
    # per-rider rows carry rank + heatmap cells
    our_row = next(r for r in velo2["rows"] if r["name"] == "Ours")
    assert our_row["rank"] == "Emerald"
    assert our_row["cells"][race_idx]["value"] == 600
    # advantage = ours - opp
    avg = next(m for m in velo2["metrics"] if m["label"] == "Average")
    assert avg["adv"][race_idx] == 100  # 600 - 500
    assert avg["adv"][sprint_idx] == -200  # 700 - 900


@pytest.mark.django_db
def test_other_stats_derived_columns(team_member):
    matchup = _make_matchup(team_member)
    d = _zr_data(rating=1564, handicaps={}, name="Ours")
    d["weight_kg"] = 60.0
    d["height_cm"] = 170
    d["zp_ftp"] = 220
    d["zp_category"] = "B"
    d["rank"] = "Sapphire"
    d["phenotype"] = "Puncheur"
    d["finishes"] = 20
    d["podiums"] = 5
    d["rating_max30"] = 1591
    d["rating_max90"] = 1607
    _add(matchup, Side.OURS, d)

    row = compute.other_stats(matchup)["rows"][0]
    assert row["weight_lb"] == 132  # round(60 * 2.20462)
    assert row["height_ft"] == pytest.approx(5.58)  # round(170 / 30.48, 2)
    assert row["zftp_wkg"] == pytest.approx(3.7)  # round(220 / 60, 1)
    assert row["podium_pct"] == 25  # round(100 * 5 / 20)
    assert row["category"] == "B"
    assert row["rank"] == "Sapphire"
    assert row["zrapp_curr"] == 1564
    assert row["zrapp_90"] == 1607


# ----- opponent fetch ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_opponents_normalizes_list(monkeypatch):
    monkeypatch.setattr(roster.zr_client, "get_riders", lambda zwids: (200, [API_RIDER]))
    riders, error = roster.fetch_opponents([87402])
    assert error is None
    assert len(riders) == 1
    assert riders[0]["zwid"] == 87402


@pytest.mark.django_db
def test_fetch_opponents_handles_rate_limit(monkeypatch):
    monkeypatch.setattr(roster.zr_client, "get_riders", lambda zwids: (429, {"retryAfter": "597"}))
    riders, error = roster.fetch_opponents([1])
    assert riders == []
    assert "rate limited" in error
    assert "597" in error


@pytest.mark.django_db
def test_fetch_opponents_handles_http_error(monkeypatch):
    def _raise(zwids):
        raise httpx.HTTPStatusError("boom", request=httpx.Request("POST", "http://x"), response=httpx.Response(500))

    monkeypatch.setattr(roster.zr_client, "get_riders", _raise)
    riders, error = roster.fetch_opponents([1])
    assert riders == []
    assert "500" in error


# ----- view smoke --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_opponents_add_view_creates_riders(auth_client, team_member, monkeypatch):
    matchup = _make_matchup(team_member)
    monkeypatch.setattr(roster, "fetch_opponents", lambda zwids: ([normalize.from_api(API_RIDER)], None))

    resp = auth_client.post(f"/ladder/{matchup.pk}/opponents/add/", {"zwids": "87402"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert matchup.riders.filter(side=Side.OPPONENT, zwid=87402).exists()


@pytest.mark.django_db
def test_detail_page_renders(auth_client, team_member):
    matchup = _make_matchup(team_member)
    resp = auth_client.get(f"/ladder/{matchup.pk}/")
    assert resp.status_code == 200
    assert b"Projected Score" in resp.content

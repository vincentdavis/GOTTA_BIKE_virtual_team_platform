"""Tests for the ladder planner: normalizer, scoring, comparisons, and opponent fetch."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from django.utils import timezone

from apps.events.models import Event, Squad, SquadMember
from apps.ladder_planner import tasks as lp_tasks
from apps.ladder_planner import views as lp_views
from apps.ladder_planner.models import CachedClub, CachedRider, CourseProfile, LadderMatchup, LadderRider, Side
from apps.ladder_planner.services import cache, compute, courses, normalize, roster, squads
from apps.zwiftracing.models import ZRRider

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
    assert rows[0]["finish"] == 1 and rows[0]["points"] == 10
    assert rows[1]["finish"] == 2 and rows[1]["points"] == 9
    assert result["our_points"] == 10
    assert result["opp_points"] == 9
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


@pytest.mark.django_db
def test_matchup_list_default_order_most_recently_modified_first(auth_client, team_member):
    from datetime import timedelta

    from django.utils import timezone

    older = _make_matchup(team_member, name="Older")
    newer = _make_matchup(team_member, name="Newer")
    # auto_now stamps both near-identically; force a clear gap via .update (bypasses auto_now).
    now = timezone.now()
    LadderMatchup.objects.filter(pk=older.pk).update(updated_at=now - timedelta(days=2))
    LadderMatchup.objects.filter(pk=newer.pk).update(updated_at=now)

    body = auth_client.get("/ladder/").content.decode()
    assert body.index("Newer") < body.index("Older")


@pytest.mark.django_db
def test_matchup_list_sort_by_name_ascending(auth_client, team_member):
    _make_matchup(team_member, name="Bravo")
    _make_matchup(team_member, name="Alpha")

    body = auth_client.get("/ladder/?my_sort=name&my_dir=asc").content.decode()
    assert body.index("Alpha") < body.index("Bravo")

    body_desc = auth_client.get("/ladder/?my_sort=name&my_dir=desc").content.decode()
    assert body_desc.index("Bravo") < body_desc.index("Alpha")


@pytest.mark.django_db
def test_matchup_list_unknown_sort_falls_back_to_updated(auth_client, team_member):
    from datetime import timedelta

    from django.utils import timezone

    older = _make_matchup(team_member, name="Older")
    newer = _make_matchup(team_member, name="Newer")
    now = timezone.now()
    LadderMatchup.objects.filter(pk=older.pk).update(updated_at=now - timedelta(days=2))
    LadderMatchup.objects.filter(pk=newer.pk).update(updated_at=now)

    # Bogus column + direction must not error and must keep the recency default.
    body = auth_client.get("/ladder/?my_sort=bogus&my_dir=sideways").content.decode()
    assert body.index("Newer") < body.index("Older")


@pytest.mark.django_db
def test_matchup_lists_sort_independently(auth_client, team_member, user_model):
    other_user = user_model.objects.create(username="ladder_other", discord_nickname="Zed")
    _make_matchup(team_member, name="MyBravo")
    _make_matchup(team_member, name="MyAlpha")
    _make_matchup(other_user, name="OtherBravo")
    _make_matchup(other_user, name="OtherAlpha")

    # Sort only my list by name asc; the other list keeps its own (default) order.
    body = auth_client.get("/ladder/?my_sort=name&my_dir=asc").content.decode()
    mine = body.split("Other team members' matchups", 1)[0]
    assert mine.index("MyAlpha") < mine.index("MyBravo")
    # My list's header links must preserve the other list's namespaced sort params.
    assert "other_sort=" in mine


@pytest.mark.django_db
def test_matchup_list_headers_are_sortable_for_both_lists(auth_client, team_member, user_model):
    other_user = user_model.objects.create(username="ladder_other2", discord_nickname="Zed")
    _make_matchup(team_member, name="Mine")
    _make_matchup(other_user, name="Theirs")

    body = auth_client.get("/ladder/").content.decode()
    assert "my_sort=name" in body
    assert "my_sort=updated" in body
    assert "other_sort=name" in body
    assert "other_sort=created_by" in body


@pytest.mark.django_db
def test_velo2_comparison_advantage_row_is_column_aligned(auth_client, team_member):
    from django.urls import reverse

    matchup = _make_matchup(team_member)
    _add(matchup, Side.OURS, _zr_data(rating=1500, handicaps={"rolling": 50}, name="Ours"))
    _add(matchup, Side.OPPONENT, _zr_data(rating=1600, handicaps={"rolling": -100}, name="Theirs"))
    body = auth_client.get(reverse("ladder_planner:detail", args=[matchup.pk])).content.decode()

    import re

    # Isolate the "vELO2 comparison" table.
    table = body.split("vELO2 comparison", 1)[1].split("<table", 1)[1].split("</table>", 1)[0]
    # `<th[ >]` avoids also matching the surrounding `<thead>` tag.
    header_cols = len(re.findall(r"<th[ >]", table.split("</thead>", 1)[0]))
    advantage_row = next(r for r in table.split("<tr") if "Advantage" in r)
    advantage_cells = len(re.findall(r"<td[ >]", advantage_row))
    # Advantage carries the label + every discipline column, i.e. all columns
    # except the row-spanned Metric column. (The old layout left it one short,
    # shifting the row a column to the left.)
    assert header_cols >= 3
    assert advantage_cells == header_cols - 1


@pytest.mark.django_db
@pytest.mark.parametrize("domain_label", ["Power: w/kg", "Power: Raw Watts"])
def test_power_comparison_advantage_row_is_column_aligned(auth_client, team_member, domain_label):
    from django.urls import reverse

    matchup = _make_matchup(team_member)
    _add(matchup, Side.OURS, _zr_data(rating=1500, handicaps={"rolling": 50}, name="Ours"))
    _add(matchup, Side.OPPONENT, _zr_data(rating=1600, handicaps={"rolling": -100}, name="Theirs"))
    body = auth_client.get(reverse("ladder_planner:detail", args=[matchup.pk])).content.decode()

    import re

    # Isolate the power table for this domain (w/kg or Raw Watts).
    table = body.split(domain_label, 1)[1].split("<table", 1)[1].split("</table>", 1)[0]
    # `<th[ >]` avoids also matching the surrounding `<thead>` tag.
    header_cols = len(re.findall(r"<th[ >]", table.split("</thead>", 1)[0]))
    advantage_row = next(r for r in table.split("<tr") if "Advantage" in r)
    advantage_cells = len(re.findall(r"<td[ >]", advantage_row))
    # Advantage carries the label + every duration column, i.e. all columns
    # except the row-spanned Metric column. (The old layout left it one short,
    # shifting the row a column to the left.)
    assert header_cols >= 3
    assert advantage_cells == header_cols - 1


@pytest.mark.django_db
def test_velo2_and_other_stats_tables_have_sortable_headers(auth_client, team_member):
    matchup = _make_matchup(team_member)
    _add(matchup, Side.OURS, _zr_data(rating=1500, handicaps={"rolling": 50}, name="Ours"))
    _add(matchup, Side.OPPONENT, _zr_data(rating=1600, handicaps={"rolling": -100}, name="Theirs"))
    body = auth_client.get(f"/ladder/{matchup.pk}/").content.decode()

    # The generic sort helper is defined and headers are wired to it.
    assert "window.sortLadderTable = function" in body
    assert 'onclick="window.sortLadderTable(this)"' in body
    assert "vELO2 scores" in body
    assert ">Phenotype<" in body  # Other Stats only column
    # Both tables: Other Stats has 15 headers + vELO2 scores has 3 fixed headers.
    assert body.count('onclick="window.sortLadderTable(this)"') >= 18


# ----- cache layer -------------------------------------------------------------------------------


def _api_rider(zwid: int, *, club_id: int = 11991, club_name: str = "RIVALS") -> dict:
    """Build a minimal ZR API rider payload with a club.

    Returns:
        A ZR API rider dict.

    """
    return {
        "riderId": zwid,
        "name": f"Rider {zwid}",
        "weight": 70,
        "club": {"id": club_id, "name": club_name},
        "power": {"w60": 400, "wkg60": 5.7},
        "race": {"current": {"rating": 1500, "mixed": {"category": "Sapphire"}}},
    }


@pytest.mark.django_db
def test_cache_upsert_get_snapshot_and_club_tracking():
    cache.upsert_riders([normalize.from_api(_api_rider(101))], source=CachedRider.Source.CLUB)
    assert CachedRider.objects.filter(zwid=101).exists()
    snap = cache.get_snapshot(101)
    assert snap and snap["zwid"] == 101 and snap["w"]["60"] == 400
    # The rider's club is now tracked for the background refresh.
    assert CachedClub.objects.filter(club_id=11991, auto_refresh=True).exists()
    assert cache.get_snapshot(999) is None


@pytest.mark.django_db
def test_cache_search_reports_age():
    cache.upsert_riders([normalize.from_api(_api_rider(202, club_name="ACME"))], source=CachedRider.Source.RIDER)
    CachedRider.objects.filter(zwid=202).update(fetched_at=timezone.now() - timedelta(days=3))
    results = cache.search("Rider 202")
    assert len(results) == 1
    assert results[0]["zwid"] == 202
    assert results[0]["club_name"] == "ACME"
    assert results[0]["age_days"] == 3


@pytest.mark.django_db
def test_fetch_opponents_writes_through_cache(monkeypatch):
    monkeypatch.setattr(roster.zr_client, "get_riders", lambda zwids: (200, [_api_rider(303)]))
    riders, error = roster.fetch_opponents([303])
    assert error is None and riders
    assert CachedRider.objects.filter(zwid=303).exists()


@pytest.mark.django_db
def test_warm_club_caches_full_roster(monkeypatch):
    payload = {"clubId": 11991, "name": "RIVALS", "riders": [_api_rider(401), _api_rider(402)]}
    monkeypatch.setattr(lp_tasks, "get_club", lambda club_id, from_id: (200, payload))

    result = lp_tasks.warm_club.func(11991)
    assert result["status"] == "complete"
    assert result["cached"] == 2
    assert CachedRider.objects.filter(zwid__in=[401, 402], source=CachedRider.Source.CLUB).count() == 2
    club = CachedClub.objects.get(club_id=11991)
    assert club.rider_count == 2
    assert club.last_refreshed_at is not None


@pytest.mark.django_db
def test_warm_club_rate_limited_reenqueues(monkeypatch):
    monkeypatch.setattr(lp_tasks, "get_club", lambda club_id, from_id: (429, {"retryAfter": "597"}))
    # Task objects are frozen; patch the module symbol the task body looks up.
    real = lp_tasks.warm_club.func
    captured = []
    fake = SimpleNamespace(func=real, using=lambda **kw: SimpleNamespace(enqueue=lambda *a: captured.append(a)))
    monkeypatch.setattr(lp_tasks, "warm_club", fake)

    result = real(11991)
    assert result["status"] == "rate_limited"
    assert captured  # re-enqueued for a later retry
    assert not CachedRider.objects.exists()


@pytest.mark.django_db
def test_refresh_cached_clubs_enqueues_due_active_clubs(team_member, monkeypatch):
    matchup = _make_matchup(team_member)
    _add(matchup, Side.OPPONENT, {**normalize.from_api(_api_rider(501, club_id=777)), "name": "Opp"})
    CachedClub.objects.create(club_id=777, auto_refresh=True, last_refreshed_at=None)
    # A stale club that is NOT referenced by any matchup must be skipped.
    CachedClub.objects.create(club_id=888, auto_refresh=True, last_refreshed_at=None)

    enqueued = []
    monkeypatch.setattr(lp_tasks, "warm_club", SimpleNamespace(enqueue=lambda cid: enqueued.append(cid)))

    result = lp_tasks.refresh_cached_clubs.func()
    assert result["enqueued"] == 1
    assert enqueued == [777]


@pytest.mark.django_db
def test_refresh_cached_clubs_skips_recently_refreshed(team_member, monkeypatch):
    matchup = _make_matchup(team_member)
    _add(matchup, Side.OPPONENT, {**normalize.from_api(_api_rider(601, club_id=777)), "name": "Opp"})
    CachedClub.objects.create(club_id=777, auto_refresh=True, last_refreshed_at=timezone.now())

    enqueued = []
    monkeypatch.setattr(lp_tasks, "warm_club", SimpleNamespace(enqueue=lambda cid: enqueued.append(cid)))

    result = lp_tasks.refresh_cached_clubs.func()
    assert result["enqueued"] == 0
    assert enqueued == []


@pytest.mark.django_db
def test_opponent_add_uses_cache_without_live_call(auth_client, team_member, monkeypatch):
    matchup = _make_matchup(team_member)
    cache.upsert_riders([normalize.from_api(_api_rider(701))], source=CachedRider.Source.CLUB)

    def _no_live(_zwids):
        raise AssertionError("live fetch should not happen on a cache hit")

    monkeypatch.setattr(roster, "fetch_opponents", _no_live)
    resp = auth_client.post(f"/ladder/{matchup.pk}/opponents/add/701/", HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert matchup.riders.filter(side=Side.OPPONENT, zwid=701).exists()


# ----- course / route picker ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("distance_km", "elevation_m", "expected"),
    [
        (28.21, 132, CourseProfile.FLAT),  # ~4.7 m/km
        (20.0, 200, CourseProfile.ROLLING),  # 10 m/km
        (10.0, 200, CourseProfile.HILLY),  # 20 m/km
        (8.05, 236, CourseProfile.MOUNTAINOUS),  # ~29 m/km
        (0, 100, CourseProfile.ROLLING),  # unknown distance -> default
    ],
)
def test_derive_profile_from_climbing_density(distance_km, elevation_m, expected):
    assert courses.derive_profile(distance_km, elevation_m) == expected


@pytest.mark.django_db
def test_route_options_includes_derived_profile():
    from apps.ttt_planner.models import Route

    Route.objects.create(name="Flatland", distance_km=30, elevation_m=120)
    Route.objects.create(name="Climby", distance_km=10, elevation_m=300)
    by_name = {o["name"]: o for o in courses.route_options()}
    assert by_name["Flatland"]["profile"] == CourseProfile.FLAT
    assert by_name["Climby"]["profile"] == CourseProfile.MOUNTAINOUS


@pytest.mark.django_db
def test_matchup_update_sets_route(auth_client, team_member):
    from apps.ttt_planner.models import Route

    matchup = _make_matchup(team_member)
    route = Route.objects.create(name="Bon Voyage", distance_km=28.21, elevation_m=132)
    resp = auth_client.post(
        f"/ladder/{matchup.pk}/update/",
        {"route": route.pk, "course_name": "Bon Voyage", "course_profile": "flat"},
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 200
    matchup.refresh_from_db()
    assert matchup.route_id == route.pk
    assert matchup.course_name == "Bon Voyage"
    assert matchup.course_profile == "flat"


@pytest.mark.django_db
def test_opponent_add_cache_miss_fetches_live(auth_client, team_member, monkeypatch):
    matchup = _make_matchup(team_member)
    monkeypatch.setattr(roster, "fetch_opponents", lambda zwids: ([normalize.from_api(_api_rider(801))], None))
    warmed = []
    monkeypatch.setattr(lp_views, "warm_club", SimpleNamespace(enqueue=lambda cid: warmed.append(cid)))

    resp = auth_client.post(f"/ladder/{matchup.pk}/opponents/add/801/", HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert matchup.riders.filter(side=Side.OPPONENT, zwid=801).exists()
    assert warmed == [11991]  # cache-miss fetch warms the rider's club in the background


# ----- squad picker ------------------------------------------------------------------------------


def _event(title, *, days_to_end=7, visible=True):
    """Create an event ending `days_to_end` days from today.

    Returns:
        The created Event.

    """
    today = timezone.now().date()
    return Event.objects.create(
        title=title, start_date=today - timedelta(days=1), end_date=today + timedelta(days=days_to_end), visible=visible
    )


@pytest.mark.django_db
def test_squads_for_picker_groups_and_sorts(team_member, user_model):
    active = _event("Spring Series")
    past = _event("Old Event", days_to_end=-2)
    Squad.objects.create(event=active, name="A Squad")
    b_squad = Squad.objects.create(event=active, name="B Squad")
    Squad.objects.create(event=past, name="Z Squad")  # excluded (event ended)
    b_squad.captains.add(team_member)  # team_member belongs to B Squad as captain

    mine, other = squads.squads_for_picker(team_member)
    assert [s["label"] for s in mine] == ["Spring Series — B Squad"]
    assert [s["label"] for s in other] == ["Spring Series — A Squad"]


@pytest.mark.django_db
def test_squad_member_users_unions_roles(team_member, user_model):
    event = _event("Series")
    squad = Squad.objects.create(event=event, name="Alpha")
    cap = user_model.objects.create(username="cap", zwid=1)
    member = user_model.objects.create(username="mem", zwid=2)
    squad.captains.add(cap)
    SquadMember.objects.create(squad=squad, user=member, status=SquadMember.Status.MEMBER)

    users = squads.squad_member_users(squad)
    assert {u.pk for u in users} == {cap.pk, member.pk}


@pytest.mark.django_db
def test_our_squad_add_adds_members(auth_client, team_member, user_model):
    matchup = _make_matchup(team_member)
    event = _event("Series")
    squad = Squad.objects.create(event=event, name="Alpha")

    synced = user_model.objects.create(username="synced", zwid=4001, first_name="Syn")
    ZRRider.objects.create(zwid=4001, name="Synced Rider", race_current_rating=1600, power_w60=380)
    unsynced = user_model.objects.create(username="unsynced", zwid=4002, first_name="Uns")
    no_zwid = user_model.objects.create(username="nozwid")
    for u in (synced, unsynced, no_zwid):
        SquadMember.objects.create(squad=squad, user=u, status=SquadMember.Status.MEMBER)

    resp = auth_client.post(f"/ladder/{matchup.pk}/ours/add-squad/", {"squad": squad.pk}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    ours = matchup.riders.filter(side=Side.OURS)
    assert set(ours.values_list("zwid", flat=True)) == {4001, 4002}  # no_zwid skipped
    synced_rider = ours.get(zwid=4001)
    assert synced_rider.zr_data["rating_current"] == 1600  # snapshotted from ZR
    unsynced_rider = ours.get(zwid=4002)
    assert unsynced_rider.zr_data["rating_current"] is None  # name-only


@pytest.mark.django_db
def test_our_squad_add_dedupes(auth_client, team_member, user_model):
    matchup = _make_matchup(team_member)
    event = _event("Series")
    squad = Squad.objects.create(event=event, name="Alpha")
    u = user_model.objects.create(username="dup", zwid=5001)
    SquadMember.objects.create(squad=squad, user=u, status=SquadMember.Status.MEMBER)

    auth_client.post(f"/ladder/{matchup.pk}/ours/add-squad/", {"squad": squad.pk})
    auth_client.post(f"/ladder/{matchup.pk}/ours/add-squad/", {"squad": squad.pk})  # again
    assert matchup.riders.filter(side=Side.OURS, zwid=5001).count() == 1


# ----- climb advantage heatmap -------------------------------------------------------------------


@pytest.mark.django_db
def test_climb_advantage_available_and_favours_stronger(team_member):
    matchup = _make_matchup(team_member)
    strong_w = {"5": 1100, "15": 880, "30": 770, "60": 560, "120": 500, "300": 420, "1200": 360}
    weak_w = {k: v * 0.8 for k, v in strong_w.items()}

    for i in range(2):
        d = _zr_data(rating=1500, handicaps={}, w=strong_w, name=f"O{i}")
        d["weight_kg"], d["height_cm"] = 66, 178
        _add(matchup, Side.OURS, d, order=i)
    for i in range(2):
        d = _zr_data(rating=1500, handicaps={}, w=weak_w, name=f"T{i}")
        d["weight_kg"], d["height_cm"] = 72, 178  # weaker and heavier
        _add(matchup, Side.OPPONENT, d, order=i)

    climb = compute.climb_advantage(matchup)
    assert climb["available"] is True
    assert len(climb["lengths"]) == len(compute.CLIMB_LENGTHS_M)
    assert len(climb["rows"]) == len(compute.CLIMB_GRADES)
    assert all(len(row["cells"]) == len(compute.CLIMB_LENGTHS_M) for row in climb["rows"])
    # Our team is stronger and lighter, so we're faster (positive time gap) somewhere.
    assert any(c["advantage_s"] > 0 for row in climb["rows"] for c in row["cells"])


@pytest.mark.django_db
def test_matchup_update_sets_and_clears_cda_coef(auth_client, team_member):
    matchup = _make_matchup(team_member)

    auth_client.post(f"/ladder/{matchup.pk}/update/", {"cda_coef": "0.04"}, HTTP_HX_REQUEST="true")
    matchup.refresh_from_db()
    assert matchup.cda_coef == pytest.approx(0.04)

    auth_client.post(f"/ladder/{matchup.pk}/update/", {"cda_coef": ""}, HTTP_HX_REQUEST="true")
    matchup.refresh_from_db()
    assert matchup.cda_coef is None  # blank falls back to the global default


@pytest.mark.django_db
def test_climb_advantage_unavailable_without_data(team_member):
    matchup = _make_matchup(team_member)
    _add(matchup, Side.OURS, _zr_data(rating=1500, handicaps={}, name="O"))
    _add(matchup, Side.OPPONENT, _zr_data(rating=1500, handicaps={}, name="T"))
    assert compute.climb_advantage(matchup)["available"] is False


# ----- Event factors (vELO2 Race weights) --------------------------------------------------------


@pytest.mark.django_db
def test_event_factors_sorted_desc_and_nonzero_only(team_member):
    from apps.ladder_planner.services import compute
    from apps.ttt_planner.models import Route

    route = Route.objects.create(
        name="Downtown Dolphin",
        distance_km=2.0,
        elevation_m=17,
        velo_sprint=38.13,
        velo_punch=21.87,
        velo_climb=0,
        velo_endurance=40.00,
        velo_pursuit=0,
    )
    matchup = _make_matchup(team_member, route=route)
    ef = compute.event_factors(matchup)

    assert ef["available"] is True
    assert ef["route_name"] == "Downtown Dolphin"
    # Zero-weight factors (Climb, Pursuit) are dropped; the rest sort high→low.
    assert [f["label"] for f in ef["factors"]] == ["Endurance", "Sprint", "Punch"]
    assert ef["factors"][0]["value"] == pytest.approx(40.0)
    assert all(f["value"] > 0 for f in ef["factors"])
    assert all(f.get("color") and f.get("icon") for f in ef["factors"])


@pytest.mark.django_db
def test_event_factors_unavailable_without_route(team_member):
    from apps.ladder_planner.services import compute

    ef = compute.event_factors(_make_matchup(team_member))
    assert ef["available"] is False
    assert ef["factors"] == []


@pytest.mark.django_db
def test_event_factors_unavailable_when_route_has_no_weights(team_member):
    from apps.ladder_planner.services import compute
    from apps.ttt_planner.models import Route

    route = Route.objects.create(name="Blank", distance_km=10, elevation_m=50)
    ef = compute.event_factors(_make_matchup(team_member, route=route))
    assert ef["available"] is False


@pytest.mark.django_db
def test_event_factors_tab_renders(auth_client, team_member):
    from django.urls import reverse

    from apps.ttt_planner.models import Route

    route = Route.objects.create(
        name="Watopia Flat Route",
        distance_km=10.3,
        elevation_m=61,
        velo_sprint=33.04,
        velo_punch=22.52,
        velo_climb=0,
        velo_endurance=44.44,
        velo_pursuit=0,
    )
    matchup = _make_matchup(team_member, route=route)
    body = auth_client.get(reverse("ladder_planner:detail", args=[matchup.pk])).content.decode()
    assert "Event Factors" in body
    assert "Event Factor Weights" in body
    assert "Watopia Flat Route" in body


@pytest.mark.django_db
def test_event_factors_tab_empty_state(auth_client, team_member):
    from django.urls import reverse

    matchup = _make_matchup(team_member)
    body = auth_client.get(reverse("ladder_planner:detail", args=[matchup.pk])).content.decode()
    assert "Select a route to see" in body


@pytest.mark.django_db
def test_import_velo_weights_matches_by_name(tmp_path):
    from django.core.management import call_command

    from apps.ttt_planner.models import Route

    route = Route.objects.create(name="Bon Voyage", distance_km=28.2, elevation_m=132)
    doc = tmp_path / "weights.md"
    doc.write_text(
        "| World | Route | Sprint | Punch | Climb | Endurance | Pursuit |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |\n"
        "| France | [Bon Voyage](https://example/) | 25.77% | 18.47% | 0.00% | 7.65% | 48.11% |\n",
    )
    call_command("import_velo_weights", "--file", str(doc))
    route.refresh_from_db()
    assert float(route.velo_sprint) == pytest.approx(25.77)
    assert float(route.velo_endurance) == pytest.approx(7.65)
    assert float(route.velo_pursuit) == pytest.approx(48.11)


@pytest.mark.django_db
def test_import_velo_weights_if_empty_preserves_existing(tmp_path):
    from django.core.management import call_command

    from apps.ttt_planner.models import Route

    # A route already carrying (manually edited) weights must not be overwritten.
    edited = Route.objects.create(name="Bon Voyage", distance_km=28.2, elevation_m=132, velo_sprint=99)
    doc = tmp_path / "weights.md"
    doc.write_text(
        "| World | Route | Sprint | Punch | Climb | Endurance | Pursuit |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |\n"
        "| France | [Bon Voyage](https://example/) | 25.77% | 18.47% | 0.00% | 7.65% | 48.11% |\n",
    )
    call_command("import_velo_weights", "--file", str(doc), "--if-empty")
    edited.refresh_from_db()
    assert float(edited.velo_sprint) == pytest.approx(99)  # untouched


# ----- Event factor match ------------------------------------------------------------------------


@pytest.mark.django_db
def test_event_factor_match_weighted_fit_and_rows(team_member):
    from apps.ttt_planner.models import Route

    route = Route.objects.create(
        name="MatchRoute",
        distance_km=10,
        elevation_m=50,
        velo_sprint=40,
        velo_punch=0,
        velo_climb=0,
        velo_endurance=60,
        velo_pursuit=0,
    )
    matchup = _make_matchup(team_member, route=route)
    ours = _zr_data(rating=1, handicaps={}, name="Ours")
    ours["velo"] = {**ours["velo"], "sprint": 700, "endurance": 600}
    opp = _zr_data(rating=1, handicaps={}, name="Theirs")
    opp["velo"] = {**opp["velo"], "sprint": 500, "endurance": 500}
    _add(matchup, Side.OURS, ours)
    _add(matchup, Side.OPPONENT, opp)

    m = compute.event_factor_match(matchup)
    assert m["available"] is True
    # Zero-weight factors dropped; rows sorted by route weight descending.
    assert [r["label"] for r in m["rows"]] == ["Endurance", "Sprint"]
    assert m["our_fit"] == 640  # 0.40*700 + 0.60*600
    assert m["opp_fit"] == 500  # 0.40*500 + 0.60*500
    assert m["margin"] == 140
    assert m["margin_abs"] == 140
    assert m["favored"] == "Us"
    assert m["rows"][0]["edge"] == 100  # Endurance: 600 - 500
    # Weighted edge = weight% * edge; contributions sum to the margin.
    assert m["rows"][0]["weighted_edge"] == pytest.approx(60.0)  # 0.60 * 100
    assert m["rows"][1]["weighted_edge"] == pytest.approx(80.0)  # 0.40 * 200
    assert m["weighted_edge_total"] == pytest.approx(140.0)
    assert round(m["weighted_edge_total"]) == m["margin"]


@pytest.mark.django_db
def test_event_factor_match_unavailable_without_opponent(team_member):
    from apps.ttt_planner.models import Route

    route = Route.objects.create(
        name="R",
        distance_km=10,
        elevation_m=50,
        velo_sprint=50,
        velo_punch=0,
        velo_climb=0,
        velo_endurance=50,
        velo_pursuit=0,
    )
    matchup = _make_matchup(team_member, route=route)
    _add(matchup, Side.OURS, _zr_data(rating=1, handicaps={}, name="Ours"))
    assert compute.event_factor_match(matchup)["available"] is False


@pytest.mark.django_db
def test_event_factor_match_unavailable_without_route(team_member):
    matchup = _make_matchup(team_member)
    _add(matchup, Side.OURS, _zr_data(rating=1, handicaps={}, name="O"))
    _add(matchup, Side.OPPONENT, _zr_data(rating=1, handicaps={}, name="T"))
    assert compute.event_factor_match(matchup)["available"] is False


@pytest.mark.django_db
def test_event_factor_match_tab_renders(auth_client, team_member):
    from django.urls import reverse

    from apps.ttt_planner.models import Route

    route = Route.objects.create(
        name="RenderRoute",
        distance_km=10,
        elevation_m=50,
        velo_sprint=40,
        velo_punch=0,
        velo_climb=0,
        velo_endurance=60,
        velo_pursuit=0,
    )
    matchup = _make_matchup(team_member, route=route)
    ours = _zr_data(rating=1, handicaps={}, name="Ours")
    ours["velo"] = {**ours["velo"], "sprint": 700, "endurance": 600}
    opp = _zr_data(rating=1, handicaps={}, name="Theirs")
    opp["velo"] = {**opp["velo"], "sprint": 500, "endurance": 500}
    _add(matchup, Side.OURS, ours)
    _add(matchup, Side.OPPONENT, opp)

    body = auth_client.get(reverse("ladder_planner:detail", args=[matchup.pk])).content.decode()
    assert "Event Factor Match" in body
    assert "Route favors" in body


# ----- lazy Climb tab ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_climb_tab_lazy_loads_in_body(auth_client, team_member):
    from django.urls import reverse

    matchup = _make_matchup(team_member)
    body = auth_client.get(reverse("ladder_planner:detail", args=[matchup.pk])).content.decode()
    # The heavy climb grid is no longer inlined; the tab lazy-loads it on demand.
    assert reverse("ladder_planner:climb", args=[matchup.pk]) in body
    assert "intersect once" in body
    assert "Climb advantage" not in body


@pytest.mark.django_db
def test_climb_panel_endpoint_renders(auth_client, team_member):
    from django.urls import reverse

    matchup = _make_matchup(team_member)
    _add(matchup, Side.OURS, _zr_data(rating=1500, handicaps={}, name="Ours"))
    _add(matchup, Side.OPPONENT, _zr_data(rating=1500, handicaps={}, name="Theirs"))
    resp = auth_client.get(reverse("ladder_planner:climb", args=[matchup.pk]))
    assert resp.status_code == 200
    assert b"Climb advantage" in resp.content

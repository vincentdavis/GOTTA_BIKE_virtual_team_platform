"""Tests for the shared climb comparison engine."""

from apps.ttt_planner.services import climb


def _curve(scale: float) -> dict[int, float]:
    # A plausible declining power-duration curve, scaled to make stronger riders.
    base = {5: 900, 15: 700, 30: 600, 60: 480, 120: 420, 300: 360, 1200: 300}
    return {s: w * scale for s, w in base.items()}


def _rider(
    name: str, *, scale: float = 1.0, weight: float = 70.0, height: float = 178.0, side: str = ""
) -> climb.ClimbRider:
    return climb.ClimbRider(name=name, weight_kg=weight, height_cm=height, power_curve=_curve(scale), side=side)


def test_sustainable_power_clamps_and_interpolates():
    curve = {60: 480, 300: 360}
    assert climb.sustainable_power(curve, 30) == 480  # below range -> first point
    assert climb.sustainable_power(curve, 1200) == 360  # above range -> last point
    mid = climb.sustainable_power(curve, 120)
    assert 360 < mid < 480  # interpolated between the two points


def test_sustainable_power_none_without_data():
    assert climb.sustainable_power({}, 60) is None
    assert climb.sustainable_power({60: 0, 300: None}, 60) is None


def test_climb_effort_stronger_rider_is_faster():
    weak = _rider("weak", scale=0.8)
    strong = _rider("strong", scale=1.2)
    grade = 0.08
    length = 2000
    weak_res = climb.climb_effort(weak, length, grade)
    strong_res = climb.climb_effort(strong, length, grade)
    assert strong_res.time_s < weak_res.time_s
    assert strong_res.wkg > weak_res.wkg


def test_climb_effort_heavier_rider_climbs_slower_same_watts():
    light = climb.ClimbRider("light", 60.0, 175.0, _curve(1.0))
    heavy = climb.ClimbRider("heavy", 85.0, 175.0, _curve(1.0))  # same watts, more kg
    light_res = climb.climb_effort(light, 3000, 0.08)
    heavy_res = climb.climb_effort(heavy, 3000, 0.08)
    assert light_res.time_s < heavy_res.time_s
    assert light_res.wkg > heavy_res.wkg


def test_climb_effort_draft_helps_a_little():
    rider = _rider("r")
    no_draft = climb.climb_effort(rider, 2000, 0.08, draft_factor=1.0)
    with_draft = climb.climb_effort(rider, 2000, 0.08, draft_factor=0.7)
    # Drafting can only help (lower or equal time); on a steep climb the gain is small.
    assert with_draft.time_s <= no_draft.time_s


def test_climb_effort_none_without_weight_or_data():
    assert climb.climb_effort(climb.ClimbRider("x", 0, 175, _curve(1.0)), 1000, 0.08) is None
    assert climb.climb_effort(climb.ClimbRider("y", 70, 175, {}), 1000, 0.08) is None


def test_climb_matchup_favours_stronger_team():
    our = [_rider("o1", scale=1.2, side="ours"), _rider("o2", scale=1.15, side="ours")]
    opp = [_rider("t1", scale=0.9, side="opp"), _rider("t2", scale=0.85, side="opp")]
    result = climb.climb_matchup(our, opp, 3000, 0.08)
    assert result["our_points"] > result["opp_points"]
    assert result["margin"] > 0
    assert result["our_best_s"] < result["opp_best_s"]


def test_advantage_grid_shape_and_keys():
    our = [_rider("o1", scale=1.1, side="ours")]
    opp = [_rider("t1", scale=1.0, side="opp")]
    lengths = [500, 2000, 8000]
    grades = [0.04, 0.08]
    grid = climb.advantage_grid(our, opp, lengths, grades)
    assert len(grid) == 2  # one row per grade
    assert [row["grade"] for row in grid] == grades
    for row in grid:
        assert len(row["cells"]) == 3
        for cell, length in zip(row["cells"], lengths, strict=True):
            assert cell["length_m"] == length
            assert {"our_points", "opp_points", "margin"} <= set(cell)

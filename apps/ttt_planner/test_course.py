"""The team on the route's real elevation profile, at a steady effort (``services/course.py``).

The typical rider's power to hold the flat speed is held on every slope; that sets the
team's speed there, the finish time, and each rider's watts on the front and sitting in,
which the plan page draws as a course chart. Profiles come from the Speed Lab catalog,
stood in for here by the ``route_profiles`` fixture.
"""

import json
import re

import pytest
from django.urls import reverse

from apps.ttt_planner.models import PlanRider, TttPlan
from apps.ttt_planner.services import compute, course, physics
from apps.zwift_data.models import ZwiftRoute

FLAT_10K = [(d, 0.0) for d in range(0, 10_001, 50)]


def _hill(climb_m=2000, grade=0.06):
    """Lay out 2 km flat, then up and straight back down at the same gradient, then 2 km flat.

    Returns:
        ``(distance m, elevation m)`` points 50 m apart.

    """
    points, elevation = [], 0.0
    for d in range(0, 2000 + 2 * climb_m + 2001, 50):
        if 2000 < d <= 2000 + climb_m:
            elevation += 50 * grade
        elif 2000 + climb_m < d <= 2000 + 2 * climb_m:
            elevation -= 50 * grade
        points.append((d, round(elevation, 3)))
    return points


def _route(route_profiles, name, points, leadin_m=0, ascent_m=0):
    """Make a route whose catalog profile is these points.

    Returns:
        The ZwiftRoute.

    """
    route = ZwiftRoute.objects.create(
        name=name,
        world="Watopia",
        world_id=1,
        name_hash=name,
        distance_km=(points[-1][0] - leadin_m) / 1000,
        ascent_m=ascent_m,
        leadin_km=leadin_m / 1000,
    )
    route_profiles[1, name] = {"d": [d for d, _ in points], "e": [e for _, e in points], "leadin_m": leadin_m}
    return route


def _course(route_profiles, points, **kwargs):
    route = _route(route_profiles, "R", points, **kwargs)
    return course.Course.build(course.load_profile(route), 75, 178, physics.DEFAULT_PARAMS)


# --- solving the power model ---------------------------------------------------------------------


@pytest.mark.parametrize("grade", [-0.12, -0.04, -0.004, 0.0, 0.02, 0.08, 0.15])
@pytest.mark.parametrize("power", [80, 250, 420])
def test_the_direct_solve_matches_the_bisection(grade, power):
    kwargs = {"weight_kg": 68, "height_cm": 175, "grade": grade, "draft_factor": 0.7}

    assert physics.speed_on_grade(power, **kwargs) == pytest.approx(physics.speed_for_power(power, **kwargs), abs=1e-6)


def test_coasting_down_a_steep_slope_still_moves():
    assert physics.speed_on_grade(0, weight_kg=75, height_cm=178, grade=-0.08) > 40
    assert physics.speed_on_grade(0, weight_kg=75, height_cm=178, grade=0.02) == 0


# --- the profile and the course ------------------------------------------------------------------


@pytest.mark.django_db
def test_a_profile_is_cleaned_up_and_starts_at_zero(route_profiles):
    route = _route(route_profiles, "Messy", [(100, 5), (150, 6), (150, 9), (200, "x"), (260, 7)], leadin_m=120)

    profile = course.load_profile(route)

    assert profile.d == (0.0, 50.0, 160.0)  # the repeated and the unreadable points are dropped
    assert profile.e == (5.0, 6.0, 7.0)
    assert profile.leadin_m == pytest.approx(20.0)


@pytest.mark.django_db
def test_a_flat_course_takes_its_length_at_the_flat_speed(route_profiles):
    assert _course(route_profiles, FLAT_10K).seconds(40) == pytest.approx(900, abs=0.01)


@pytest.mark.django_db
def test_hills_cost_time_but_the_descent_gives_some_back(route_profiles):
    """An out-and-back hill: slower than flat, but quicker than one steady climb over the whole route."""
    hill = _course(route_profiles, _hill())
    length_km = hill.profile.length_m / 1000

    as_one_slope = physics.estimate_time_seconds(length_km, 120, 44, avg_weight_kg=75, avg_height_cm=178)

    assert length_km / 44 * 3600 < hill.seconds(44) < as_one_slope


# --- the plan ------------------------------------------------------------------------------------


@pytest.fixture
def plan(team_member):
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=44)
    PlanRider.objects.create(plan=plan, order=0, name="Light", weight_kg=60, height_cm=168, ftp_w=300)
    PlanRider.objects.create(plan=plan, order=1, name="Heavy", weight_kg=90, height_cm=190, ftp_w=360)
    return plan


@pytest.mark.django_db
def test_the_finish_time_rides_the_profile_lead_in_included(plan, route_profiles):
    plan.route = _route(route_profiles, "Lead", FLAT_10K, leadin_m=1000)
    plan.save()

    result = compute.compute_plan(plan)

    assert result.course_km == pytest.approx(10.0)  # the profile's length: lead-in and route
    assert result.leadin_km == pytest.approx(1.0)
    assert result.estimated_time_s == pytest.approx(10 / 44 * 3600, abs=0.1)
    assert result.course_avg_kph == pytest.approx(44.0)


@pytest.mark.django_db
def test_without_a_profile_the_average_gradient_stands_in(plan, route_profiles):
    plan.route = ZwiftRoute.objects.create(
        name="Bare", world="Watopia", world_id=1, name_hash="Bare", distance_km=20, ascent_m=200
    )
    plan.save()

    result = compute.compute_plan(plan)

    expected = physics.estimate_time_seconds(20, 200, 44, avg_weight_kg=75, avg_height_cm=179)
    assert result.estimated_time_s == pytest.approx(expected, rel=1e-3)
    assert result.course_chart is None


@pytest.mark.django_db
def test_on_a_climb_the_light_rider_pulls_fewer_watts_and_the_draft_fades(plan, route_profiles):
    plan.route = _route(route_profiles, "Hill", _hill(grade=0.06))
    plan.save()

    chart = compute.compute_plan(plan).course_chart
    light, heavy = chart["riders"]
    flat = chart["grade"].index(0.0)
    climb = chart["grade"].index(max(chart["grade"]))

    assert chart["kph"][climb] < chart["kph"][flat] == pytest.approx(44.0, abs=0.1)
    assert light["front"][climb] < heavy["front"][climb]
    # Sitting in saves a share of the aero power, which is most of the work on the flat and
    # little of it on a climb: there, sitting in costs nearly as much as pulling.
    assert light["sit"][climb] / light["front"][climb] > light["sit"][flat] / light["front"][flat]
    assert light["sit"][climb] / light["front"][climb] > 0.9


@pytest.mark.django_db
def test_a_custom_pull_power_scales_that_riders_pulls(plan, route_profiles):
    plan.route = _route(route_profiles, "Flat", FLAT_10K)
    plan.save()
    plan.riders.filter(name="Light").update(pull_power_w=400)

    light = compute.compute_plan(plan).course_chart["riders"][0]

    assert set(light["front"]) == {400}


@pytest.mark.django_db
def test_the_chart_marks_riders_who_take_no_pulls(plan, route_profiles):
    plan.route = _route(route_profiles, "Flat", FLAT_10K)
    plan.save()
    plan.riders.filter(name="Heavy").update(zero_pull=True)

    pulls = [r["pulls"] for r in compute.compute_plan(plan).course_chart["riders"]]

    assert pulls == [True, False]


@pytest.mark.django_db
def test_the_speed_search_and_the_status_ride_the_profile(plan, route_profiles):
    plan.route = _route(route_profiles, "Hill", _hill())
    plan.save()

    status = compute.compute_plan(plan).status

    assert status.fastest_kph == compute.sustainable_speed(plan)
    assert status.held_back_by


# --- the page ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_plan_page_draws_the_course_when_the_route_has_a_profile(auth_client, plan, route_profiles):
    url = reverse("ttt_planner:detail", args=[plan.pk])
    bare = auth_client.get(url).content.decode()
    assert 'id="ttt-course-data"' not in bare
    assert 'id="ttt-on-course-heading"' not in bare

    plan.route = _route(route_profiles, "Hill", _hill(), leadin_m=500)
    plan.save()
    body = auth_client.get(url).content.decode()

    assert body.count('id="ttt-on-course-heading"') == 1
    assert body.count('id="ttt-course-heading"') == 1  # the Course card's, not taken twice
    data = json.loads(re.search(r'id="ttt-course-data"[^>]*>(.*?)</script>', body, re.S).group(1))
    assert len(data["riders"]) == 2
    assert len(data["km"]) == len(data["kph"]) == len(data["riders"][0]["front"])
    # The readout's rows, in the same order as the chart's riders, are named by the page.
    readout = body[body.index('id="ttt-course-readout"') :]
    assert readout.index(">Light<") < readout.index(">Heavy<")
    assert "incl. 0.5 km lead-in" in body


@pytest.mark.django_db
def test_rider_names_reach_the_chart_as_data_not_markup(auth_client, plan, route_profiles):
    plan.route = _route(route_profiles, "Flat", FLAT_10K)
    plan.save()
    plan.riders.filter(name="Light").update(name="</script><script>alert(1)</script>")

    body = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()

    assert "<script>alert(1)</script>" not in body

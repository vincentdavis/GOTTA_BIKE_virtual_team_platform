"""The target speed is a flat speed, and the route's climbing is counted once, in the finish time.

Every power used to be worked out at the route's average gradient (total climbing over
distance) as if the whole race were a steady climb, while the finish time also slowed the
team down for the same climbing. On a 20 km / 200 m route that put roughly 100 W on every
pull, made the fastest speed the limits allow about 5 kph slower, and then slowed the finish time again.
"""

import pytest

from apps.ttt_planner.models import PlanRider, TttPlan
from apps.ttt_planner.services.compute import compute_plan, optimize_plan, sustainable_speed
from apps.zwift_data.models import ZwiftRoute

# Invented riders: (name, kg, cm, FTP).
RIDERS = [("A", 75, 180, 320), ("B", 68, 175, 280), ("C", 82, 185, 300), ("D", 62, 168, 230)]


def _route(name, ascent_m):
    return ZwiftRoute.objects.create(
        name=name, world="Watopia", world_id=1, name_hash=name[:32], distance_km=20, ascent_m=ascent_m
    )


@pytest.fixture
def plan(team_member):
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=44, target_if=0.95)
    for order, (name, kg, cm, ftp) in enumerate(RIDERS):
        PlanRider.objects.create(
            plan=plan, order=order, name=name, weight_kg=kg, height_cm=cm, ftp_w=ftp, pull_duration_s=45
        )
    return plan


def _on(plan, route):
    plan.route = route
    plan.save()
    return plan


@pytest.mark.django_db
def test_pull_powers_do_not_depend_on_the_routes_climbing(plan):
    flat = compute_plan(_on(plan, _route("Flat", 0)))
    hilly = compute_plan(_on(plan, _route("Hilly", 200)))

    assert [r.pull_power_w for r in hilly.riders] == [r.pull_power_w for r in flat.riders]
    assert [r.avg_power_w for r in hilly.riders] == [r.avg_power_w for r in flat.riders]
    # The climbing still costs time, once.
    assert hilly.estimated_time_s > flat.estimated_time_s
    assert hilly.course_avg_kph < flat.course_avg_kph == pytest.approx(44.0, abs=0.05)


@pytest.mark.django_db
def test_the_fastest_speed_for_the_pulls_is_the_same_on_any_route(plan):
    assert sustainable_speed(_on(plan, _route("Flat", 0))) == sustainable_speed(_on(plan, _route("Hilly", 200)))


@pytest.mark.django_db
def test_optimize_gives_the_same_flat_speed_on_any_route(plan):
    flat = optimize_plan(_on(plan, _route("Flat", 0)))
    hilly = optimize_plan(_on(plan, _route("Hilly", 200)))

    assert hilly.speed_kph == flat.speed_kph
    assert [a.pull_duration_s for a in hilly.assignments] == [a.pull_duration_s for a in flat.assignments]


@pytest.mark.django_db
def test_pull_pct_ftp_is_the_pull_against_ftp_and_if_is_np_against_ftp(plan):
    plan.riders.filter(name="D").update(zero_pull=True)
    result = compute_plan(_on(plan, _route("Flat", 0)))
    by_name = {r.rider.name: r for r in result.riders}

    a = by_name["A"]
    assert a.pull_pct_ftp == round(a.pull_power_w / 320 * 100)
    assert a.intensity_factor == pytest.approx(a.normalized_power_w / 320, abs=0.01)
    # A rider who takes no pulls has no pull to rate.
    assert by_name["D"].pull_pct_ftp is None
    assert by_name["D"].intensity_factor is not None


@pytest.mark.django_db
def test_without_a_route_there_is_no_tss_or_course_speed(plan):
    """IF comes from one rotation and needs no route; TSS and the course speed need the race length."""
    result = compute_plan(plan)

    assert not result.course_avg_kph
    assert all(r.tss is None for r in result.riders)
    assert all(r.intensity_factor is not None for r in result.riders)
    assert all(r.pull_pct_ftp is not None for r in result.riders)

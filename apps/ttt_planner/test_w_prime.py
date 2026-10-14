"""W′ balance in the TTT planner, and the two limits Optimize works to.

Every rider with an FTP, those who take no pulls too, must keep IF (NP / FTP) at or under
Target IF and their W′ balance at or above the plan's W′ reserve. NP and W′bal are worked
out in closed form from one rotation (``services/effort.py``); the first block of tests
checks them against plain second-by-second simulations.
"""

import importlib
import math

import pytest
from django.apps import apps as django_apps
from django.urls import reverse

from apps.ttt_planner.models import PlanRider, TttPlan
from apps.ttt_planner.services import compute, effort, physics
from apps.zwiftracing.models import ZRRider

# --- the maths, against second-by-second simulations --------------------------------------------


def _simulated_np(front, draft, pull_s, cycle_s, cycles=80):
    series = [front if (t % cycle_s) < pull_s else draft for t in range(cycle_s * cycles)]
    return physics.normalized_power(series)


@pytest.mark.parametrize(
    ("front", "draft", "pull_s", "cycle_s"),
    [(420, 260, 60, 240), (380, 300, 30, 150), (450, 280, 180, 600), (400, 250, 20, 90), (350, 290, 15, 45)],
)
def test_cycle_np_matches_a_simulated_race(front, draft, pull_s, cycle_s):
    """The closed form (pulls and sits of 30 s or more) and the per-second fallback (shorter)."""
    assert effort.cycle_np(front, draft, pull_s, cycle_s) == pytest.approx(
        _simulated_np(front, draft, pull_s, cycle_s), abs=1.0
    )


def test_cycle_np_edges():
    assert effort.cycle_np(400, 250, 0, 120) == 250  # no pulls: sitting in all race
    assert effort.cycle_np(400, 250, 120, 120) == 400  # the only puller: on the front all race


def _simulated_low(front, draft, pull_s, cycle_s, cp, w_prime, seconds):
    balance = lowest = w_prime
    for t in range(seconds):
        power = front if (t % cycle_s) < pull_s else draft
        if power > cp:
            balance -= power - cp
        else:
            balance += (w_prime - balance) * (cp - power) / w_prime
        lowest = min(lowest, balance)
    return lowest


def test_lowest_w_prime_bal_matches_a_simulated_race():
    race_s, cycle_s = 2400, 240
    closed = effort.lowest_w_prime_bal(420, 260, 60, cycle_s, 320, 20000, pulls=math.ceil(race_s / cycle_s))

    assert closed == pytest.approx(_simulated_low(420, 260, 60, cycle_s, 320, 20000, race_s), abs=400)


def test_a_long_race_is_the_worst_case_and_the_race_approaches_it():
    args = (420, 260, 60, 240, 320, 20000)
    long_race = effort.lowest_w_prime_bal(*args)
    lows = [effort.lowest_w_prime_bal(*args, pulls=n) for n in (1, 3, 10, 100)]

    assert lows == sorted(lows, reverse=True)  # each extra pull digs a little deeper
    assert lows[-1] == pytest.approx(long_race, abs=1)
    # The settled low point: one pull's spend over the share refilled in one sit.
    spend = (420 - 320) * 60
    refill = 1 - math.exp(-(320 - 260) * 180 / 20000)
    assert long_race == pytest.approx(20000 - spend / refill, abs=1)


def test_a_race_of_endless_pulls_is_still_quick_to_judge():
    """A speed search near a standstill asks about races of billions of pulls.

    The low point comes from a closed form, so the cost does not grow with the count, and a
    race that empties W′ says so rather than settling.
    """
    settled = effort.lowest_w_prime_bal(420, 260, 60, 240, 320, 20000)

    assert effort.lowest_w_prime_bal(420, 260, 60, 240, 320, 20000, pulls=10**15) == pytest.approx(settled)
    assert effort.lowest_w_prime_bal(420, 330, 60, 240, 320, 20000, pulls=10**15) < -1e15


def test_w_prime_edges():
    assert effort.lowest_w_prime_bal(300, 200, 60, 240, 320, 20000) == 20000  # pulls below CP never drain
    assert effort.lowest_w_prime_bal(400, 330, 60, 240, 320, 20000) == -math.inf  # nothing ever refills
    assert effort.sitting_w_prime_bal(300, 320, 20000, 1800) == 20000
    assert effort.sitting_w_prime_bal(330, 320, 20000, 1800) == 20000 - 10 * 1800
    assert effort.sitting_w_prime_bal(330, 320, 20000, None) == -math.inf


# --- where CP and W′ come from -------------------------------------------------------------------


@pytest.mark.django_db
def test_a_team_rider_brings_their_zwift_racing_cp_and_w_prime(auth_client, team_member):
    ZRRider.objects.create(zwid=4242, name="Rider", weight=70, height=178, zp_ftp=300, power_cp=310, power_awc=21500)
    plan = TttPlan.objects.create(created_by=team_member)

    auth_client.post(reverse("ttt_planner:rider_add", args=[plan.pk, 4242]))

    rider = plan.riders.get()
    assert (rider.cp_w, rider.w_prime_j) == (310, 21500)


@pytest.mark.django_db
def test_without_zwift_racing_figures_ftp_and_twenty_kj_stand_in(team_member):
    plan = TttPlan.objects.create(created_by=team_member)
    guest = PlanRider.objects.create(plan=plan, order=0, name="Guest", ftp_w=280)
    zero = PlanRider.objects.create(plan=plan, order=1, name="Zero", ftp_w=280, cp_w=290, w_prime_j=0)

    g, z = compute.rider_spec(guest), compute.rider_spec(zero)

    assert (g.cp, g.w_prime, g.cp_estimated, g.w_prime_estimated) == (280.0, 20000.0, True, True)
    # Zwift Racing reports 0 for some riders: that is "unknown", not "no W′ at all".
    assert (z.cp, z.w_prime, z.cp_estimated, z.w_prime_estimated) == (290.0, 20000.0, False, True)


@pytest.mark.django_db
def test_the_migration_fills_cp_and_w_prime_for_riders_already_on_plans(team_member):
    migration = importlib.import_module("apps.ttt_planner.migrations.0026_plan_w_prime")
    ZRRider.objects.create(zwid=1, name="One", power_cp=300, power_awc=18000)
    ZRRider.objects.create(zwid=2, name="Two", power_cp=250, power_awc=0)
    plan = TttPlan.objects.create(created_by=team_member)
    one = PlanRider.objects.create(plan=plan, order=0, name="One", zwid=1)
    two = PlanRider.objects.create(plan=plan, order=1, name="Two", zwid=2)
    kept = PlanRider.objects.create(plan=plan, order=2, name="Kept", zwid=1, cp_w=999, w_prime_j=12345)

    migration.copy_cp_and_w_prime(django_apps, None)

    for rider in (one, two, kept):
        rider.refresh_from_db()
    assert (one.cp_w, one.w_prime_j) == (300, 18000)
    assert (two.cp_w, two.w_prime_j) == (250, None)  # a W′ of 0 is left blank
    assert (kept.cp_w, kept.w_prime_j) == (999, 12345)  # never overwritten


# --- the limits ----------------------------------------------------------------------------------

# Invented riders: (name, kg, cm, FTP, CP, W′ J).
STRONG_AND_SMALL_W_PRIME = [
    ("A", 75, 180, 320, 300, 5000),
    ("B", 68, 175, 280, 270, 4000),
    ("C", 82, 185, 300, 290, 6000),
    ("E", 90, 190, 340, 330, 5000),
]


@pytest.fixture
def plan(team_member):
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    for order, (name, kg, cm, ftp, cp, w_prime) in enumerate(STRONG_AND_SMALL_W_PRIME):
        PlanRider.objects.create(
            plan=plan,
            order=order,
            name=name,
            weight_kg=kg,
            height_cm=cm,
            ftp_w=ftp,
            cp_w=cp,
            w_prime_j=w_prime,
            pull_duration_s=90,
        )
    return plan


@pytest.mark.django_db
def test_a_bigger_w_prime_reserve_slows_the_fastest_speed(plan):
    plan.w_prime_reserve_pct = 10
    loose = compute.sustainable_speed(plan)
    plan.w_prime_reserve_pct = 60
    tight = compute.sustainable_speed(plan)

    assert tight < loose


@pytest.mark.django_db
def test_the_fastest_speed_for_the_pulls_keeps_every_rider_inside_both_limits(plan):
    plan.target_speed_kph = compute.sustainable_speed(plan)
    plan.save()
    result = compute.compute_plan(plan)

    assert not any(r.over_target for r in result.riders)
    assert max(r.intensity_factor for r in result.riders) <= 0.95
    assert min(r.w_prime_low_pct for r in result.riders) >= 25


@pytest.mark.django_db
def test_a_rider_who_takes_no_pulls_still_caps_the_speed(plan, team_member):
    """They still have to hold the wheel: the plan never goes faster by dropping them."""
    without = compute.sustainable_speed(plan)
    PlanRider.objects.create(plan=plan, order=9, name="Weak", weight_kg=80, height_cm=185, ftp_w=180, zero_pull=True)

    assert compute.sustainable_speed(plan) < without


@pytest.mark.django_db
def test_optimized_pulls_sit_on_the_grid_and_keep_everyone_inside_the_limits(plan):
    plan.min_pull_s, plan.max_pull_s, plan.pull_step_s = 30, 120, 15
    plan.save()

    result = compute.optimize_plan(plan)
    by_pk = {r.pk: r for r in plan.riders.all()}
    for assignment in result.assignments:
        rider = by_pk[assignment.rider_pk]
        rider.pull_duration_s, rider.zero_pull, rider.order = (
            assignment.pull_duration_s,
            assignment.zero_pull,
            assignment.order,
        )
        rider.save()
        if not assignment.zero_pull:
            assert 30 <= assignment.pull_duration_s <= 120
            assert assignment.pull_duration_s % 15 == 0
    plan.target_speed_kph = result.speed_kph
    plan.save()

    shown = compute.compute_plan(plan)
    assert not any(r.over_target for r in shown.riders)
    # Optimize sets the speed by the keep-my-pulls rule, so optimizing again with the pulls kept changes nothing.
    assert compute.sustainable_speed(plan) == result.speed_kph


@pytest.mark.django_db
def test_optimize_is_faster_than_even_pulls(plan):
    even = compute.sustainable_speed(plan)

    assert compute.optimize_plan(plan).speed_kph >= even


# --- the settings --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("posted", "stored"),
    [
        ({"pull_step_s": "15", "min_pull_s": "40", "max_pull_s": "170"}, (15, 45, 165)),
        ({"pull_step_s": "7", "min_pull_s": "30", "max_pull_s": "180"}, (15, 30, 180)),  # unknown step
        ({"pull_step_s": "30", "min_pull_s": "200", "max_pull_s": "60"}, (30, 210, 210)),  # longest below shortest
        ({"pull_step_s": "10", "min_pull_s": "0", "max_pull_s": "900"}, (10, 10, 300)),  # clamped
    ],
)
@pytest.mark.django_db
def test_the_settings_are_stored_as_the_optimisers_read_them(auth_client, team_member, posted, stored):
    plan = TttPlan.objects.create(created_by=team_member)

    auth_client.post(reverse("ttt_planner:update", args=[plan.pk]), posted, HTTP_HX_REQUEST="true")

    plan.refresh_from_db()
    assert (plan.pull_step_s, plan.min_pull_s, plan.max_pull_s) == stored


@pytest.mark.django_db
def test_the_w_prime_reserve_is_clamped(auth_client, team_member):
    plan = TttPlan.objects.create(created_by=team_member)

    auth_client.post(
        reverse("ttt_planner:update", args=[plan.pk]), {"w_prime_reserve_pct": "150"}, HTTP_HX_REQUEST="true"
    )

    plan.refresh_from_db()
    assert plan.w_prime_reserve_pct == 90


# --- what the page shows -------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_schedule_shows_w_prime_and_flags_a_rider_past_a_limit(auth_client, team_member):
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=48)
    PlanRider.objects.create(
        plan=plan, order=0, name="Known", weight_kg=72, height_cm=178, ftp_w=300, cp_w=310, w_prime_j=21000
    )
    PlanRider.objects.create(plan=plan, order=1, name="Guest", weight_kg=72, height_cm=178, ftp_w=250)

    body = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()

    assert "Low W′bal" in body
    assert ">21.0<" in body  # Known's W′, from Zwift Racing
    assert '<span class="sr-only">about </span>250' in body  # Guest's CP stands in from FTP
    assert "over target" in body  # 48 kph is too fast for these two

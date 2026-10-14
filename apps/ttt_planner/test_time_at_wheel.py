"""Time at each wheel: every rider sits in on the wheels their place in the line gives them.

After a pull a rider drops to the back and works up the line a wheel at a time, so they sit
at the 2nd wheel, where the draft saves least, for as long as the rider ahead of them pulls,
at the 3rd for the pull two ahead, and so on round the line. Sitting in is a run of steps,
not one average: ``services/effort.py`` turns any run of steady stretches into NP and W′
balance exactly, the order of the line changes every rider's numbers, and Optimize settles
its pulls onto those wheels before it judges them.
"""

import math
import random
import re

import pytest
from django.urls import reverse

from apps.ttt_planner.models import PlanRider, TttPlan
from apps.ttt_planner.services import compute, effort, physics
from apps.zwift_data.models import ZwiftRoute

# --- the maths, against simulations --------------------------------------------------------------


def _steady_np(stretches, per_second=10):
    """NP of the repeating rotation from a fine simulation, the window wrapping round (no warm-up).

    Returns:
        Normalized Power in watts.

    """
    series = [watts for watts, seconds in stretches for _ in range(round(seconds * per_second))]
    size, window = len(series), 30 * per_second
    looped = [series[(i - window) % size] for i in range(size + window)]
    sums = [0.0]
    for watts in looped:
        sums.append(sums[-1] + watts)
    return (sum(((sums[t + window + 1] - sums[t + 1]) / window) ** 4 for t in range(size)) / size) ** 0.25


def _simulated_low(stretches, cp, w_prime, rotations):
    """Simulate the lowest W′ balance second by second, with the model's exact refill over each second.

    Returns:
        The lowest balance in joules.

    """
    balance = lowest = w_prime
    for _ in range(rotations):
        for watts, seconds in stretches:
            for _ in range(seconds):
                if watts > cp:
                    balance -= watts - cp
                else:
                    balance = w_prime - (w_prime - balance) * math.exp(-(cp - watts) / w_prime)
                lowest = min(lowest, balance)
    return lowest


ROTATIONS = [
    [(420, 60), (250, 90), (262, 45), (281, 120)],  # every stretch 30 s or more: the short sum
    [(400, 20), (250, 25), (262, 15), (270, 45), (290, 30)],  # short stretches: integrated piece by piece
    [(400, 8), (250, 7)],  # a rotation shorter than NP's 30-second window
    [(380, 30), (380, 30), (240, 60), (0, 0), (300, 45)],  # repeats and an empty stretch join up
]


@pytest.mark.parametrize("stretches", ROTATIONS)
def test_np_of_a_rotation_of_many_steps_matches_a_simulation(stretches):
    assert effort.rotation_np(stretches) == pytest.approx(_steady_np(stretches), abs=0.01)


@pytest.mark.parametrize("rotations", [1, 3, 12])
def test_w_prime_through_a_rotation_of_many_steps_matches_a_simulation(rotations):
    # The 2nd wheel (the last stretch) is above CP here, so W′ drains before the pull as well as on it.
    stretches = [(430, 60), (250, 90), (290, 45), (335, 120)]

    low = effort.rotation_lowest_w_prime_bal(stretches, 320, 20_000, rotations)

    assert low == pytest.approx(_simulated_low(stretches, 320, 20_000, rotations), abs=1e-3)


def test_a_long_race_is_where_a_race_of_many_rotations_settles():
    stretches = [(430, 60), (250, 90), (290, 45), (335, 120)]

    long_race = effort.rotation_lowest_w_prime_bal(stretches, 320, 20_000)

    assert long_race == pytest.approx(effort.rotation_lowest_w_prime_bal(stretches, 320, 20_000, 10**6))


def test_a_pull_and_one_sit_are_a_rotation_of_two_steps():
    assert effort.cycle_np(420, 260, 60, 240) == effort.rotation_np([(420, 60), (260, 180)])
    two = effort.rotation_lowest_w_prime_bal([(420, 60), (260, 180)], 320, 20_000, 10)
    assert effort.lowest_w_prime_bal(420, 260, 60, 240, 320, 20_000, pulls=10) == two


# --- which wheel, for how long -------------------------------------------------------------------


def test_each_wheel_lasts_as_long_as_the_pull_that_many_places_ahead():
    pulls = [120, 90, 60, 45]

    seconds = compute.wheel_seconds(pulls)

    # 2nd wheel behind the 120 s pull, 3rd for the 45 s two ahead, the back for the 60 s just behind.
    assert seconds[1] == [120, 45, 60]
    assert seconds[0] == [45, 60, 90]  # the first rider follows the last, round the line
    for pull, at_wheels in zip(pulls, seconds, strict=True):
        assert sum(at_wheels) == sum(pulls) - pull  # sitting in for everyone else's pulls


def test_a_rider_who_takes_no_pulls_moves_everyone_up_without_taking_time():
    seconds = compute.wheel_seconds([60, 0, 90])

    assert seconds[2] == [0, 60]  # never on the 2nd wheel: the rider ahead swings straight off
    assert seconds[1] == [60, 90]  # who still rides every wheel in turn


def test_the_average_draft_weighs_each_wheel_by_the_time_there():
    params = physics.DEFAULT_PARAMS

    behind_a_long_pull = compute.sitting_draft_factor([180, 60, 60], params)
    in_front_of_it = compute.sitting_draft_factor([60, 60, 180], params)

    assert behind_a_long_pull > in_front_of_it  # more air to push
    assert compute.sitting_draft_factor([60, 60, 60], params) == pytest.approx(
        1 - sum(physics.draft_savings_fraction(w, params) for w in (2, 3, 4)) / 3
    )


# --- the plan ------------------------------------------------------------------------------------


def _plan(owner, pulls, speed=44):
    """Make a plan of identical riders (75 kg, 178 cm, FTP 320) with these pulls, in this order.

    Returns:
        The plan.

    """
    plan = TttPlan.objects.create(created_by=owner, target_speed_kph=speed)
    for order, pull in enumerate(pulls):
        PlanRider.objects.create(
            plan=plan,
            order=order,
            name=f"R{order}",
            weight_kg=75,
            height_cm=178,
            ftp_w=320,
            cp_w=330,
            w_prime_j=20_000,
            pull_duration_s=pull,
            zero_pull=pull == 0,
        )
    return plan


def _sitting_power(wheel):
    params = physics.params_from_constance()
    factor = 1 - physics.draft_savings_fraction(wheel, params)
    return physics.power_for_speed(44, weight_kg=75, height_cm=178, draft_factor=factor, params=params)


@pytest.mark.django_db
def test_the_rider_behind_a_long_pull_works_harder_than_the_one_in_front_of_it(team_member):
    riders = compute.compute_plan(_plan(team_member, [180, 60, 60, 60])).riders
    behind, in_front = riders[1], riders[3]

    assert behind.wheel_seconds == [180, 60, 60]
    assert in_front.wheel_seconds == [60, 60, 180]
    assert behind.avg_power_w > in_front.avg_power_w
    assert behind.normalized_power_w > in_front.normalized_power_w


@pytest.mark.django_db
def test_the_numbers_follow_the_place_in_the_line(team_member):
    plan = _plan(team_member, [180, 60, 60, 60])
    before = {r.rider.name: r.avg_power_w for r in compute.compute_plan(plan).riders}

    plan.riders.filter(name="R1").update(order=3)
    plan.riders.filter(name="R3").update(order=1)  # now R3 rides right behind the long pull
    after = {r.rider.name: r for r in compute.compute_plan(plan).riders}

    assert after["R3"].wheel_seconds == [180, 60, 60]
    assert after["R3"].avg_power_w == before["R1"]  # the riders are the same, so the places swap their numbers
    assert after["R1"].avg_power_w == before["R3"]


@pytest.mark.django_db
def test_with_even_pulls_every_rider_gets_an_even_share_of_every_wheel(team_member):
    rider = compute.compute_plan(_plan(team_member, [60, 60, 60, 60])).riders[0]
    params = physics.params_from_constance()
    front = physics.power_for_speed(44, weight_kg=75, height_cm=178, params=params)
    even = sum(_sitting_power(wheel) for wheel in (2, 3, 4)) / 3

    assert rider.wheel_seconds == [60, 60, 60]
    assert rider.avg_power_w == round((front * 60 + even * 180) / 240)


@pytest.mark.django_db
def test_behind_a_lone_puller_a_rider_sits_on_the_second_wheel_all_race(team_member):
    """What an even share of every wheel got wrong: nobody comes past to move them back."""
    riders = compute.compute_plan(_plan(team_member, [60, 0, 0, 0])).riders

    assert riders[1].wheel_seconds == [60, 0, 0]
    assert riders[1].avg_power_w == round(_sitting_power(2))
    assert riders[3].wheel_seconds == [0, 0, 60]  # the last in line sits at the back
    assert riders[3].avg_power_w == round(_sitting_power(4))


@pytest.mark.django_db
def test_the_course_chart_sits_each_rider_on_their_own_wheels(team_member, route_profiles):
    plan = _plan(team_member, [180, 60, 60, 60])
    flat = [(d, 0.0) for d in range(0, 10_001, 50)]
    plan.route = ZwiftRoute.objects.create(
        name="Flat", world="Watopia", world_id=1, name_hash="Flat", distance_km=10, ascent_m=0
    )
    route_profiles[1, "Flat"] = {"d": [d for d, _ in flat], "e": [e for _, e in flat], "leadin_m": 0}
    plan.save()

    rows = compute.compute_plan(plan).course_chart["riders"]

    assert rows[1]["sit"][0] > rows[3]["sit"][0]  # right behind the long pull, against right in front of it


@pytest.mark.django_db
def test_the_pull_schedule_says_how_long_each_rider_sits_at_each_wheel(auth_client, team_member):
    plan = _plan(team_member, [180, 60, 60, 60])

    body = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()
    card = body[body.index('id="ttt-schedule-heading"') : body.index('id="ttt-stats-heading"')]
    row = card[card.index(">R1<") :]
    row = row[: row.index("</tr>")]

    assert re.findall(r"(\d+) s<", row) == ["180", "60", "60"]


# --- Optimize ------------------------------------------------------------------------------------


def _team(seed, size):
    """Invent riders: (kg, cm, FTP, W′ J).

    Returns:
        The riders.

    """
    rnd = random.Random(seed)  # noqa: S311 -- seeded so the invented riders are the same every run
    riders = []
    for _ in range(size):
        kg = rnd.uniform(58, 92)
        riders.append((round(kg), round(rnd.uniform(165, 192)), round(kg * rnd.uniform(3.2, 4.6)), rnd.randint(13, 27)))
    return [(kg, cm, ftp, kj * 1000) for kg, cm, ftp, kj in riders]


@pytest.mark.parametrize("pull_rule", ["even", "reserve"])
@pytest.mark.parametrize("seed", range(4))
@pytest.mark.django_db
def test_what_optimize_sets_fits_on_the_wheels_its_order_gives(team_member, seed, pull_rule):
    """Teams of four to seven: judged on every rider's own wheels, Optimize's plan fits and is at its fastest."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=44, pull_rule=pull_rule)
    for order, (kg, cm, ftp, w_prime) in enumerate(_team(seed, 4 + seed)):
        PlanRider.objects.create(
            plan=plan,
            order=order,
            name=f"R{order}",
            weight_kg=kg,
            height_cm=cm,
            ftp_w=ftp,
            cp_w=round(ftp * 1.03),
            w_prime_j=w_prime,
            pull_duration_s=60,
        )

    outcome = compute.optimize_plan(plan)
    for assignment in outcome.assignments:
        PlanRider.objects.filter(pk=assignment.rider_pk).update(
            pull_duration_s=assignment.pull_duration_s, zero_pull=assignment.zero_pull, order=assignment.order
        )
    plan.target_speed_kph = outcome.speed_kph
    plan.save()
    result = compute.compute_plan(plan)

    assert result.status.fits
    assert result.status.at_fastest
    pulls = [r.rider.pull_duration_s for r in result.riders if not r.rider.zero_pull]
    assert pulls == sorted(pulls, reverse=True)  # the longest pull first
    assert all(r.rider.zero_pull for r in result.riders[len(pulls) :])  # then the riders who sit in

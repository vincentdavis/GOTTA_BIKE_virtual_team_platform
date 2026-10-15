"""Optimize carries out the plan's goal, and Pull Stats says whether the plan fits.

A plan's goal has two halves: how fast (as fast as the limits allow, or hold the target speed)
and how the pull lengths are set (for even effort, for the most W′ in reserve, or kept as they
are). Every rider with an FTP is held to the same two limits whichever is chosen.
"""

import pytest
from django.urls import reverse

from apps.ttt_planner.models import PlanRider, TttPlan
from apps.ttt_planner.services import compute
from apps.ttt_planner.views import GOAL_SENTENCES

# Invented riders: (name, kg, cm, FTP, CP, W′ J).
TEAM = [
    ("A", 75, 180, 320, 315, 21000),
    ("B", 68, 175, 280, 275, 18000),
    ("C", 82, 185, 300, 290, 24000),
    ("D", 62, 168, 230, 225, 15000),
    ("E", 90, 190, 340, 335, 26000),
    ("F", 70, 177, 290, 285, 19000),
]


@pytest.fixture
def plan(team_member):
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    for order, (name, kg, cm, ftp, cp, w_prime) in enumerate(TEAM):
        PlanRider.objects.create(
            plan=plan,
            order=order,
            name=name,
            weight_kg=kg,
            height_cm=cm,
            ftp_w=ftp,
            cp_w=cp,
            w_prime_j=w_prime,
            pull_duration_s=60,
        )
    return plan


def _goal(plan, speed_goal, pull_rule, **fields):
    plan.speed_goal, plan.pull_rule = speed_goal, pull_rule
    for name, value in fields.items():
        setattr(plan, name, value)
    plan.save()
    return plan


def _apply(plan, outcome):
    """Write an outcome to the plan, as the optimize view does.

    Returns:
        The plan's results, recomputed.

    """
    for assignment in outcome.assignments or []:
        PlanRider.objects.filter(pk=assignment.rider_pk).update(
            pull_duration_s=assignment.pull_duration_s, zero_pull=assignment.zero_pull, order=assignment.order
        )
    plan.target_speed_kph = outcome.speed_kph
    plan.save()
    return compute.compute_plan(plan)


def _on_the_grid(outcome, limits):
    for assignment in outcome.assignments:
        if not assignment.zero_pull:
            assert limits.min_pull_s <= assignment.pull_duration_s <= limits.max_pull_s
            assert assignment.pull_duration_s % limits.step_s == 0


# --- the goal ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_new_plan_goes_as_fast_as_the_limits_allow_with_even_effort(team_member):
    plan = TttPlan.objects.create(created_by=team_member)

    assert (plan.speed_goal, plan.pull_rule) == (compute.FASTEST, compute.EVEN)
    assert set(TttPlan.SpeedGoal.values) == {compute.FASTEST, compute.HOLD}
    assert set(TttPlan.PullRule.values) == {compute.EVEN, compute.RESERVE, compute.KEEP}


@pytest.mark.django_db
def test_fastest_keeping_the_pulls_is_the_fastest_those_pulls_allow(plan):
    outcome = compute.optimize_plan(_goal(plan, "fastest", "keep"))

    assert outcome.assignments is None
    assert outcome.speed_kph == compute.sustainable_speed(plan)


@pytest.mark.django_db
def test_holding_with_the_pulls_kept_changes_nothing(plan):
    outcome = compute.optimize_plan(_goal(plan, "hold", "keep", target_speed_kph=41.3))

    assert outcome.assignments is None
    assert outcome.speed_kph == pytest.approx(41.3)


@pytest.mark.parametrize("rule", ["even", "reserve"])
@pytest.mark.django_db
def test_fastest_keeps_every_rider_inside_both_limits(plan, rule):
    outcome = compute.optimize_plan(_goal(plan, "fastest", rule))
    _on_the_grid(outcome, compute.Limits.for_plan(plan))

    result = _apply(plan, outcome)

    assert result.status.fits
    assert result.highest_if <= plan.target_if
    assert result.lowest_w_prime_pct >= plan.w_prime_reserve_pct
    # The speed is set by the keep-my-pulls rule, so optimizing again with the pulls kept changes nothing.
    assert compute.sustainable_speed(plan) == outcome.speed_kph
    assert result.status.at_fastest


@pytest.mark.django_db
def test_holding_a_speed_with_even_effort_evens_out_lopsided_pulls(plan):
    # The weakest rider pulls longest: the hardest-working rider is far over everyone else.
    plan.riders.filter(name="D").update(pull_duration_s=150)
    _goal(plan, "hold", "even", target_speed_kph=41)
    before = compute.compute_plan(plan).highest_if

    outcome = compute.optimize_plan(plan)
    _on_the_grid(outcome, compute.Limits.for_plan(plan))
    result = _apply(plan, outcome)

    assert not outcome.relaxed
    assert float(plan.target_speed_kph) == pytest.approx(41)  # the speed is held
    assert result.highest_if < before
    assert result.status.fits


@pytest.mark.django_db
def test_most_w_prime_keeps_more_in_hand_and_even_effort_a_lower_if(plan):
    speed = 41.5
    even = _apply(plan, compute.optimize_plan(_goal(plan, "hold", "even", target_speed_kph=speed)))
    reserve = _apply(plan, compute.optimize_plan(_goal(plan, "hold", "reserve", target_speed_kph=speed)))

    assert reserve.lowest_w_prime_pct >= even.lowest_w_prime_pct
    assert even.highest_if <= reserve.highest_if
    assert reserve.highest_if <= plan.target_if  # Most W′ still keeps IF at or under Target IF


@pytest.mark.parametrize("rule", ["even", "reserve"])
@pytest.mark.django_db
def test_holding_a_speed_too_fast_for_the_limits_still_sets_pulls_and_says_so(plan, rule):
    outcome = compute.optimize_plan(_goal(plan, "hold", rule, target_speed_kph=48))

    assert outcome.relaxed
    assert outcome.speed_kph == pytest.approx(48)
    assert outcome.assignments
    assert not _apply(plan, outcome).status.fits


@pytest.mark.django_db
def test_riders_without_an_ftp_keep_their_pulls_and_go_last(plan):
    guest = PlanRider.objects.create(plan=plan, order=0, name="Guest", weight_kg=70, height_cm=175, pull_duration_s=45)

    outcome = compute.optimize_plan(plan)
    last = outcome.assignments[-1]

    assert (last.rider_pk, last.pull_duration_s, last.zero_pull) == (guest.pk, 45, False)
    assert last.order == len(TEAM)


@pytest.mark.django_db
def test_without_an_ftp_there_is_nothing_to_optimize(team_member):
    plan = TttPlan.objects.create(created_by=team_member)
    PlanRider.objects.create(plan=plan, order=0, name="Guest", weight_kg=70, height_cm=175, pull_duration_s=45)

    assert compute.optimize_plan(plan) is None


# --- the fit status ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_status_names_who_stops_the_team_going_faster(plan):
    plan.target_speed_kph = compute.sustainable_speed(plan)
    plan.save()

    status = compute.compute_plan(plan).status

    assert status.fits
    assert status.at_fastest
    assert status.held_back_by  # somebody sets the speed
    faster = compute.compute_plan(_goal(plan, "hold", "keep", target_speed_kph=status.fastest_kph + 0.1))
    over = {r.rider.name for r in faster.riders if r.over_target}
    assert {h.name for h in status.held_back_by} <= over


@pytest.mark.django_db
def test_a_rider_with_little_w_prime_is_named_for_their_w_prime(plan):
    plan.riders.filter(name="A").update(w_prime_j=2000, pull_duration_s=180)

    status = compute.compute_plan(plan).status

    assert ("A", "w_prime") in {(h.name, h.limit) for h in status.held_back_by}


@pytest.mark.django_db
def test_the_schedule_says_whether_the_plan_fits(auth_client, plan):
    plan.target_speed_kph = compute.sustainable_speed(plan)
    plan.save()
    fits = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()

    plan.target_speed_kph = 47
    plan.save()
    over = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()

    assert ">Fits<" in fits
    assert "the fastest these pull lengths allow" in fits
    assert ">Over a limit<" in over
    assert "past a limit" in over


# --- the page and the endpoint -----------------------------------------------------------------


@pytest.mark.django_db
def test_the_card_says_what_optimize_will_do(auth_client, plan):
    _goal(plan, "hold", "reserve")
    body = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()

    assert GOAL_SENTENCES["hold-reserve"] in body
    assert 'id="ttt-goal-sentences"' in body  # every sentence, for the page to switch between


@pytest.mark.django_db
def test_holding_with_the_pulls_kept_disables_optimize(auth_client, plan):
    _goal(plan, "hold", "keep")
    body = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()

    button = body[body.index('id="ttt-optimize"') :]
    assert "disabled" in button[: button.index(">")]


@pytest.mark.django_db
def test_optimize_saves_the_goal_on_screen_and_reports_back(auth_client, plan):
    resp = auth_client.post(
        reverse("ttt_planner:optimize", args=[plan.pk]),
        {"speed_goal": "fastest", "pull_rule": "reserve", "target_if": "0.9"},
    )

    plan.refresh_from_db()
    body = resp.content.decode()
    assert (plan.speed_goal, plan.pull_rule, plan.target_if) == ("fastest", "reserve", pytest.approx(0.9))
    assert f'value="{plan.target_speed_kph}"' in body  # the speed field, sent back out of band
    # Under the button, in the plan body the response swaps in...
    shown = body[body.index("data-ttt-optimize-result") : body.index('id="ttt-stats-heading"')]
    assert "the fastest the limits allow" in shown
    assert "most W′ in reserve" in shown
    # ...and for screen readers, into the live region outside the body, which keeps its role.
    announcer = body[body.index('id="ttt-optimize-announcer"') :]
    assert 'hx-swap-oob="innerHTML"' in announcer[: announcer.index(">")]
    assert "the fastest the limits allow" in announcer


@pytest.mark.django_db
def test_optimize_holding_with_the_pulls_kept_says_there_is_nothing_to_change(auth_client, plan):
    before = list(plan.riders.values_list("pk", "pull_duration_s", "order"))

    resp = auth_client.post(
        reverse("ttt_planner:optimize", args=[plan.pk]),
        {"speed_goal": "hold", "pull_rule": "keep", "target_speed_kph": "40"},
    )

    assert "Nothing to change" in resp.content.decode()
    assert list(plan.riders.values_list("pk", "pull_duration_s", "order")) == before


@pytest.mark.django_db
def test_typing_a_new_speed_means_holding_it(auth_client, plan):
    url = reverse("ttt_planner:update", args=[plan.pk])

    auth_client.post(url, {"speed_goal": "fastest", "target_speed_kph": "40.0"}, HTTP_HX_REQUEST="true")
    plan.refresh_from_db()
    assert plan.speed_goal == "fastest"  # the same speed, sent with the rest of the card

    auth_client.post(url, {"speed_goal": "fastest", "target_speed_kph": "43.5"}, HTTP_HX_REQUEST="true")
    plan.refresh_from_db()
    assert (plan.speed_goal, float(plan.target_speed_kph)) == ("hold", pytest.approx(43.5))


@pytest.mark.django_db
def test_an_unknown_goal_is_ignored(auth_client, plan):
    auth_client.post(
        reverse("ttt_planner:update", args=[plan.pk]),
        {"speed_goal": "warp", "pull_rule": "chaos"},
        HTTP_HX_REQUEST="true",
    )

    plan.refresh_from_db()
    assert (plan.speed_goal, plan.pull_rule) == ("fastest", "even")

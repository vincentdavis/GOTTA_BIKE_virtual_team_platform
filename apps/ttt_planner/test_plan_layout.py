"""The plan page's cards, the help page, and the controls that moved between them.

Plan and Course are separate cards; the edit-squad picker lives in Add riders; the plan's goal
(how fast, how the pulls are set), its limits, the CdA coefficient and the draft savings live in
Parameters, closed until opened; Optimize sits just above Pull Stats, saying what it will do.
"""

import re
from datetime import timedelta

import pytest
from constance.test import override_config
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, Squad
from apps.ttt_planner.models import PlanRider, TttPlan


def _plan(owner, **kwargs):
    plan = TttPlan.objects.create(created_by=owner, name="Mine", target_speed_kph=40, **kwargs)
    PlanRider.objects.create(plan=plan, order=0, name="Ana", weight_kg=70, height_cm=175, ftp_w=300)
    PlanRider.objects.create(plan=plan, order=1, name="Bo", weight_kg=80, height_cm=185, ftp_w=260)
    return plan


def _page(client, plan, query=""):
    resp = client.get(reverse("ttt_planner:detail", args=[plan.pk]) + query)
    assert resp.status_code == 200
    return resp.content.decode()


def _card(body, heading_id):
    """Cut out the card whose heading has this id.

    Returns:
        The HTML from that heading up to the next card heading.

    """
    start = body.index(f'id="{heading_id}"')
    nxt = body.find('class="card-title"', body.index("</h2>", start))
    return body[start : nxt if nxt != -1 else len(body)]


# --- where things live -------------------------------------------------------------------------


@pytest.mark.django_db
def test_course_has_its_own_card(auth_client, team_member):
    body = _page(auth_client, _plan(team_member))
    course = _card(body, "ttt-course-heading")

    for field in ('name="route"', 'name="course_name"', 'name="course_type"'):
        assert field in course, field
    assert 'name="target_if"' not in course


@pytest.mark.django_db
def test_the_edit_squad_picker_is_in_add_riders(auth_client, team_member):
    today = timezone.now().date()
    event = Event.objects.create(
        title="Series", start_date=today - timedelta(days=1), end_date=today + timedelta(days=5), visible=True
    )
    Squad.objects.create(event=event, name="Alpha")
    body = _page(auth_client, _plan(team_member))

    assert 'name="edit_squad"' in _card(body, "ttt-add-heading")
    assert 'name="edit_squad"' not in _card(body, "ttt-plan-heading")


@pytest.mark.django_db
def test_parameters_hold_the_goal_and_the_limits_closed_until_opened(auth_client, team_member):
    plan = _plan(team_member)
    body = _page(auth_client, plan)
    params = _card(body, "ttt-params-heading")

    for control in (
        'name="speed_goal"',
        'name="pull_rule"',
        'name="target_speed_kph"',
        'name="target_if"',
        'name="w_prime_reserve_pct"',
        'name="cda_coef"',
        'id="ttt-draft-savings"',
    ):
        assert control in params, control
    assert body.index('id="ttt-params-heading"') < body.index('id="ttt-schedule-heading"')
    opening = body[body.index('<details id="ttt-params"') :]
    assert " open" not in opening[: opening.index(">")]  # closed by default
    assert reverse("ttt_planner:optimize", args=[plan.pk]) not in params


@pytest.mark.django_db
def test_optimize_sits_just_above_pull_stats_saying_what_it_will_do(auth_client, team_member):
    plan = _plan(team_member)
    body = _page(auth_client, plan)

    optimize = body.index(reverse("ttt_planner:optimize", args=[plan.pk]))
    assert body.index('id="ttt-schedule-heading"') < optimize < body.index('id="ttt-stats-heading"')
    bar = body[optimize : body.index('id="ttt-stats-heading"')]
    assert 'id="ttt-goal-sentence"' in bar
    assert 'href="#ttt-params"' in bar  # and a way back to the goal it carries out
    button = body[body.rindex("<button", 0, optimize) : optimize]
    assert "btn-block" in button  # full width


@pytest.mark.django_db
def test_a_read_only_viewer_sees_the_parameters_but_no_controls(auth_client, team_member, app_admin):
    plan = _plan(team_member, target_if=0.92)
    auth_client.force_login(app_admin)
    params = _card(_page(auth_client, plan), "ttt-params-heading")

    assert "0.92" in params
    assert "As fast as the limits allow" in params  # the goal, as text
    assert 'name="target_if"' not in params
    assert reverse("ttt_planner:optimize", args=[plan.pk]) not in params


# --- the help page -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_plan_page_and_the_list_link_to_the_help_page(auth_client, team_member):
    help_url = reverse("ttt_planner:help")

    assert f'href="{help_url}"' in _page(auth_client, _plan(team_member))
    assert f'href="{help_url}"' in auth_client.get(reverse("ttt_planner:list")).content.decode()


@pytest.mark.django_db
def test_the_help_page_quotes_the_live_settings(auth_client):
    with override_config(TTT_CRR=0.0051, TTT_CDA_COEF=0.0299, TTT_DRAFT_SAVINGS="[0, 21.5, 33]"):
        body = auth_client.get(reverse("ttt_planner:help")).content.decode()

    assert "0.0051" in body
    assert "0.0299" in body
    assert "21.5%" in body
    for anchor in ("targets", "metrics", "physics", "draft", "time", "limits"):
        assert f'id="{anchor}"' in body, anchor


@pytest.mark.django_db
def test_the_help_page_needs_team_membership(client, user):
    client.force_login(user)

    assert client.get(reverse("ttt_planner:help")).status_code == 403


# --- behaviour that moved with the controls ----------------------------------------------------


@pytest.mark.django_db
def test_optimize_uses_the_target_if_sent_with_the_click(auth_client, team_member):
    """The field's own save may not have landed when the button is pressed."""
    plan = _plan(team_member, target_if=0.95)

    auth_client.post(reverse("ttt_planner:optimize", args=[plan.pk]), {"target_if": "0.80"})
    plan.refresh_from_db()
    slower = float(plan.target_speed_kph)

    assert plan.target_if == pytest.approx(0.80)
    auth_client.post(reverse("ttt_planner:optimize", args=[plan.pk]), {"target_if": "0.95"})
    plan.refresh_from_db()
    assert float(plan.target_speed_kph) > slower


@pytest.mark.django_db
def test_resetting_the_draft_savings_refreshes_the_inputs_out_of_band(auth_client, team_member):
    plan = _plan(team_member, draft_savings=[0.0, 0.1, 0.2])
    url = reverse("ttt_planner:draft_savings_update", args=[plan.pk])

    edited = auth_client.post(url, {"saving": ["15", "25"]}).content.decode()
    reset = auth_client.post(url, {"reset": "1"}).content.decode()

    # An ordinary edit leaves the inputs alone, so the cell being typed in keeps focus.
    assert 'id="ttt-draft-savings"' not in edited
    assert 'id="ttt-draft-savings" hx-swap-oob="true"' in reset
    plan.refresh_from_db()
    assert plan.draft_savings == []


@pytest.mark.django_db
def test_saving_the_parameters_does_not_pin_the_site_cda_default(auth_client, team_member):
    """The field shows the default as a placeholder, so a full-form save sends it blank."""
    plan = _plan(team_member)
    body = _page(auth_client, plan)

    assert re.search(r'name="cda_coef"\s+value=""', body)
    auth_client.post(
        reverse("ttt_planner:update", args=[plan.pk]),
        {"target_speed_kph": "41", "target_if": "0.95", "cda_coef": ""},
        HTTP_HX_REQUEST="true",
    )
    plan.refresh_from_db()
    assert plan.cda_coef is None


# --- names a screen reader needs ---------------------------------------------------------------


@pytest.mark.django_db
def test_row_controls_say_what_they_do(auth_client, team_member):
    body = _page(auth_client, _plan(team_member))

    assert 'aria-label="Move Ana up in the pull order"' in body
    assert 'aria-label="Move Ana down in the pull order"' in body
    assert 'aria-label="Pull length for Ana, seconds (0 to 300)"' in body
    assert 'aria-label="Ana takes no pulls"' in body
    assert 'aria-label="Aero power saved at position 2, percent"' in body
    assert '<label for="ttt-rider-q"' in body

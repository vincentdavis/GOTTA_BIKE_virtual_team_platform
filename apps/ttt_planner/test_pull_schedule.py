"""The pull schedule: what each rider rides, ready to paste into Discord.

Above Pull Stats (the planning table), one row per rider in pull order: pull length, pull
W/kg and watts, then the watts to hold the speed sitting in at each wheel from the 2nd to
the last. "Copy for Discord" copies it as a code block, laid out by ``services/schedule.py``.
"""

import pytest
from django.urls import reverse

from apps.ttt_planner.models import PlanRider, TttPlan
from apps.ttt_planner.services import compute, physics, schedule

# Invented riders: (name, kg, cm, FTP, pull s).
TEAM = [("Ana", 62, 168, 260, 60), ("Bo", 75, 178, 320, 90), ("Cy", 88, 188, 340, 120), ("Di", 70, 175, 280, 45)]


@pytest.fixture
def plan(team_member):
    plan = TttPlan.objects.create(
        created_by=team_member, name="Saturday TTT", course_name="Tempus", target_speed_kph=44
    )
    for order, (name, kg, cm, ftp, pull) in enumerate(TEAM):
        PlanRider.objects.create(
            plan=plan, order=order, name=name, weight_kg=kg, height_cm=cm, ftp_w=ftp, pull_duration_s=pull
        )
    return plan


def _page(client, plan):
    return client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()


def _table_lines(text):
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Rider"))
    return lines[start : start + 1 + len(TEAM)]


# --- the numbers ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_every_rider_gets_a_power_for_each_wheel_behind_the_front(plan):
    result = compute.compute_plan(plan)
    params = compute.params_for_plan(plan)

    assert result.wheel_positions == [2, 3, 4]
    bo = result.riders[1]

    def sitting(position):
        factor = 1 - physics.draft_savings_fraction(position, params)
        return round(physics.power_for_speed(44, weight_kg=75, height_cm=178, draft_factor=factor, params=params))

    expected = [sitting(p) for p in (2, 3, 4)]
    assert bo.wheel_powers == expected
    # Deeper in the line, more draft, fewer watts; and every wheel is easier than the front.
    assert bo.wheel_powers == sorted(bo.wheel_powers, reverse=True)
    assert bo.wheel_powers[0] < bo.pull_power_w


@pytest.mark.parametrize(
    ("number", "word"), [(2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st")]
)
def test_wheels_are_named_as_ordinals(number, word):
    assert schedule.ordinal(number) == word


# --- the Discord text ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_discord_text_is_one_code_block_with_lined_up_columns(plan):
    text = schedule.discord_text(plan, compute.compute_plan(plan))
    lines = text.splitlines()

    assert lines[0] == lines[-1] == "```"
    assert lines[1] == "Saturday TTT · Tempus"
    assert lines[2] == "44.0 kph on the flat"
    table = _table_lines(text)
    assert table[0].split() == ["Rider", "Pull", "W/kg", "W", "2nd", "3rd", "4th"]
    assert table[2].split()[:2] == ["Bo", "90s"]
    # Numbers are right-aligned to a common edge, so every row of the table ends in the same column.
    assert len({len(line) for line in table}) == 1
    assert "2nd, 3rd…: watts sitting in at that wheel" in text


@pytest.mark.django_db
def test_a_rider_who_takes_no_pulls_says_so(plan):
    plan.riders.filter(name="Di").update(zero_pull=True)

    table = _table_lines(schedule.discord_text(plan, compute.compute_plan(plan)))

    assert table[4].split()[:4] == ["Di", "no", "pull", "-"]


@pytest.mark.django_db
def test_names_cannot_break_the_code_block_or_the_layout(plan):
    plan.riders.filter(name="Ana").update(name="Ana `the` Long-Named Climber")
    plan.name = "Week ``` 3"
    plan.save()

    text = schedule.discord_text(plan, compute.compute_plan(plan))

    assert text.count("```") == 2  # only the fences
    assert "Ana 'the' Long" in text  # backticks become apostrophes, the name is cut short
    assert "Climber" not in text


@pytest.mark.django_db
def test_a_full_team_fits_in_one_discord_message(team_member):
    plan = TttPlan.objects.create(created_by=team_member, name="Eight", target_speed_kph=46)
    for order in range(8):
        PlanRider.objects.create(
            plan=plan,
            order=order,
            name=f"Rider number {order}",
            weight_kg=70 + order,
            height_cm=175,
            ftp_w=300,
            pull_duration_s=60,
        )

    assert len(schedule.discord_text(plan, compute.compute_plan(plan))) < 2000


# --- the page ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_schedule_sits_above_pull_stats_with_its_wheel_columns(auth_client, plan):
    body = _page(auth_client, plan)

    assert body.index('id="ttt-schedule-heading"') < body.index('id="ttt-stats-heading"')
    assert ">Pull Stats<" in body
    schedule_card = body[body.index('id="ttt-schedule-heading"') : body.index('id="ttt-stats-heading"')]
    for header in (">Pull (s)<", ">Pull W/kg<", ">Pull W<", ">2nd<", ">3rd<", ">4th<"):
        assert header in schedule_card, header
    assert ">5th<" not in schedule_card  # four riders: the front and three wheels behind it
    assert 'data-ttt-copy-text="ttt-discord-text"' in schedule_card
    assert 'id="ttt-discord-text"' in schedule_card


@pytest.mark.django_db
def test_the_text_on_the_page_is_escaped_markup_free(auth_client, plan):
    plan.riders.filter(name="Ana").update(name="<b>Ana</b>")

    body = _page(auth_client, plan)
    textarea = body[body.index('id="ttt-discord-text"') :]
    textarea = textarea[: textarea.index("</textarea>")]

    assert "<b>" not in textarea
    assert "&lt;b&gt;Ana" in textarea


@pytest.mark.django_db
def test_a_read_only_viewer_can_copy_it_too(auth_client, plan, app_admin):
    auth_client.force_login(app_admin)

    body = _page(auth_client, plan)

    assert 'data-ttt-copy-text="ttt-discord-text"' in body
    assert reverse("ttt_planner:optimize", args=[plan.pk]) not in body  # this really is the read-only view


@pytest.mark.django_db
def test_no_schedule_before_there_are_riders(auth_client, team_member):
    plan = TttPlan.objects.create(created_by=team_member, name="Empty")

    body = _page(auth_client, plan)

    assert 'id="ttt-schedule-heading"' not in body
    assert 'id="ttt-stats-heading"' in body

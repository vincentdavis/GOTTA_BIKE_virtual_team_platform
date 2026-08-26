"""Copying the TTT pull schedule as an image.

Mirrors the ladder planner's per-tab capture. The mechanics -- expanding scroll wrappers,
pinning to the full scroll extent, clipboard with a download fallback -- live in one shared
partial so the two cannot drift; each planner owns only its own button placement.
"""

import pytest
from django.urls import reverse

from apps.ladder_planner.models import LadderMatchup
from apps.ttt_planner.models import PlanRider, TttPlan

# Unique to the rendered control -- the attribute selector also appears in the JS.
BUTTON = 'aria-label="Copy the pull schedule as an image"'


def _rider(plan, name, order=0):
    """Add one rider to a plan, enough for the schedule to render.

    Returns:
        The rider.

    """
    return PlanRider.objects.create(
        plan=plan, order=order, name=name, weight_kg=72, height_cm=178,
        ftp_w=300, pull_duration_s=45,
    )


def _plan_page(client, plan):
    """Load a TTT plan detail page.

    Returns:
        The decoded body.

    """
    response = client.get(reverse("ttt_planner:detail", args=[plan.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_the_copy_button_appears_once_the_plan_has_riders(auth_client, team_member) -> None:
    """Nothing to copy before then, so the control would be dead."""
    plan = TttPlan.objects.create(created_by=team_member, name="Mine")

    assert BUTTON not in _plan_page(auth_client, plan)

    _rider(plan, "Ana")

    assert BUTTON in _plan_page(auth_client, plan)


@pytest.mark.django_db
def test_a_read_only_viewer_can_still_copy(auth_client, team_member, app_admin) -> None:
    """Someone opening a shared link wants the image as much as the owner does.

    The other pull-schedule buttons are gated on can_edit; Copy deliberately is not.
    """
    plan = TttPlan.objects.create(created_by=team_member, name="Mine")
    _rider(plan, "Ana")
    auth_client.force_login(app_admin)

    body = _plan_page(auth_client, plan)

    assert BUTTON in body
    assert "Auto-balance" not in body  # confirms this really is the read-only view


@pytest.mark.django_db
def test_the_capture_targets_the_pull_schedule_card(auth_client, team_member) -> None:
    """The button finds its card by attribute, so the two must both be present."""
    plan = TttPlan.objects.create(created_by=team_member, name="Mine")
    _rider(plan, "Ana")

    body = _plan_page(auth_client, plan)

    assert "data-ttt-capture" in body
    assert 'excludeClass: "ttt-capture-btn"' in body  # keeps the button out of the PNG


@pytest.mark.django_db
def test_both_planners_share_one_capture_implementation(auth_client, team_member) -> None:
    """The subtle parts are identical, so a second copy would drift."""
    plan = TttPlan.objects.create(created_by=team_member, name="Mine")
    _rider(plan, "Ana")
    matchup = LadderMatchup.objects.create(created_by=team_member, name="Mine")

    ttt = _plan_page(auth_client, plan)
    ladder = auth_client.get(reverse("ladder_planner:detail", args=[matchup.pk])).content.decode()

    for body in (ttt, ladder):
        assert "window.copyAsImage" in body
        assert "copy-capturing" in body

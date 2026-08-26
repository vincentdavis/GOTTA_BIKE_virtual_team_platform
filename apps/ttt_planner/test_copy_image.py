"""Copying the TTT pull schedule as an image.

Mirrors the ladder planner's per-tab capture. The mechanics -- expanding scroll wrappers,
pinning to the full scroll extent, clipboard with a download fallback -- live in one shared
partial so the two cannot drift; each planner owns only its own button placement.
"""

from unittest.mock import patch

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
def test_the_capture_targets_a_ride_sheet_not_the_planning_table(auth_client, team_member) -> None:
    """Only what a rider needs on the bike, and no number inputs in the PNG.

    The visible table is a planning view (kg, CdA, FTP, IF, NP, TSS) whose Pull (s) cell
    is an <input>. The image comes from a separate off-screen sheet instead.
    """
    plan = TttPlan.objects.create(created_by=team_member, name="Mine")
    _rider(plan, "Ana")

    body = _plan_page(auth_client, plan)
    sheet = body[body.index("data-ttt-capture-sheet"):]
    sheet = sheet[: sheet.index("</table>")]

    for wanted in ("Pull W", "W/kg", "Pull (s)", "Rider"):
        assert wanted in sheet, wanted
    for planning_only in ("CdA", "FTP", "TSS", "NP"):
        assert planning_only not in sheet, planning_only
    # The offset must sit on the wrapper, never on the captured sheet itself:
    # html-to-image clones the target with its own computed styles, so an offset there
    # travels into the clone and the PNG comes out blank.
    stage = body[: body.index("data-ttt-capture-sheet")]
    assert "opacity:0" in stage.rsplit("<div", 1)[-1] or "opacity:0" in stage[-400:]
    sheet_tag = body[body.index("data-ttt-capture-sheet"):]
    sheet_tag = sheet_tag[: sheet_tag.index(">")]
    assert "position:" not in sheet_tag
    assert "-10000" not in sheet_tag


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


@pytest.mark.django_db
def test_the_zwiftgopher_panel_has_its_own_copy(auth_client, team_member) -> None:
    """Its suggested order is the one people actually ride, so it needs the same button."""
    plan = TttPlan.objects.create(
        created_by=team_member, name="Mine",
        zwiftgopher_result={"ok": True, "riders": [
            {"order": 1, "name": "Ana", "zwift_id": "12345", "pull_power": 300, "pull_power_wkg": 4.2,
             "pull_duration": 45, "avg_power": 250, "if_percent": 92, "pull_ftp_percent": 110},
        ]},
    )

    with patch("apps.ttt_planner.services.zwiftgopher_client.is_configured", return_value=True):
        body = auth_client.get(
            reverse("ttt_planner:zwiftgopher_panel", args=[plan.pk])
        ).content.decode()

    assert "data-gopher-capture-sheet" in body
    assert 'data-capture-target="[data-gopher-capture-sheet]"' in body

    sheet = body[body.index("data-gopher-capture-sheet"):]
    sheet = sheet[: sheet.index("</table>")]
    assert "Ana" in sheet
    for planning_only in ("%FTP", "Avg W", "IF"):
        assert planning_only not in sheet, planning_only

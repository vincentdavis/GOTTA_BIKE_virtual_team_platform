"""Views for the club ladder planner.

The detail page is a server-rendered DaisyUI page with tabbed comparison views;
mutations are HTMX POSTs that return the ``_matchup_body`` partial (the tabs),
keeping the page in sync after every edit. CSRF is supplied globally via
``hx-headers`` on ``<body>`` in base.html.
"""

from __future__ import annotations

import re

import logfire
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import team_member_required
from apps.ladder_planner.models import CourseProfile, LadderMatchup, LadderRider, Side
from apps.ladder_planner.services import compute, roster

_MAX_OPPONENTS_PER_REQUEST = 50


def _can_edit(matchup: LadderMatchup, user) -> bool:
    """Return whether a user may edit a matchup.

    Args:
        matchup: The matchup.
        user: The requesting user.

    Returns:
        True for the matchup owner or a superuser.

    """
    return user.is_superuser or matchup.created_by_id == user.id


def _render_body(
    request: HttpRequest,
    matchup: LadderMatchup,
    *,
    can_edit: bool,
    notice: str = "",
    error: str = "",
) -> str:
    """Render the tabbed comparison body partial for a matchup.

    Args:
        request: The request (for template context/CSRF).
        matchup: The matchup.
        can_edit: Whether edit controls should be shown.
        notice: Optional success message shown at the top of the body.
        error: Optional error message shown at the top of the body.

    Returns:
        Rendered HTML string of the ``_matchup_body`` partial.

    """
    return render_to_string(
        "ladder_planner/_matchup_body.html",
        {
            "matchup": matchup,
            "summary": compute.matchup_summary(matchup),
            "can_edit": can_edit,
            "notice": notice,
            "error": error,
            "Side": Side,
        },
        request=request,
    )


def _get_editable(request: HttpRequest, matchup_id: str) -> LadderMatchup | None:
    """Fetch a matchup if the user may edit it, else None.

    Args:
        request: The request.
        matchup_id: Matchup UUID.

    Returns:
        The matchup, or None if not editable (caller returns 403).

    """
    matchup = get_object_or_404(LadderMatchup, pk=matchup_id)
    return matchup if _can_edit(matchup, request.user) else None


def _next_order(matchup: LadderMatchup, side: str) -> int:
    """Return the next display-order index for a new rider on a side.

    Args:
        matchup: The matchup.
        side: The side value.

    Returns:
        One past the current maximum order for that side (0 if empty).

    """
    last = matchup.riders.filter(side=side).order_by("-order").first()
    return (last.order + 1) if last else 0


@login_required
@team_member_required(raise_exception=True)
@require_GET
def matchup_list(request: HttpRequest) -> HttpResponse:
    """List the current user's ladder matchups.

    Returns:
        The matchup list page.

    """
    matchups = LadderMatchup.objects.filter(created_by=request.user).annotate(rider_count=Count("riders"))
    return render(request, "ladder_planner/list.html", {"matchups": matchups})


@login_required
@team_member_required(raise_exception=True)
@require_POST
def matchup_create(request: HttpRequest) -> HttpResponse:
    """Create a new empty matchup and redirect to it.

    Returns:
        Redirect to the new matchup's detail page.

    """
    profile = request.POST.get("course_profile", CourseProfile.ROLLING)
    matchup = LadderMatchup.objects.create(
        created_by=request.user,
        name=request.POST.get("name", "").strip(),
        our_team_name=request.POST.get("our_team_name", "").strip(),
        opponent_team_name=request.POST.get("opponent_team_name", "").strip(),
        course_name=request.POST.get("course_name", "").strip(),
        course_profile=profile if profile in CourseProfile.values else CourseProfile.ROLLING,
    )
    logfire.info("Ladder matchup created", matchup_id=str(matchup.pk), user_id=request.user.id)
    return redirect("ladder_planner:detail", matchup_id=matchup.pk)


@login_required
@team_member_required(raise_exception=True)
@require_GET
def matchup_detail(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Show a matchup. Read-only for non-owners (share link).

    Returns:
        The matchup detail page.

    """
    matchup = get_object_or_404(LadderMatchup, pk=matchup_id)
    can_edit = _can_edit(matchup, request.user)
    return render(
        request,
        "ladder_planner/detail.html",
        {
            "matchup": matchup,
            "summary": compute.matchup_summary(matchup),
            "can_edit": can_edit,
            "course_profiles": CourseProfile.choices,
            "Side": Side,
        },
    )


@login_required
@team_member_required(raise_exception=True)
@require_POST
def matchup_delete(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Delete a matchup (owner only).

    Returns:
        Redirect to the matchup list.

    """
    matchup = get_object_or_404(LadderMatchup, pk=matchup_id)
    if not _can_edit(matchup, request.user):
        return HttpResponse("Permission denied", status=403)
    matchup.delete()
    logfire.info("Ladder matchup deleted", matchup_id=matchup_id, user_id=request.user.id)
    return redirect("ladder_planner:list")


@login_required
@team_member_required(raise_exception=True)
@require_POST
def matchup_update(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Update matchup-level settings (names, course, profile); recompute.

    Returns:
        The refreshed matchup body partial.

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    if "name" in request.POST:
        matchup.name = request.POST.get("name", "").strip()
    if "our_team_name" in request.POST:
        matchup.our_team_name = request.POST.get("our_team_name", "").strip()
    if "opponent_team_name" in request.POST:
        matchup.opponent_team_name = request.POST.get("opponent_team_name", "").strip()
    if "course_name" in request.POST:
        matchup.course_name = request.POST.get("course_name", "").strip()
    if "course_profile" in request.POST:
        profile = request.POST.get("course_profile", "")
        if profile in CourseProfile.values:
            matchup.course_profile = profile

    matchup.save()
    return HttpResponse(_render_body(request, matchup, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_GET
def our_rider_search(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Autocomplete: search our synced riders to add to the matchup.

    Returns:
        The search-results dropdown partial.

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    query = request.GET.get("q", "")
    existing = set(matchup.riders.filter(side=Side.OURS).values_list("zwid", flat=True))
    results = roster.search_our_riders(query, exclude_zwids=existing)
    return render(
        request,
        "ladder_planner/_rider_search.html",
        {"matchup": matchup, "results": results, "query": query.strip()},
    )


@login_required
@team_member_required(raise_exception=True)
@require_POST
def our_rider_add(request: HttpRequest, matchup_id: str, zwid: int) -> HttpResponse:
    """Add one of our riders (by zwid) to the matchup, snapshotting ZR data.

    Returns:
        The refreshed matchup body partial.

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    error = ""
    if not matchup.riders.filter(side=Side.OURS, zwid=zwid).exists():
        data = roster.get_our_rider(zwid)
        if data:
            LadderRider.objects.create(
                matchup=matchup,
                side=Side.OURS,
                order=_next_order(matchup, Side.OURS),
                zwid=zwid,
                name=data["name"],
                zr_data=data,
                fetched_at=timezone.now(),
            )
        else:
            error = "Rider not found in our synced Zwift Racing data."
    return HttpResponse(_render_body(request, matchup, can_edit=True, error=error))


def _parse_zwids(raw: str) -> list[int]:
    """Parse zwids from free text (commas, spaces, or newlines).

    Args:
        raw: The raw textarea content.

    Returns:
        Deduplicated list of positive integers, capped at the per-request max.

    """
    seen: dict[int, None] = {}
    for token in re.split(r"[^0-9]+", raw or ""):
        if token:
            seen.setdefault(int(token), None)
    return list(seen)[:_MAX_OPPONENTS_PER_REQUEST]


@login_required
@team_member_required(raise_exception=True)
@require_POST
def opponents_add(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Add opponent riders by zwid, fetching their data live from Zwift Racing.

    Returns:
        The refreshed matchup body partial (with a notice or error).

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    zwids = _parse_zwids(request.POST.get("zwids", ""))
    if not zwids:
        return HttpResponse(_render_body(request, matchup, can_edit=True, error="Enter one or more Zwift IDs."))

    existing = set(matchup.riders.filter(side=Side.OPPONENT).values_list("zwid", flat=True))
    to_fetch = [z for z in zwids if z not in existing]
    if not to_fetch:
        return HttpResponse(
            _render_body(request, matchup, can_edit=True, notice="Those riders are already on the opponent.")
        )

    riders, error = roster.fetch_opponents(to_fetch)
    if error:
        return HttpResponse(_render_body(request, matchup, can_edit=True, error=error))

    now = timezone.now()
    order = _next_order(matchup, Side.OPPONENT)
    created = 0
    for data in riders:
        zwid = data.get("zwid")
        if not zwid or zwid in existing:
            continue
        LadderRider.objects.create(
            matchup=matchup,
            side=Side.OPPONENT,
            order=order,
            zwid=zwid,
            name=data["name"] or str(zwid),
            zr_data=data,
            fetched_at=now,
        )
        existing.add(zwid)
        order += 1
        created += 1

    missing = len(to_fetch) - created
    notice = f"Added {created} opponent rider(s)." + (f" {missing} zwid(s) returned no data." if missing else "")
    return HttpResponse(_render_body(request, matchup, can_edit=True, notice=notice))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def rider_remove(request: HttpRequest, matchup_id: str, rider_id: int) -> HttpResponse:
    """Remove a rider from the matchup.

    Returns:
        The refreshed matchup body partial.

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    matchup.riders.filter(pk=rider_id).delete()
    return HttpResponse(_render_body(request, matchup, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def rider_toggle(request: HttpRequest, matchup_id: str, rider_id: int) -> HttpResponse:
    """Toggle whether a rider is racing (included in comparisons/scoring).

    Returns:
        The refreshed matchup body partial.

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    rider = get_object_or_404(LadderRider, pk=rider_id, matchup=matchup)
    rider.is_racing = not rider.is_racing
    rider.save(update_fields=["is_racing"])
    return HttpResponse(_render_body(request, matchup, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def matchup_refresh(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Re-fetch ZR data for all riders, overwriting their snapshots.

    Our riders refresh from synced ``ZRRider`` data; opponents are re-fetched
    live from the Zwift Racing API in one batched call.

    Returns:
        The refreshed matchup body partial (with a notice or error).

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    now = timezone.now()
    riders = list(matchup.riders.all())

    # Our side: refresh from local ZRRider snapshots.
    for rider in (r for r in riders if r.side == Side.OURS):
        data = roster.get_our_rider(rider.zwid)
        if data:
            rider.zr_data = data
            rider.name = data["name"] or rider.name
            rider.fetched_at = now
            rider.save(update_fields=["zr_data", "name", "fetched_at"])

    # Opponent side: one batched live fetch.
    error = ""
    opp_riders = [r for r in riders if r.side == Side.OPPONENT]
    if opp_riders:
        fetched, error = roster.fetch_opponents([r.zwid for r in opp_riders])
        by_zwid = {d["zwid"]: d for d in fetched}
        for rider in opp_riders:
            data = by_zwid.get(rider.zwid)
            if data:
                rider.zr_data = data
                rider.name = data["name"] or rider.name
                rider.fetched_at = now
                rider.save(update_fields=["zr_data", "name", "fetched_at"])

    logfire.info("Ladder matchup refreshed", matchup_id=str(matchup.pk), user_id=request.user.id)
    notice = "" if error else "Rider data refreshed."
    return HttpResponse(_render_body(request, matchup, can_edit=True, notice=notice, error=error))

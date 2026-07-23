"""Views for the club ladder planner.

The detail page is a server-rendered DaisyUI page with tabbed comparison views;
mutations are HTMX POSTs that return the ``_matchup_body`` partial (the tabs),
keeping the page in sync after every edit. CSRF is supplied globally via
``hx-headers`` on ``<body>`` in base.html.
"""

from __future__ import annotations

import contextlib
import re

import logfire
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import team_member_required
from apps.events.models import Squad
from apps.ladder_planner.models import CourseProfile, LadderMatchup, LadderRider, Side
from apps.ladder_planner.services import cache, compute, courses, normalize, roster, squads
from apps.ladder_planner.tasks import warm_club
from apps.zwift_data.models import ZwiftRoute

_MAX_OPPONENTS_PER_REQUEST = 50


def _warm_clubs(datas: list[dict]) -> None:
    """Enqueue background club warming for the distinct clubs in freshly-fetched riders.

    Args:
        datas: Normalized rider dicts just fetched live from ZR.

    """
    for club_id in {d.get("club_id") for d in datas if d.get("club_id")}:
        warm_club.enqueue(club_id)


def _add_opponent(matchup: LadderMatchup, data: dict, order: int, now) -> None:
    """Create an opponent LadderRider from a normalized snapshot.

    Args:
        matchup: The matchup.
        data: Normalized rider dict.
        order: Display order.
        now: Snapshot timestamp.

    """
    LadderRider.objects.create(
        matchup=matchup,
        side=Side.OPPONENT,
        order=order,
        zwid=data["zwid"],
        name=data.get("name") or str(data["zwid"]),
        zr_data=data,
        fetched_at=now,
    )


def _can_edit(matchup: LadderMatchup, user) -> bool:
    """Return whether a user may edit a matchup.

    Args:
        matchup: The matchup.
        user: The requesting user.

    Returns:
        True for the matchup owner, a superuser, or a member of the matchup's
        ``edit_squad`` (member, captain, or vice-captain).

    """
    if user.is_superuser or matchup.created_by_id == user.id:
        return True
    return bool(matchup.edit_squad_id) and squads.user_in_squad(matchup.edit_squad, user)


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
    # The add-riders controls live inside the body partial (combined with the
    # rider list), so the squad picker must be supplied on every body swap too.
    my_squads, other_squads = squads.squads_for_picker(request.user) if can_edit else ([], [])
    return render_to_string(
        "ladder_planner/_matchup_body.html",
        {
            "matchup": matchup,
            "summary": compute.matchup_summary(matchup),
            "can_edit": can_edit,
            "notice": notice,
            "error": error,
            "my_squads": my_squads,
            "other_squads": other_squads,
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


# Whitelist of sortable columns -> (ascending, descending) ORM expressions.
# `rider_count` is the Count("riders") annotation added in the view.
LADDER_MATCHUP_SORT: dict[str, tuple[str, str]] = {
    "name": ("name", "-name"),
    "created_by": ("created_by__discord_nickname", "-created_by__discord_nickname"),
    "our_team": ("our_team_name", "-our_team_name"),
    "opponent": ("opponent_team_name", "-opponent_team_name"),
    "profile": ("course_profile", "-course_profile"),
    "riders": ("rider_count", "-rider_count"),
    "updated": ("updated_at", "-updated_at"),
}


def _resolve_ladder_sort(raw_sort: str, raw_dir: str) -> tuple[str, str, str]:
    """Resolve a matchup-list sort request against the whitelist.

    Falls back to ``updated`` descending (most recently modified first) for any
    unknown column or direction.

    Args:
        raw_sort: The raw sort-key querystring value.
        raw_dir: The raw direction querystring value.

    Returns:
        Tuple of (resolved key, resolved direction, ORM order_by expression).

    """
    key = raw_sort if raw_sort in LADDER_MATCHUP_SORT else "updated"
    direction = raw_dir if raw_dir in {"asc", "desc"} else "desc"
    expr = LADDER_MATCHUP_SORT[key][0 if direction == "asc" else 1]
    return key, direction, expr


@login_required
@team_member_required(raise_exception=True)
@require_GET
def matchup_list(request: HttpRequest) -> HttpResponse:
    """List ladder matchups: the current user's first, then other members'.

    Each list sorts independently via namespaced querystring params
    (``my_sort``/``my_dir`` and ``other_sort``/``other_dir``). Both default to
    most-recently-modified first.

    Returns:
        The matchup list page.

    """
    all_matchups = LadderMatchup.objects.annotate(rider_count=Count("riders")).select_related("created_by")

    my_sort, my_dir, my_expr = _resolve_ladder_sort(request.GET.get("my_sort", ""), request.GET.get("my_dir", ""))
    other_sort, other_dir, other_expr = _resolve_ladder_sort(
        request.GET.get("other_sort", ""), request.GET.get("other_dir", "")
    )

    # `pk` is a stable tiebreaker so equal sort values keep a deterministic order.
    matchups = all_matchups.filter(created_by=request.user).order_by(my_expr, "pk")
    other_matchups = all_matchups.exclude(created_by=request.user).order_by(other_expr, "pk")

    return render(
        request,
        "ladder_planner/list.html",
        {
            "matchups": matchups,
            "other_matchups": other_matchups,
            "my_sort": my_sort,
            "my_dir": my_dir,
            "other_sort": other_sort,
            "other_dir": other_dir,
            # Each list's header links preserve the other list's current sort.
            "my_keep_qs": urlencode({"other_sort": other_sort, "other_dir": other_dir}),
            "other_keep_qs": urlencode({"my_sort": my_sort, "my_dir": my_dir}),
        },
    )


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
    matchup = get_object_or_404(LadderMatchup.objects.select_related("edit_squad__event"), pk=matchup_id)
    can_edit = _can_edit(matchup, request.user)
    my_squads, other_squads = squads.squads_for_picker(request.user) if can_edit else ([], [])
    # Whether the currently-selected edit squad appears in the active-event picker.
    # If not (its event has ended), the template renders it as a standalone option
    # so a settings save doesn't silently clear the grant.
    picker_pks = {s["pk"] for s in my_squads} | {s["pk"] for s in other_squads}
    return render(
        request,
        "ladder_planner/detail.html",
        {
            "matchup": matchup,
            "summary": compute.matchup_summary(matchup),
            "can_edit": can_edit,
            "course_profiles": CourseProfile.choices,
            "route_options": courses.route_options() if can_edit else [],
            "my_squads": my_squads,
            "other_squads": other_squads,
            "edit_squad_active": matchup.edit_squad_id in picker_pks,
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
    if "route" in request.POST:
        route_id = request.POST.get("route")
        matchup.route = ZwiftRoute.objects.filter(pk=route_id).first() if route_id else None
    if "course_name" in request.POST:
        matchup.course_name = request.POST.get("course_name", "").strip()
    if "course_profile" in request.POST:
        profile = request.POST.get("course_profile", "")
        if profile in CourseProfile.values:
            matchup.course_profile = profile
    if "edit_squad" in request.POST:
        squad_id = request.POST.get("edit_squad")
        matchup.edit_squad = Squad.objects.filter(pk=squad_id).first() if squad_id else None
    if "cda_coef" in request.POST:
        raw = request.POST.get("cda_coef", "").strip()
        if not raw:
            matchup.cda_coef = None  # fall back to the global TTT_CDA_COEF
        else:
            with contextlib.suppress(ValueError):
                matchup.cda_coef = max(0.0, float(raw))

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
@require_GET
def opponent_search(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Autocomplete: search cached opponent riders (no ZR call).

    Shows cached matches with data age; a miss offers a live fetch-by-zwid.

    Returns:
        The opponent search-results dropdown partial.

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    query = request.GET.get("q", "").strip()
    existing = set(matchup.riders.filter(side=Side.OPPONENT).values_list("zwid", flat=True))
    results = cache.search(query, exclude_zwids=existing)
    fetch_zwid = int(query) if query.isdigit() and int(query) not in existing else None
    return render(
        request,
        "ladder_planner/_opponent_search.html",
        {"matchup": matchup, "results": results, "query": query, "fetch_zwid": fetch_zwid},
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


@login_required
@team_member_required(raise_exception=True)
@require_POST
def our_squad_add(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Add all members of a chosen event squad to our side.

    Members are snapshotted from synced ZR data where available, else added
    name-only. Existing riders are skipped; members without a zwid can't be
    added (the matchup keys riders by zwid).

    Returns:
        The refreshed matchup body partial (with a notice or error).

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    squad = Squad.objects.filter(pk=request.POST.get("squad")).select_related("event").first()
    if squad is None:
        return HttpResponse(_render_body(request, matchup, can_edit=True, error="Squad not found."))

    existing = set(matchup.riders.filter(side=Side.OURS).values_list("zwid", flat=True))
    now = timezone.now()
    order = _next_order(matchup, Side.OURS)
    added = no_zwid = 0
    for user in squads.squad_member_users(squad):
        if not user.zwid:
            no_zwid += 1
            continue
        if user.zwid in existing:
            continue
        data = roster.get_our_rider(user.zwid) or normalize.minimal(
            user.zwid, user.get_full_name() or user.username
        )
        LadderRider.objects.create(
            matchup=matchup,
            side=Side.OURS,
            order=order,
            zwid=user.zwid,
            name=data["name"],
            zr_data=data,
            fetched_at=now,
        )
        existing.add(user.zwid)
        order += 1
        added += 1

    notice = f"Added {added} rider(s) from {squad.name}."
    if no_zwid:
        notice += f" {no_zwid} member(s) skipped (no Zwift ID)."
    logfire.info("Ladder squad added", matchup_id=str(matchup.pk), squad_id=squad.pk, added=added)
    return HttpResponse(_render_body(request, matchup, can_edit=True, notice=notice))


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
def opponent_add(request: HttpRequest, matchup_id: str, zwid: int) -> HttpResponse:
    """Add one opponent from the cache (search-result click); live-fetch on miss.

    Returns:
        The refreshed matchup body partial.

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    error = ""
    if not matchup.riders.filter(side=Side.OPPONENT, zwid=zwid).exists():
        data = cache.get_snapshot(zwid)
        if data is None:
            riders, error = roster.fetch_opponents([zwid])
            data = riders[0] if riders else None
            if data:
                _warm_clubs(riders)
        if data:
            _add_opponent(matchup, data, _next_order(matchup, Side.OPPONENT), timezone.now())
        elif not error:
            error = "No Zwift Racing data found for that rider."
    return HttpResponse(_render_body(request, matchup, can_edit=True, error=error))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def opponents_add(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Add opponents by zwid: use the cache where present, live-fetch the misses.

    Freshly fetched riders trigger a background club warm so their teammates
    become searchable without further live calls.

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
    candidates = [z for z in zwids if z not in existing]
    if not candidates:
        return HttpResponse(
            _render_body(request, matchup, can_edit=True, notice="Those riders are already on the opponent.")
        )

    # Cache-first: only the misses cost a (single, batched) live ZR call.
    snapshots: dict[int, dict] = {}
    misses: list[int] = []
    for zwid in candidates:
        cached = cache.get_snapshot(zwid)
        if cached is not None:
            snapshots[zwid] = cached
        else:
            misses.append(zwid)

    error = ""
    if misses:
        fetched, error = roster.fetch_opponents(misses)
        for data in fetched:
            if data.get("zwid"):
                snapshots[data["zwid"]] = data
        _warm_clubs(fetched)

    now = timezone.now()
    order = _next_order(matchup, Side.OPPONENT)
    created = 0
    for zwid in candidates:
        data = snapshots.get(zwid)
        if data:
            _add_opponent(matchup, data, order, now)
            order += 1
            created += 1

    if error and not created:
        return HttpResponse(_render_body(request, matchup, can_edit=True, error=error))

    missing = len(candidates) - created
    notice = f"Added {created} opponent rider(s)."
    if missing:
        notice += f" {missing} zwid(s) returned no data."
    if error:
        notice += f" ({error})"
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
@require_GET
def matchup_climb(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Render the Climb compare panel on demand.

    Split out of the main body because ``climb_advantage`` is ~90% of the summary
    compute cost; the Climb tab lazy-loads this so roster edits stay fast.

    Returns:
        The climb panel partial.

    """
    matchup = get_object_or_404(LadderMatchup, pk=matchup_id)
    return render(
        request,
        "ladder_planner/_climb_panel.html",
        {
            "matchup": matchup,
            "climb": compute.climb_advantage(matchup),
            "can_edit": _can_edit(matchup, request.user),
        },
    )


@login_required
@team_member_required(raise_exception=True)
@require_POST
def matchup_refresh(request: HttpRequest, matchup_id: str) -> HttpResponse:
    """Re-snapshot all riders from the cache (no live ZR call).

    Our riders re-read from synced ``ZRRider`` data; opponents re-read from the
    shared cache, which is kept current by ``refresh_cached_clubs`` and on-demand
    fetches. Riders missing from the cache keep their existing snapshot.

    Returns:
        The refreshed matchup body partial (with a notice).

    """
    matchup = _get_editable(request, matchup_id)
    if matchup is None:
        return HttpResponse("Permission denied", status=403)

    now = timezone.now()
    updated = 0
    for rider in matchup.riders.all():
        data = roster.get_our_rider(rider.zwid) if rider.side == Side.OURS else cache.get_snapshot(rider.zwid)
        if data:
            rider.zr_data = data
            rider.name = data["name"] or rider.name
            rider.fetched_at = now
            rider.save(update_fields=["zr_data", "name", "fetched_at"])
            updated += 1

    logfire.info("Ladder matchup refreshed", matchup_id=str(matchup.pk), user_id=request.user.id, updated=updated)
    notice = f"Re-snapshotted {updated} rider(s) from cache."
    return HttpResponse(_render_body(request, matchup, can_edit=True, notice=notice))

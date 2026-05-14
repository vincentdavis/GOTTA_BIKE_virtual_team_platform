"""Views for zwiftpower app."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from urllib.parse import urlencode

import logfire
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.decorators import team_member_required
from apps.team.services import ZP_DIV_TO_CATEGORY
from apps.zwiftpower.models import ZPEvent, ZPRiderResults, ZPTeamRiders
from apps.zwiftracing.models import ZRRider

User = get_user_model()

# Whitelisted sort keys → ORM field expressions. Each tuple is (asc, desc).
TEAM_RESULTS_SORT: dict[str, tuple[str, str]] = {
    "date": ("event__event_date", "-event__event_date"),
    "event": ("event__title", "-event__title"),
    "name": ("name", "-name"),
    "category": ("category", "-category"),
    "pos": ("pos", "-pos"),
    "pos_cat": ("position_in_cat", "-position_in_cat"),
    "avg_power": ("avg_power", "-avg_power"),
    "avg_wkg": ("avg_wkg", "-avg_wkg"),
    "w1200": ("w1200", "-w1200"),
    "ftp": ("ftp", "-ftp"),
    "weight": ("weight", "-weight"),
}

EVENT_RESULTS_SORT: dict[str, tuple[str, str]] = {
    "name": ("name", "-name"),
    "category": ("category", "-category"),
    "pos": ("pos", "-pos"),
    "pos_cat": ("position_in_cat", "-position_in_cat"),
    "time": ("time_seconds", "-time_seconds"),
    "gap": ("gap", "-gap"),
    "avg_power": ("avg_power", "-avg_power"),
    "avg_wkg": ("avg_wkg", "-avg_wkg"),
    "np": ("np", "-np"),
    "w1200": ("w1200", "-w1200"),
    "ftp": ("ftp", "-ftp"),
    "weight": ("weight", "-weight"),
    "avg_hr": ("avg_hr", "-avg_hr"),
}


def _resolve_sort(
    sort_map: dict[str, tuple[str, str]],
    raw_sort: str,
    raw_dir: str,
    default_key: str,
    default_dir: str = "desc",
) -> tuple[str, str, str]:
    """Pick the sort key + direction from query parameters.

    Falls back to ``default_key``/``default_dir`` for unknown values.

    Args:
        sort_map: Whitelist of sort keys to (asc_expr, desc_expr) tuples.
        raw_sort: The raw ``sort`` querystring value.
        raw_dir: The raw ``dir`` querystring value.
        default_key: Fallback sort key when ``raw_sort`` is unknown.
        default_dir: Fallback direction when ``raw_dir`` is not ``asc`` or ``desc``.

    Returns:
        Tuple of (resolved key, resolved direction, ORM order_by expression).

    """
    key = raw_sort if raw_sort in sort_map else default_key
    direction = raw_dir if raw_dir in {"asc", "desc"} else default_dir
    expr = sort_map[key][0 if direction == "asc" else 1]
    return key, direction, expr


def _enrich_results_by_zwid(zwids: list[int]) -> dict[int, dict]:
    """Look up team-member context for a set of result zwids.

    Builds a ``zwid → tooltip-context`` dict for rendering the shared
    ``accounts/_user_tooltip.html`` partial. Three bulk queries (User,
    ZPTeamRiders, ZRRider) keep this cheap on large pages.

    Args:
        zwids: List of distinct ZP rider IDs from the current page of results.

    Returns:
        Dict keyed by zwid; only zwids that map to a User are included.

    """
    if not zwids:
        return {}

    users_by_zwid = {u.zwid: u for u in User.objects.filter(zwid__in=zwids)}
    if not users_by_zwid:
        return {}

    matched_zwids = list(users_by_zwid.keys())
    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=matched_zwids)}
    zr_by_zwid = {r.zwid: r for r in ZRRider.objects.filter(zwid__in=matched_zwids)}

    enriched: dict[int, dict] = {}
    for zwid, user in users_by_zwid.items():
        zp = zp_by_zwid.get(zwid)
        zr = zr_by_zwid.get(zwid)
        enriched[zwid] = {
            "user": user,
            "display_name": user.get_full_name() or user.discord_username or user.username,
            "user_id": user.pk,
            "discord_id": user.discord_id,
            "discord_avatar_url": user.discord_avatar_url,
            "zwid": zwid,
            "is_race_ready": user.is_race_ready,
            "is_extra_verified": user.is_extra_verified,
            "in_zwiftpower": zp is not None,
            "zp_category": ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else "",
            "zp_category_w": ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else "",
            "in_zwiftracing": zr is not None,
            "zr_category": getattr(zr, "race_current_category", "") or "" if zr else "",
            "zr_rating": getattr(zr, "race_current_rating", None) if zr else None,
            "zr_phenotype": getattr(zr, "phenotype_value", "") or "" if zr else "",
            "zr_age": getattr(zr, "age", "") or "" if zr else "",
        }
    return enriched


def _parse_date(raw: str) -> datetime | None:
    """Parse a YYYY-MM-DD string into an aware datetime.

    Args:
        raw: The raw query-string value.

    Returns:
        Aware datetime at local midnight, or None if the string is empty/invalid.

    """
    if not raw:
        return None
    try:
        parsed_date = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None
    naive = datetime.combine(parsed_date, time.min)
    return timezone.make_aware(naive, timezone.get_current_timezone())


@login_required
@team_member_required()
@require_GET
def team_results_view(request: HttpRequest) -> HttpResponse:
    """Team-wide ZwiftPower race results with filters and pagination.

    Filters: rider/event search, event type (race/ride), category, date range.

    Args:
        request: The HTTP request.

    Returns:
        Rendered team results page.

    """
    search_query = request.GET.get("q", "").strip()
    event_type_filter = request.GET.get("event_type", "").strip()
    category_filter = request.GET.get("category", "").strip().upper()
    date_from_raw = request.GET.get("date_from", "").strip()
    date_to_raw = request.GET.get("date_to", "").strip()

    results = ZPRiderResults.objects.select_related("event")

    if search_query:
        results = results.filter(name__icontains=search_query) | results.filter(
            event__title__icontains=search_query,
        )

    if event_type_filter:
        results = results.filter(f_t=event_type_filter)

    if category_filter:
        results = results.filter(category__iexact=category_filter)

    date_from = _parse_date(date_from_raw)
    if date_from:
        results = results.filter(event__event_date__gte=date_from)

    date_to = _parse_date(date_to_raw)
    if date_to:
        # Inclusive of the end date — bump to the next day's midnight
        results = results.filter(event__event_date__lt=date_to + timedelta(days=1))

    sort_key, sort_dir, sort_expr = _resolve_sort(
        TEAM_RESULTS_SORT,
        request.GET.get("sort", ""),
        request.GET.get("dir", ""),
        default_key="date",
    )
    # Use position as a stable tiebreaker
    results = results.order_by(sort_expr, "pos") if sort_key != "pos" else results.order_by(sort_expr)

    total_count = results.count()

    paginator = Paginator(results, 50)
    page_obj = paginator.get_page(request.GET.get("page", "1"))

    page_zwids = list({r.zwid for r in page_obj.object_list if r.zwid})
    enriched_by_zwid = _enrich_results_by_zwid(page_zwids)
    for r in page_obj.object_list:
        r.enriched = enriched_by_zwid.get(r.zwid)

    filter_params = {
        "q": search_query,
        "event_type": event_type_filter,
        "category": category_filter,
        "date_from": date_from_raw,
        "date_to": date_to_raw,
    }
    filter_qs = urlencode({k: v for k, v in filter_params.items() if v})
    base_params = {**filter_params, "sort": sort_key, "dir": sort_dir}
    base_qs = urlencode({k: v for k, v in base_params.items() if v})

    logfire.debug(
        "Team results viewed",
        user_id=request.user.id,
        total_count=total_count,
        page=page_obj.number,
        search_query=search_query,
        event_type=event_type_filter,
        category=category_filter,
    )

    return render(
        request,
        "zwiftpower/team_results.html",
        {
            "results": page_obj,
            "page_obj": page_obj,
            "total_count": total_count,
            "search_query": search_query,
            "event_type_filter": event_type_filter,
            "category_filter": category_filter,
            "date_from": date_from_raw,
            "date_to": date_to_raw,
            "event_type_choices": [("TYPE_RACE", "Race"), ("TYPE_RIDE", "Ride")],
            "category_choices": ["A", "B", "C", "D", "E"],
            "base_qs": base_qs,
            "filter_qs": filter_qs,
            "sort_key": sort_key,
            "sort_dir": sort_dir,
            "has_filters": any(filter_params.values()),
            "enriched_by_zwid": enriched_by_zwid,
        },
    )


@login_required
@team_member_required()
@require_GET
def event_results_view(request: HttpRequest, zid: int) -> HttpResponse:
    """Leaderboard for a single ZwiftPower event.

    Args:
        request: The HTTP request.
        zid: ZwiftPower event ID.

    Returns:
        Rendered event detail / leaderboard page.

    """
    event = get_object_or_404(ZPEvent, zid=zid)

    sort_key, sort_dir, sort_expr = _resolve_sort(
        EVENT_RESULTS_SORT,
        request.GET.get("sort", ""),
        request.GET.get("dir", ""),
        default_key="pos",
        default_dir="asc",
    )
    results = event.results.order_by(sort_expr)

    # Quick stats for the header strip
    finishers = results.exclude(pos__isnull=True).count()
    podium = list(results.filter(pos__lte=3).order_by("pos")[:3])

    page_zwids = list({r.zwid for r in results if r.zwid})
    enriched_by_zwid = _enrich_results_by_zwid(page_zwids)
    # Attach to each row so the template doesn't have to look up by zwid
    enriched_results = list(results)
    for r in enriched_results:
        r.enriched = enriched_by_zwid.get(r.zwid)

    logfire.debug(
        "Event results viewed",
        user_id=request.user.id,
        zid=zid,
        result_count=results.count(),
        sort_key=sort_key,
    )

    return render(
        request,
        "zwiftpower/event_results.html",
        {
            "event": event,
            "results": enriched_results,
            "finishers": finishers,
            "podium": podium,
            "sort_key": sort_key,
            "sort_dir": sort_dir,
            "enriched_by_zwid": enriched_by_zwid,
        },
    )

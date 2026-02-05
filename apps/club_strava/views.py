"""Views for club_strava app."""

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import team_member_required
from apps.club_strava.models import ClubActivity
from apps.club_strava.strava_client import sync_club_activities


@login_required
@team_member_required()
@require_GET
def activity_list_view(request: HttpRequest) -> HttpResponse:
    """Display list of recent Strava club activities.

    Args:
        request: The HTTP request.

    Returns:
        Rendered activity list page.

    """
    # Get filter parameters
    sport_type_filter = request.GET.get("sport_type", "")
    search_query = request.GET.get("q", "").strip()

    # Base queryset
    activities = ClubActivity.objects.all()

    # Apply filters
    if sport_type_filter:
        activities = activities.filter(sport_type=sport_type_filter)

    if search_query:
        activities = activities.filter(name__icontains=search_query) | activities.filter(
            athlete_first_name__icontains=search_query
        )

    # Get unique sport types for filter dropdown
    sport_types = ClubActivity.objects.values_list("sport_type", flat=True).distinct().order_by("sport_type")

    # Limit to recent activities
    activities = activities[:100]

    logfire.debug(
        "Strava activity list viewed",
        user_id=request.user.id,
        activity_count=len(activities),
        sport_type_filter=sport_type_filter,
        search_query=search_query,
    )

    return render(
        request,
        "club_strava/activity_list.html",
        {
            "activities": activities,
            "sport_types": sport_types,
            "sport_type_filter": sport_type_filter,
            "search_query": search_query,
        },
    )


@login_required
@team_member_required()
@require_POST
def sync_activities_view(request: HttpRequest) -> HttpResponse:
    """Trigger sync of Strava club activities.

    Args:
        request: The HTTP request.

    Returns:
        Redirect back to activity list.

    """
    logfire.info("Strava sync triggered", user_id=request.user.id, username=request.user.username)

    try:
        results = sync_club_activities(pages=2)
        messages.success(
            request,
            f"Synced Strava activities: {results['created']} new, {results['updated']} updated.",
        )
    except Exception as e:
        logfire.error("Strava sync failed", error=str(e))
        messages.error(request, f"Failed to sync Strava activities: {e}")

    return redirect("club_strava:activity_list")

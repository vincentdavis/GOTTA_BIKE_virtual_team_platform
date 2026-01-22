"""Views for team app."""

import uuid

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import discord_permission_required, team_member_required
from apps.accounts.discord_service import send_verification_notification
from apps.accounts.models import User
from apps.team.forms import (
    ApplicationZwiftVerificationForm,
    MembershipApplicationAdminForm,
    MembershipApplicationApplicantForm,
    TeamLinkEditForm,
    TeamLinkForm,
)
from apps.team.models import MembershipApplication, RaceReadyRecord, RosterFilter, TeamLink
from apps.team.services import (
    ZP_DIV_TO_CATEGORY,
    get_membership_review_data,
    get_performance_review_data,
    get_unified_team_roster,
)
from apps.team.tasks import notify_application_update
from apps.zwift.utils import fetch_zwift_id
from apps.zwiftpower.models import ZPTeamRiders


@login_required
@team_member_required()
@require_GET
def team_roster_view(request: HttpRequest) -> HttpResponse:
    """Display unified team roster.

    Args:
        request: The HTTP request.

    Returns:
        Rendered team roster page.

    """
    roster = get_unified_team_roster()

    # Exclude riders who have left (have zp_date_left set)
    roster = [r for r in roster if not r.zp_date_left]

    # Get filter parameters
    search_query = request.GET.get("q", "").strip()
    zp_category_filter = request.GET.get("zp_category", "")
    zr_category_filter = request.GET.get("zr_category", "")
    gender_filter = request.GET.get("gender", "")
    race_ready_filter = request.GET.get("race_ready", "")

    # Get sort parameters (default: result_count descending)
    sort_by = request.GET.get("sort", "results")
    sort_dir = request.GET.get("dir", "desc")

    # Collect unique values for filter dropdowns (before filtering)
    # For ZP categories, use the mapping to show letters
    zp_divs_present = sorted({r.zp_div for r in roster if r.in_zwiftpower and r.zp_div})
    zp_categories = [(div, ZP_DIV_TO_CATEGORY.get(div, str(div))) for div in zp_divs_present]
    zr_categories = sorted({r.zr_category for r in roster if r.in_zwiftracing and r.zr_category})

    # Apply search filter (by zwid or name)
    if search_query:
        search_lower = search_query.lower()
        roster = [
            r for r in roster
            if search_lower in r.display_name.lower() or search_query in str(r.zwid)
        ]

    # Apply ZwiftPower category filter (filter by div number)
    if zp_category_filter:
        try:
            div_value = int(zp_category_filter)
            roster = [r for r in roster if r.in_zwiftpower and r.zp_div == div_value]
        except ValueError:
            pass

    # Apply Zwift Racing category filter
    if zr_category_filter:
        roster = [r for r in roster if r.in_zwiftracing and r.zr_category == zr_category_filter]

    # Apply gender filter
    if gender_filter:
        roster = [r for r in roster if r.gender == gender_filter]

    # Apply race ready filter
    if race_ready_filter:
        if race_ready_filter == "yes":
            roster = [r for r in roster if r.is_race_ready]
        elif race_ready_filter == "no":
            roster = [r for r in roster if not r.is_race_ready]

    # Apply sorting
    reverse = sort_dir == "desc"
    sort_keys = {
        "name": lambda r: r.display_name.lower(),
        "zwid": lambda r: r.zwid,
        "gender": lambda r: r.gender or "",
        "account": lambda r: r.has_account,
        "verified": lambda r: r.zwid_verified,
        "race_ready": lambda r: r.is_race_ready,
        "category": lambda r: r.zp_div or 0,
        "catw": lambda r: r.zp_divw or 0,
        "rating": lambda r: r.zr_category or "",
        "results": lambda r: r.result_count,
        "rank": lambda r: r.zp_rank or 0,
        "ftp": lambda r: r.zp_ftp or 0,
        "wkg": lambda r: r.wkg or 0,
    }
    if sort_by in sort_keys:
        roster = sorted(roster, key=sort_keys[sort_by], reverse=reverse)

    return render(
        request,
        "team/roster.html",
        {
            "roster": roster,
            "search_query": search_query,
            "zp_category_filter": zp_category_filter,
            "zr_category_filter": zr_category_filter,
            "gender_filter": gender_filter,
            "race_ready_filter": race_ready_filter,
            "zp_categories": zp_categories,
            "zr_categories": zr_categories,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
    )


@require_GET
def filtered_roster_view(request: HttpRequest, filter_id: uuid.UUID) -> HttpResponse:
    """Display filtered team roster based on Discord channel members.

    This view does not require login - it's accessed via time-limited links
    generated by the Discord bot /in_channel command.

    Args:
        request: The HTTP request.
        filter_id: UUID of the RosterFilter record.

    Returns:
        Rendered filtered team roster page or 404 if filter not found/expired.

    """
    # Get the filter, return 404 if not found
    roster_filter = get_object_or_404(RosterFilter, id=filter_id)

    # Check if filter has expired
    if roster_filter.is_expired:
        logfire.debug(
            "Expired roster filter accessed",
            filter_id=str(filter_id),
            channel_name=roster_filter.channel_name,
        )
        return render(
            request,
            "team/roster_filter_expired.html",
            {"filter": roster_filter},
            status=410,  # Gone
        )

    # Get full roster
    roster = get_unified_team_roster()

    # Filter to only users whose discord_id is in the filter's discord_ids list
    # Convert discord_ids to strings for comparison
    discord_id_set = {str(did) for did in roster_filter.discord_ids}

    # Import User model to look up discord_ids
    from apps.accounts.models import User

    # Get user_ids that match the discord_ids
    matching_user_ids = set(
        User.objects.filter(discord_id__in=discord_id_set).values_list("id", flat=True)
    )

    # Filter roster to only include riders with matching user accounts
    roster = [r for r in roster if r.user_id and r.user_id in matching_user_ids]

    # Get sort parameters (default: name ascending for filtered view)
    sort_by = request.GET.get("sort", "name")
    sort_dir = request.GET.get("dir", "asc")

    # Apply sorting
    reverse = sort_dir == "desc"
    sort_keys = {
        "name": lambda r: r.display_name.lower(),
        "zwid": lambda r: r.zwid,
        "gender": lambda r: r.gender or "",
        "account": lambda r: r.has_account,
        "verified": lambda r: r.zwid_verified,
        "race_ready": lambda r: r.is_race_ready,
        "category": lambda r: r.zp_div or 0,
        "catw": lambda r: r.zp_divw or 0,
        "rating": lambda r: r.zr_category or "",
        "results": lambda r: r.result_count,
        "rank": lambda r: r.zp_rank or 0,
        "ftp": lambda r: r.zp_ftp or 0,
        "wkg": lambda r: r.wkg or 0,
    }
    if sort_by in sort_keys:
        roster = sorted(roster, key=sort_keys[sort_by], reverse=reverse)

    # Collect unique values for filter dropdowns
    zp_divs_present = sorted({r.zp_div for r in roster if r.in_zwiftpower and r.zp_div})
    zp_categories = [(div, ZP_DIV_TO_CATEGORY.get(div, str(div))) for div in zp_divs_present]
    zr_categories = sorted({r.zr_category for r in roster if r.in_zwiftracing and r.zr_category})

    return render(
        request,
        "team/roster.html",
        {
            "roster": roster,
            "roster_filter": roster_filter,
            "search_query": "",
            "zp_category_filter": "",
            "zr_category_filter": "",
            "status_filter": "",
            "gender_filter": "",
            "race_ready_filter": "",
            "zp_categories": zp_categories,
            "zr_categories": zr_categories,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
    )


@login_required
@team_member_required()
@require_GET
def team_links_view(request: HttpRequest) -> HttpResponse:
    """Display team links with filtering.

    Args:
        request: The HTTP request.

    Returns:
        Rendered team links page.

    """
    now = timezone.now()
    # Filter links that are currently visible:
    # - active=True
    # - date_open is null OR date_open <= now
    # - date_closed is null OR date_closed > now
    links = TeamLink.objects.filter(
        active=True,
    ).filter(
        Q(date_open__isnull=True) | Q(date_open__lte=now),
    ).filter(
        Q(date_closed__isnull=True) | Q(date_closed__gt=now),
    )

    # Get filter parameters
    search_query = request.GET.get("q", "").strip()
    type_filter = request.GET.get("type", "")

    # Get all available types for filter dropdown
    available_types = TeamLink.LinkType.choices

    # Apply search filter
    if search_query:
        search_lower = search_query.lower()
        links = links.filter(title__icontains=search_lower) | links.filter(description__icontains=search_lower)

    # Apply type filter
    if type_filter:
        links = links.filter(link_types__contains=type_filter)

    # Check if user can edit links
    can_edit_links = request.user.is_link_admin or request.user.is_superuser

    return render(
        request,
        "team/links.html",
        {
            "links": links,
            "search_query": search_query,
            "type_filter": type_filter,
            "available_types": available_types,
            "can_edit_links": can_edit_links,
        },
    )


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def submit_team_link_view(request: HttpRequest) -> HttpResponse:
    """Submit a new team link.

    Args:
        request: The HTTP request.

    Returns:
        Rendered form or redirect on success.

    """
    # Check if user has permission to create links
    if not request.user.is_link_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized team link creation attempt",
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to create team links.")
        return redirect("team:links")

    if request.method == "POST":
        form = TeamLinkForm(request.POST)
        if form.is_valid():
            link = form.save()
            logfire.info(
                "Team link created",
                link_id=link.pk,
                link_title=link.title,
                user_id=request.user.id,
                username=request.user.username,
            )
            messages.success(request, "Team link submitted successfully!")
            return redirect("team:links")
    else:
        form = TeamLinkForm()

    return render(
        request,
        "team/submit_link.html",
        {"form": form},
    )


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def edit_team_link_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit an existing team link.

    Args:
        request: The HTTP request.
        pk: The primary key of the TeamLink to edit.

    Returns:
        Rendered form or redirect on success.

    """
    link = get_object_or_404(TeamLink, pk=pk)

    # Check if user has permission to edit
    if not request.user.is_link_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized team link edit attempt",
            link_id=pk,
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to edit team links.")
        return redirect("team:links")

    if request.method == "POST":
        form = TeamLinkEditForm(request.POST, instance=link)
        if form.is_valid():
            form.save()
            logfire.info(
                "Team link updated",
                link_id=pk,
                link_title=link.title,
                user_id=request.user.id,
                username=request.user.username,
            )
            messages.success(request, "Team link updated successfully!")
            return redirect("team:links")
    else:
        form = TeamLinkEditForm(instance=link)

    return render(
        request,
        "team/edit_link.html",
        {"form": form, "link": link},
    )


@login_required
@team_member_required()
@require_POST
def delete_team_link_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a team link.

    Args:
        request: The HTTP request.
        pk: The primary key of the TeamLink to delete.

    Returns:
        Redirect to links list.

    """
    link = get_object_or_404(TeamLink, pk=pk)

    # Check if user has permission to delete
    if not request.user.is_link_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized team link delete attempt",
            link_id=pk,
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to delete team links.")
        return redirect("team:links")

    link_title = link.title
    link.delete()
    logfire.info(
        "Team link deleted",
        link_id=pk,
        link_title=link_title,
        user_id=request.user.id,
        username=request.user.username,
    )
    messages.success(request, "Team link deleted successfully!")
    return redirect("team:links")


@login_required
@team_member_required()
@require_GET
def verification_records_view(request: HttpRequest) -> HttpResponse:
    """Display verification records for team captains.

    Args:
        request: The HTTP request.

    Returns:
        Rendered verification records page.

    """
    # Check if user can view/approve verification records
    if not request.user.can_approve_verification and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized verification records access attempt",
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to view verification records.")
        return redirect("home")

    records = RaceReadyRecord.objects.select_related("user", "reviewed_by").order_by("-date_created")

    # Get filter parameters
    search_query = request.GET.get("q", "").strip()
    type_filter = request.GET.get("type", "")
    status_filter = request.GET.get("status", "")
    gender_filter = request.GET.get("gender", "")

    # Get choices for filter dropdowns
    verify_type_choices = RaceReadyRecord._meta.get_field("verify_type").choices
    status_choices = RaceReadyRecord.Status.choices
    gender_choices = User.Gender.choices

    # Apply search filter (by username or discord_username)
    if search_query:
        records = records.filter(
            Q(user__username__icontains=search_query) | Q(user__discord_username__icontains=search_query)
        )

    # Apply type filter
    if type_filter:
        records = records.filter(verify_type=type_filter)

    # Apply status filter
    if status_filter:
        records = records.filter(status=status_filter)

    # Apply gender filter
    if gender_filter:
        records = records.filter(user__gender=gender_filter)

    # Check if user can verify records (has permission)
    can_verify = request.user.can_approve_verification or request.user.is_superuser

    # Add can_review flag to each record based on same_gender preference
    def user_can_review_record(record: RaceReadyRecord) -> bool:
        """Check if the current user can review a specific record.

        Superusers can always review. If same_gender is False, anyone with
        permission can review. If same_gender is True, only same-gender
        reviewers can review.

        Returns:
            True if the user can review this record, False otherwise.

        """
        if request.user.is_superuser:
            return True
        if not record.same_gender:
            return True
        return record.user.gender == request.user.gender

    # Create list of (record, can_review) tuples for template
    records_with_review_status = [(record, user_can_review_record(record)) for record in records]

    return render(
        request,
        "team/verification_records.html",
        {
            "records_with_review_status": records_with_review_status,
            "search_query": search_query,
            "type_filter": type_filter,
            "status_filter": status_filter,
            "gender_filter": gender_filter,
            "verify_type_choices": verify_type_choices,
            "status_choices": status_choices,
            "gender_choices": gender_choices,
            "can_verify": can_verify,
        },
    )


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def verification_record_detail_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Display and verify or reject a verification record.

    Args:
        request: The HTTP request.
        pk: The primary key of the RaceReadyRecord.

    Returns:
        Rendered record detail page.

    """
    # Check if user can view/approve verification records
    if not request.user.can_approve_verification and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized verification record detail access attempt",
            record_id=pk,
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to view verification records.")
        return redirect("home")

    record = get_object_or_404(RaceReadyRecord.objects.select_related("user", "reviewed_by"), pk=pk)

    # Check if user can verify records (has permission)
    has_permission = request.user.can_approve_verification or request.user.is_superuser

    # Check same_gender restriction: if set, only same-gender reviewers can access (superusers bypass)
    if record.same_gender and not request.user.is_superuser and record.user.gender != request.user.gender:
        logfire.info(
            "Same-gender restriction blocked verification review",
            record_id=pk,
            user_id=request.user.id,
            user_gender=request.user.gender,
            record_user_gender=record.user.gender,
        )
        messages.warning(request, "This record requires a same-gender reviewer.")
        return redirect("team:verification_records")

    # User can review if they have permission and pass same_gender check (already checked above)
    can_review = has_permission

    if request.method == "POST" and can_review and record.is_pending:
        action = request.POST.get("action")
        if action == "verify":
            # Prevent self-approval
            if record.user == request.user:
                logfire.warning(
                    "Self-approval attempt blocked",
                    user_id=request.user.id,
                    record_id=pk,
                    verify_type=record.verify_type,
                )
                messages.error(request, "You cannot approve your own verification record.")
                return redirect("team:verification_record_detail", pk=pk)
            record.status = RaceReadyRecord.Status.VERIFIED
            record.reviewed_by = request.user
            record.reviewed_date = timezone.now()
            record.save()
            logfire.info(
                "Verification record approved",
                record_id=pk,
                verify_type=record.verify_type,
                target_user_id=record.user.id,
                target_username=record.user.username,
                reviewer_id=request.user.id,
                reviewer_username=request.user.username,
            )
            # Send Discord DM notification
            if record.user.discord_id:
                send_verification_notification(
                    discord_id=record.user.discord_id,
                    is_verified=True,
                    verify_type=record.verify_type,
                )
            messages.success(request, f"Record for {record.user.username} has been verified.")
        elif action == "reject":
            record.status = RaceReadyRecord.Status.REJECTED
            record.reviewed_by = request.user
            record.reviewed_date = timezone.now()
            rejection_reason = request.POST.get("rejection_reason", "").strip()
            record.rejection_reason = rejection_reason
            record.save()
            logfire.info(
                "Verification record rejected",
                record_id=pk,
                verify_type=record.verify_type,
                target_user_id=record.user.id,
                target_username=record.user.username,
                reviewer_id=request.user.id,
                reviewer_username=request.user.username,
                rejection_reason=rejection_reason or None,
            )
            # Send Discord DM notification
            if record.user.discord_id:
                send_verification_notification(
                    discord_id=record.user.discord_id,
                    is_verified=False,
                    verify_type=record.verify_type,
                    rejection_reason=rejection_reason or None,
                )
            messages.warning(request, f"Record for {record.user.username} has been rejected.")
        return redirect("team:verification_records")

    # Get ZwiftPower data for the user if they have a zwid
    zp_rider = None
    if record.user.zwid:
        zp_rider = ZPTeamRiders.objects.filter(zwid=record.user.zwid).first()

    return render(
        request,
        "team/verification_record_detail.html",
        {
            "record": record,
            "can_review": can_review,
            "zp_rider": zp_rider,
        },
    )


@login_required
@team_member_required()
@require_POST
def delete_expired_media_view(request: HttpRequest) -> HttpResponse:
    """Delete media files and URLs from expired verification records.

    Args:
        request: The HTTP request.

    Returns:
        Redirect to verification records list.

    """
    if not request.user.can_approve_verification and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized bulk delete expired media attempt",
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to perform this action.")
        return redirect("team:verification_records")

    # Get all verified records and filter to expired ones
    records = RaceReadyRecord.objects.filter(status=RaceReadyRecord.Status.VERIFIED)
    expired_records = [r for r in records if r.is_expired]

    deleted_count = 0
    for record in expired_records:
        has_media = record.media_file or record.url
        if has_media:
            record.delete_media_file()
            record.url = ""
            record.save(update_fields=["url"])
            deleted_count += 1

    logfire.info(
        "Bulk delete expired verification media",
        user_id=request.user.id,
        username=request.user.username,
        expired_records_found=len(expired_records),
        media_deleted_count=deleted_count,
    )

    if deleted_count:
        messages.success(request, f"Deleted media from {deleted_count} expired record(s).")
    else:
        messages.info(request, "No expired records with media found.")

    return redirect("team:verification_records")


@login_required
@team_member_required()
@require_POST
def delete_rejected_media_view(request: HttpRequest) -> HttpResponse:
    """Delete media files and URLs from rejected records older than 30 days.

    Args:
        request: The HTTP request.

    Returns:
        Redirect to verification records list.

    """
    from datetime import timedelta

    if not request.user.can_approve_verification and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized bulk delete rejected media attempt",
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to perform this action.")
        return redirect("team:verification_records")

    # Get rejected records older than 30 days
    cutoff_date = timezone.now() - timedelta(days=30)
    records = RaceReadyRecord.objects.filter(
        status=RaceReadyRecord.Status.REJECTED,
        reviewed_date__lt=cutoff_date,
    )

    deleted_count = 0
    total_records = records.count()
    for record in records:
        has_media = record.media_file or record.url
        if has_media:
            record.delete_media_file()
            record.url = ""
            record.save(update_fields=["url"])
            deleted_count += 1

    logfire.info(
        "Bulk delete rejected verification media",
        user_id=request.user.id,
        username=request.user.username,
        rejected_records_found=total_records,
        media_deleted_count=deleted_count,
        cutoff_days=30,
    )

    if deleted_count:
        messages.success(request, f"Deleted media from {deleted_count} rejected record(s).")
    else:
        messages.info(request, "No rejected records older than 30 days with media found.")

    return redirect("team:verification_records")


@login_required
@team_member_required()
@require_GET
def youtube_channels_view(request: HttpRequest) -> HttpResponse:
    """Display list of team members with YouTube channels.

    Args:
        request: The HTTP request.

    Returns:
        Rendered YouTube channels page.

    """
    from apps.accounts.models import User

    # Get users with YouTube channels, ordered by name
    users_with_channels = (
        User.objects.filter(youtube_channel__isnull=False)
        .exclude(youtube_channel="")
        .order_by("first_name", "last_name", "discord_nickname")
    )

    return render(
        request,
        "team/youtube_channels.html",
        {
            "users": users_with_channels,
        },
    )


@login_required
@team_member_required()
@require_GET
def performance_review_view(request: HttpRequest) -> HttpResponse:
    """Display performance review comparing verification records with ZwiftPower data.

    Args:
        request: The HTTP request.

    Returns:
        Rendered performance review page.

    """
    # Check permission - only verification reviewers can access
    if not request.user.can_approve_verification and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized performance review access attempt",
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to view performance review.")
        return redirect("home")

    # Get performance data
    riders = get_performance_review_data()

    # Get filter parameters
    search_query = request.GET.get("q", "").strip()
    zp_category_filter = request.GET.get("zp_category", "")
    gender_filter = request.GET.get("gender", "")

    # Get sort parameters (default: weight_diff descending - largest concerns first)
    sort_by = request.GET.get("sort", "weight_diff")
    sort_dir = request.GET.get("dir", "desc")

    # Collect unique values for filter dropdowns (before filtering)
    zp_divs_present = sorted({r.zp_div for r in riders if r.zp_div})
    zp_categories = [(div, ZP_DIV_TO_CATEGORY.get(div, str(div))) for div in zp_divs_present]

    # Apply search filter (by name or zwid)
    if search_query:
        search_lower = search_query.lower()
        riders = [
            r for r in riders
            if search_lower in r.display_name.lower() or search_query in str(r.zwid)
        ]

    # Apply ZwiftPower category filter
    if zp_category_filter:
        try:
            div_value = int(zp_category_filter)
            riders = [r for r in riders if r.zp_div == div_value]
        except ValueError:
            pass

    # Apply gender filter
    if gender_filter:
        riders = [r for r in riders if r.gender == gender_filter]

    # Apply sorting
    reverse = sort_dir == "desc"
    sort_keys = {
        "name": lambda r: r.display_name.lower(),
        "weight_diff": lambda r: r.weight_diff_abs if r.weight_diff_abs is not None else -1,
        "weight_light_date": lambda r: r.weight_light_date or timezone.datetime.min.replace(tzinfo=timezone.utc),
        "weight_full_date": lambda r: r.weight_full_date or timezone.datetime.min.replace(tzinfo=timezone.utc),
        "height_date": lambda r: r.height_date or timezone.datetime.min.replace(tzinfo=timezone.utc),
        "zp_result_date": lambda r: r.zp_result_date or timezone.datetime.min.replace(tzinfo=timezone.utc),
        "zp_height_date": lambda r: r.zp_height_date or timezone.datetime.min.replace(tzinfo=timezone.utc),
        "ftp": lambda r: r.ftp_current or 0,
        "wkg": lambda r: r.wkg or 0,
    }
    if sort_by in sort_keys:
        riders = sorted(riders, key=sort_keys[sort_by], reverse=reverse)

    return render(
        request,
        "team/performance_review.html",
        {
            "riders": riders,
            "search_query": search_query,
            "zp_category_filter": zp_category_filter,
            "gender_filter": gender_filter,
            "zp_categories": zp_categories,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
    )


# =============================================================================
# Membership Application Views
# =============================================================================


@login_required
@discord_permission_required("membership_admin", raise_exception=True)
@require_GET
def membership_review_view(request: HttpRequest) -> HttpResponse:
    """Display membership review showing all users with their ZP/ZR data.

    Args:
        request: The HTTP request.

    Returns:
        Rendered membership review page.

    """
    riders = get_membership_review_data()

    # Get view toggle parameter (race or member)
    current_view = request.GET.get("view", "race")
    if current_view not in ("race", "member"):
        current_view = "race"

    # Get filter parameters
    search_query = request.GET.get("q", "").strip()
    gender_filter = request.GET.get("gender", "")
    zp_category_filter = request.GET.get("zp_category", "")
    country_filter = request.GET.get("country", "")
    status_filter = request.GET.get("status", "active")  # Default to showing active members

    # Get sort parameters (default: name ascending)
    sort_by = request.GET.get("sort", "name")
    sort_dir = request.GET.get("dir", "asc")

    # Collect unique values for filter dropdowns (before filtering)
    zp_divs_present = sorted({r.zp_div for r in riders if r.zp_div})
    zp_categories = [(div, ZP_DIV_TO_CATEGORY.get(div, str(div))) for div in zp_divs_present]

    # Collect unique countries (code, name) tuples sorted by name
    countries_present = sorted(
        {(r.country, r.country_name) for r in riders if r.country and r.country_name},
        key=lambda x: x[1],  # Sort by country name
    )

    # Apply search filter (by name, discord nickname, or zwid)
    if search_query:
        search_lower = search_query.lower()
        riders = [
            r for r in riders
            if search_lower in r.full_name.lower()
            or search_lower in r.discord_nickname.lower()
            or search_lower in r.zp_name.lower()
            or search_lower in r.zr_name.lower()
            or search_query in str(r.zwid)
        ]

    # Apply gender filter
    if gender_filter:
        riders = [r for r in riders if r.gender == gender_filter]

    # Apply country filter
    if country_filter:
        riders = [r for r in riders if r.country == country_filter]

    # Apply ZwiftPower category filter
    if zp_category_filter:
        try:
            div_value = int(zp_category_filter)
            riders = [r for r in riders if r.zp_div == div_value]
        except ValueError:
            pass

    # Apply status filter
    if status_filter == "active":
        riders = [r for r in riders if r.is_active_member]
    elif status_filter in ("both", "zp_only", "zr_only", "left", "none"):
        riders = [r for r in riders if r.membership_status == status_filter]

    # Apply sorting
    reverse = sort_dir == "desc"
    sort_keys = {
        # Race profile sort keys
        "name": lambda r: (r.full_name or r.discord_nickname).lower(),
        "discord": lambda r: r.discord_nickname.lower(),
        "zp_name": lambda r: r.zp_name.lower(),
        "zr_name": lambda r: r.zr_name.lower(),
        "zwid": lambda r: r.zwid,
        "gender": lambda r: r.gender or "",
        "verified": lambda r: r.zwid_verified,
        "category": lambda r: r.zp_div or 0,
        "results": lambda r: r.result_count,
        "days": lambda r: r.days_since_result if r.days_since_result is not None else 9999,
        # Member profile sort keys
        "country": lambda r: r.country_name.lower(),
        "city": lambda r: r.city.lower(),
        "timezone": lambda r: r.timezone.lower(),
        "birth_year": lambda r: r.birth_year or 0,
        "trainer": lambda r: r.trainer.lower(),
    }
    if sort_by in sort_keys:
        riders = sorted(riders, key=sort_keys[sort_by], reverse=reverse)

    return render(
        request,
        "team/membership_review.html",
        {
            "riders": riders,
            "search_query": search_query,
            "gender_filter": gender_filter,
            "zp_category_filter": zp_category_filter,
            "country_filter": country_filter,
            "status_filter": status_filter,
            "zp_categories": zp_categories,
            "countries": countries_present,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "current_view": current_view,
        },
    )


@login_required
@discord_permission_required("membership_admin", raise_exception=True)
@require_GET
def membership_application_list_view(request: HttpRequest) -> HttpResponse:
    """Display list of membership applications for admin review.

    Args:
        request: The HTTP request.

    Returns:
        Rendered membership application list page.

    """
    applications = MembershipApplication.objects.select_related("modified_by").order_by("-date_created")

    # Get filter parameters
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")

    # Get sort parameters
    sort_by = request.GET.get("sort", "date_created")
    sort_dir = request.GET.get("dir", "desc")

    # Count applications by status (before filtering)
    status_counts = {}
    for status_choice in MembershipApplication.Status:
        status_counts[status_choice.value] = applications.filter(status=status_choice.value).count()

    # Apply search filter
    if search_query:
        applications = applications.filter(
            Q(discord_username__icontains=search_query)
            | Q(server_nickname__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(discord_id__icontains=search_query)
        )

    # Apply status filter
    if status_filter:
        applications = applications.filter(status=status_filter)

    # Apply sorting
    sort_mapping = {
        "date_created": "date_created",
        "date_modified": "date_modified",
        "status": "status",
        "discord_username": "discord_username",
    }
    if sort_by in sort_mapping:
        order_field = sort_mapping[sort_by]
        if sort_dir == "desc":
            order_field = f"-{order_field}"
        applications = applications.order_by(order_field)

    return render(
        request,
        "team/application_list.html",
        {
            "applications": applications,
            "search_query": search_query,
            "status_filter": status_filter,
            "status_choices": MembershipApplication.Status.choices,
            "status_counts": status_counts,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
    )


@login_required
@discord_permission_required("membership_admin", raise_exception=True)
@require_http_methods(["GET", "POST"])
def membership_application_admin_view(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Display and edit a membership application (admin view).

    Args:
        request: The HTTP request.
        pk: UUID of the MembershipApplication.

    Returns:
        Rendered membership application admin page.

    """
    from constance import config

    application = get_object_or_404(
        MembershipApplication.objects.select_related("modified_by"),
        pk=pk,
    )

    if request.method == "POST":
        old_status = application.status
        old_admin_notes = application.admin_notes
        form = MembershipApplicationAdminForm(request.POST, instance=application)
        if form.is_valid():
            app = form.save(commit=False)
            app.modified_by = request.user
            app.save()
            logfire.info(
                "Membership application updated by admin",
                application_id=str(pk),
                applicant_discord_id=application.discord_id,
                applicant_name=application.display_name,
                old_status=old_status,
                new_status=app.status,
                admin_id=request.user.id,
                admin_username=request.user.username,
            )

            # Send Discord notification based on what changed
            admin_display = request.user.discord_nickname or request.user.first_name or request.user.username
            status_changed = old_status != app.status
            notes_changed = old_admin_notes != app.admin_notes
            admin_url = request.build_absolute_uri(
                reverse("team:application_admin", kwargs={"pk": pk})
            )

            if status_changed:
                notify_application_update.enqueue(
                    application_id=str(pk),
                    update_type="status_changed",
                    admin_name=admin_display,
                    old_status=old_status,
                    new_status=app.status,
                    application_url=admin_url,
                )
            elif notes_changed:
                notify_application_update.enqueue(
                    application_id=str(pk),
                    update_type="admin_notes",
                    admin_name=admin_display,
                    application_url=admin_url,
                )

            messages.success(request, f"Application for {application.display_name} updated.")
            return redirect("team:application_list")
    else:
        form = MembershipApplicationAdminForm(instance=application)

    return render(
        request,
        "team/application_admin.html",
        {
            "application": application,
            "form": form,
            "application_form_instructions": config.REGISTRATION_FORM_INSTRUCTIONS,
        },
    )


@require_http_methods(["GET", "POST"])
def membership_application_public_view(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Display and edit a membership application (public applicant view).

    This view does not require login - it's accessed via UUID link sent to
    the applicant by the Discord bot.

    Args:
        request: The HTTP request.
        pk: UUID of the MembershipApplication.

    Returns:
        Rendered membership application public form page.

    """
    from constance import config

    application = get_object_or_404(MembershipApplication, pk=pk)

    # Check if application is still editable
    if not application.is_editable:
        # Show read-only view for approved/rejected applications
        return render(
            request,
            "team/application_public.html",
            {
                "application": application,
                "form": None,
                "privacy_policy_url": config.PRIVACY_POLICY_URL,
                "terms_of_service_url": config.TERMS_OF_SERVICE_URL,
                "application_form_instructions": config.REGISTRATION_FORM_INSTRUCTIONS,
            },
        )

    if request.method == "POST":
        form = MembershipApplicationApplicantForm(request.POST, instance=application)
        if form.is_valid():
            form.save()
            logfire.info(
                "Membership application updated by applicant",
                application_id=str(pk),
                applicant_discord_id=application.discord_id,
                applicant_name=application.display_name,
                is_complete=application.is_complete,
            )

            # Send Discord notification for applicant update
            admin_url = request.build_absolute_uri(
                reverse("team:application_admin", kwargs={"pk": pk})
            )
            notify_application_update.enqueue(
                application_id=str(pk),
                update_type="applicant_updated",
                application_url=admin_url,
            )

            messages.success(request, "Your application has been updated. Thank you!")
            return redirect("team:application_public", pk=pk)
    else:
        form = MembershipApplicationApplicantForm(instance=application)

    return render(
        request,
        "team/application_public.html",
        {
            "application": application,
            "form": form,
            "privacy_policy_url": config.PRIVACY_POLICY_URL,
            "terms_of_service_url": config.TERMS_OF_SERVICE_URL,
            "application_form_instructions": config.REGISTRATION_FORM_INSTRUCTIONS,
        },
    )


@require_http_methods(["GET", "POST"])
def application_verify_zwift(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Verify Zwift account for a membership application.

    This view handles Zwift account verification without requiring login.
    Auth is based on knowing the application UUID.

    Args:
        request: The HTTP request.
        pk: UUID of the MembershipApplication.

    Returns:
        Rendered verification modal partial for HTMX requests.

    """
    application = get_object_or_404(MembershipApplication, pk=pk)

    # Only allow verification if application is editable
    if not application.is_editable:
        return render(
            request,
            "team/partials/application_zwift_verify_modal.html",
            {"application": application, "error": "Application is no longer editable."},
        )

    if request.method == "POST":
        form = ApplicationZwiftVerificationForm(request.POST)
        if form.is_valid():
            zwift_username = form.cleaned_data["zwift_username"]
            zwift_password = form.cleaned_data["zwift_password"]

            # Fetch Zwift ID using the credentials
            zwift_id = fetch_zwift_id(zwift_username, zwift_password)

            if zwift_id:
                # Update application's Zwift ID and mark as verified
                application.zwift_id = zwift_id
                application.zwift_verified = True
                application.save(update_fields=["zwift_id", "zwift_verified"])
                logfire.info(
                    "Zwift account verified for application",
                    application_id=str(pk),
                    applicant_discord_id=application.discord_id,
                    zwift_id=zwift_id,
                )

                return render(
                    request,
                    "team/partials/application_zwift_verify_modal.html",
                    {"application": application, "success": True, "zwift_id": zwift_id},
                )
            else:
                logfire.warning(
                    "Zwift verification failed for application",
                    application_id=str(pk),
                    applicant_discord_id=application.discord_id,
                )
                form.add_error(None, "Could not verify Zwift account. Please check your credentials.")
    else:
        form = ApplicationZwiftVerificationForm()

    return render(
        request,
        "team/partials/application_zwift_verify_modal.html",
        {"application": application, "form": form},
    )


@login_required
@discord_permission_required("membership_admin", raise_exception=True)
@require_GET
def discord_review_view(request: HttpRequest) -> HttpResponse:
    """Review and audit Discord guild members.

    Args:
        request: The HTTP request.

    Returns:
        Rendered Discord review page.

    """
    from datetime import datetime

    from apps.accounts.models import GuildMember
    from apps.team.models import DiscordRole

    # Get all guild members
    members = GuildMember.objects.select_related("user").all()

    # Build role lookup dict: {role_id: role}
    all_roles = DiscordRole.objects.all()
    role_lookup = {role.role_id: role for role in all_roles}

    # Get filter parameters
    search_query = request.GET.get("q", "").strip()
    join_from = request.GET.get("join_from", "").strip()
    join_to = request.GET.get("join_to", "").strip()
    left_status = request.GET.get("left_status", "")
    is_bot_filter = request.GET.get("is_bot", "")
    role_filters = request.GET.getlist("role")  # Multiple values for Roles filter (OR logic)
    exclude_roles = request.GET.getlist("exclude_roles")  # Multiple values for ~Roles filter
    account_status = request.GET.get("account_status", "")

    # Get sort parameters
    sort_by = request.GET.get("sort", "joined_at")
    sort_dir = request.GET.get("dir", "desc")

    # Apply search filter (by username, display_name, or nickname)
    if search_query:
        members = members.filter(
            Q(username__icontains=search_query)
            | Q(display_name__icontains=search_query)
            | Q(nickname__icontains=search_query)
        )

    # Apply join date range filters
    if join_from:
        try:
            from_date = datetime.strptime(join_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            members = members.filter(joined_at__gte=from_date)
        except ValueError:
            pass

    if join_to:
        try:
            to_date = datetime.strptime(join_to, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            # Include the entire day by adding 1 day
            to_date = to_date.replace(hour=23, minute=59, second=59)
            members = members.filter(joined_at__lte=to_date)
        except ValueError:
            pass

    # Apply left status filter
    if left_status == "active":
        members = members.filter(date_left__isnull=True)
    elif left_status == "left":
        members = members.filter(date_left__isnull=False)

    # Apply is_bot filter
    if is_bot_filter == "yes":
        members = members.filter(is_bot=True)
    elif is_bot_filter == "no":
        members = members.filter(is_bot=False)

    # Apply account status filter (linked/unlinked)
    if account_status == "linked":
        members = members.filter(user__isnull=False)
    elif account_status == "unlinked":
        members = members.filter(user__isnull=True)

    # Apply role filter (members must have ANY of the selected roles - OR logic)
    if role_filters:
        role_q = Q()
        for role_id in role_filters:
            role_q |= Q(roles__contains=role_id)
        members = members.filter(role_q)

    # Apply exclude roles filter (~Roles) - exclude members who have ANY of the selected roles
    # This is NOT(role1 OR role2 OR role3) logic
    if exclude_roles:
        for excluded_role_id in exclude_roles:
            members = members.exclude(roles__contains=excluded_role_id)

    # Apply sorting
    sort_mapping = {
        "username": "username",
        "nickname": "nickname",
        "joined_at": "joined_at",
        "date_left": "date_left",
        "is_bot": "is_bot",
    }
    if sort_by in sort_mapping:
        order_field = sort_mapping[sort_by]
        if sort_dir == "desc":
            order_field = f"-{order_field}"
        members = members.order_by(order_field)

    # Convert to list and add role_names_display for each member
    members_list = list(members)
    for member in members_list:
        role_names = []
        for role_id in member.roles or []:
            role = role_lookup.get(str(role_id))
            if role:
                role_names.append(role.name)
            else:
                role_names.append(f"Unknown ({role_id})")
        member.role_names_display = ", ".join(role_names) if role_names else "No roles"
        member.role_count = len(member.roles or [])

    # Handle role_count sorting in Python (since it's computed)
    if sort_by == "role_count":
        reverse = sort_dir == "desc"
        members_list = sorted(members_list, key=lambda m: m.role_count, reverse=reverse)

    logfire.debug(
        "Discord review page loaded",
        user_id=request.user.id,
        total_members=len(members_list),
        filters={
            "search": search_query,
            "join_from": join_from,
            "join_to": join_to,
            "left_status": left_status,
            "is_bot": is_bot_filter,
            "account_status": account_status,
            "roles": role_filters,
            "exclude_roles": exclude_roles,
        },
    )

    return render(
        request,
        "team/discord_review.html",
        {
            "members": members_list,
            "search_query": search_query,
            "join_from": join_from,
            "join_to": join_to,
            "left_status": left_status,
            "is_bot_filter": is_bot_filter,
            "account_status": account_status,
            "role_filters": role_filters,
            "exclude_roles": exclude_roles,
            "all_roles": all_roles.order_by("position"),
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
    )


@require_POST
def application_unverify_zwift(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Remove Zwift verification from a membership application.

    Args:
        request: The HTTP request.
        pk: UUID of the MembershipApplication.

    Returns:
        Rendered Zwift status partial for HTMX requests.

    """
    application = get_object_or_404(MembershipApplication, pk=pk)

    # Only allow modification if application is editable
    if not application.is_editable:
        return render(
            request,
            "team/partials/application_zwift_status.html",
            {"application": application},
        )

    old_zwift_id = application.zwift_id
    application.zwift_id = ""
    application.zwift_verified = False
    application.save(update_fields=["zwift_id", "zwift_verified"])
    logfire.info(
        "Zwift verification removed from application",
        application_id=str(pk),
        applicant_discord_id=application.discord_id,
        old_zwift_id=old_zwift_id,
    )

    return render(
        request,
        "team/partials/application_zwift_status.html",
        {"application": application},
    )

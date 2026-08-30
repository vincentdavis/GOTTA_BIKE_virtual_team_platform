"""Views for accounts app."""

import json
from collections import Counter
from datetime import timedelta

import logfire
from constance import config
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_countries.fields import Country

from apps.accounts.decorators import team_member_required
from apps.accounts.forms import ProfileForm
from apps.accounts.models import BlockedDiscordId, User
from apps.accounts.services import delete_user_account
from apps.team.forms import RaceReadyRecordForm
from apps.team.services import (
    build_verify_type_options,
    delete_verification_records,
    get_user_required_verification_types,
    get_user_verification_types,
)
from apps.zwift import profile_fields

# How recently the Zwift Racing data must have been fetched before the profile
# refresh button is offered / honored. Mirrors the once-per-hour client guard.
ZR_REFRESH_MIN_AGE = timedelta(hours=1)


def zr_rating_tiers(zr_rider) -> list[dict]:
    """Build the current / 30-day / 90-day rating rows for a profile.

    The API reports all three, and they answer different questions: current is form today,
    the two maxima are what a rider is seeded against. Showing only "current" made a rider
    who has not raced recently look unrated.

    Args:
        zr_rider: The ZRRider row, or None.

    Returns:
        One row per tier, each with ``label``, ``rating``, ``category`` and ``date``.
        Rows whose rating is null are kept, so the gap is visible rather than silent.

    """
    if zr_rider is None:
        return []
    return [
        {
            "label": label,
            "rating": getattr(zr_rider, f"race_{key}_rating"),
            "category": getattr(zr_rider, f"race_{key}_category"),
            "date": getattr(zr_rider, f"race_{key}_date"),
        }
        for label, key in (("Current", "current"), ("30-day max", "max30"), ("90-day max", "max90"))
    ]


def _build_zwift_status_context(user: User, *, zr_refresh_error: bool = False) -> dict:
    """Build the context for the ``accounts/partials/zwift_status.html`` partial.

    Shared by ``profile_view`` (full page) and ``refresh_zr`` (HTMX swap) so the
    ZwiftPower / Zwift Racing cards render identically. ``zr_last_updated`` and
    ``zr_can_refresh`` drive the Zwift Racing refresh control on the own-profile
    page (refresh is offered when the data is missing or at least
    ``ZR_REFRESH_MIN_AGE`` old).

    Args:
        user: The profile owner (the requesting user).
        zr_refresh_error: True to surface a "refresh failed" hint in the partial.

    Returns:
        Template context dict for the Zwift status partial.

    """
    from apps.team.services import ZP_DIV_TO_CATEGORY
    from apps.zwiftpower.models import ZPTeamRiders
    from apps.zwiftracing.models import ZRRider

    zp_data = None
    zr_data = None
    zr_last_updated = None
    zr_can_refresh = False
    if user.zwid_verified and user.zwid:
        zp_rider = ZPTeamRiders.objects.filter(zwid=user.zwid).first()
        if zp_rider:
            # Use divw for females, div for everyone else
            div = zp_rider.divw if user.gender == "female" else zp_rider.div
            zp_data = {
                "category": ZP_DIV_TO_CATEGORY.get(div, ""),
                "rank": zp_rider.rank,
                "ftp": zp_rider.ftp,
                "updated": zp_rider.date_modified,
            }
            # Include women's category for female riders
            if user.gender == "female" and zp_rider.div:
                zp_data["category_mixed"] = ZP_DIV_TO_CATEGORY.get(zp_rider.div, "")

        zr_rider = ZRRider.objects.filter(zwid=user.zwid).first()
        if zr_rider:
            zr_data = {
                "category": zr_rider.race_current_category,
                "rating": zr_rider.race_current_rating,
                "updated": zr_rider.date_modified,
                "tiers": zr_rating_tiers(zr_rider),
                # All three tiers go null once someone stops racing, so history is the
                # only record that they ever had a rating.
                "best_seen": zr_rider.best_rating_seen(),
            }
            zr_last_updated = zr_rider.date_modified
            zr_can_refresh = (timezone.now() - zr_rider.date_modified) >= ZR_REFRESH_MIN_AGE
        else:
            # No record yet — allow an initial fetch.
            zr_can_refresh = True

    # Official Zwift OAuth (zauth) connection status — independent of zwid_verified.
    from apps.zwift import client as zwift_client

    zauth_configured = zwift_client.is_configured()
    zauth = zwift_client.get_connection_status(str(user.pk)) if zauth_configured else None
    zauth_uuid = (zauth or {}).get("zwift_user_id") or ""

    return {
        "user": user,
        "zp_data": zp_data,
        "zr_data": zr_data,
        "zr_last_updated": zr_last_updated,
        "zr_can_refresh": zr_can_refresh,
        "zr_refresh_error": zr_refresh_error,
        "zauth_configured": zauth_configured,
        "zauth_connected": bool(zauth and zauth.get("connected")),
        "zauth_zwid": (zauth or {}).get("zwid"),
        "zauth_connected_at": (zauth or {}).get("connected_at"),
        "zauth_uuid_tail": zauth_uuid.split("-")[-1] if zauth_uuid else None,
    }


def _fetch_racing_profile(user: User) -> dict | None:
    """Fetch a user's official Zwift racing profile from the zauth service.

    Returns the service's racing-profile dict enriched with a ``weight_kg``
    convenience field, or None when unconfigured / not connected / on error. Kept
    resilient so a slow or down service never breaks a profile page render.

    Args:
        user: The profile owner.

    Returns:
        The racing-profile context dict, or None.

    """
    from apps.zwift import client as zwift_client

    profile = zwift_client.get_racing_profile(str(user.pk))
    if not profile:
        return None
    grams = profile.get("weight_in_grams")
    profile["weight_kg"] = round(grams / 1000, 1) if grams else None

    # Zwift reports gender as a boolean `male` on the raw DTO, so it is resolved to a
    # display string here rather than in the template: `{% if %}` on the bool itself
    # cannot tell False (female) from absent, and would silently hide every woman.
    zwift_gender = profile_fields.zwift_gender(profile)
    profile["gender"] = dict(User.Gender.choices).get(zwift_gender) if zwift_gender else None

    zwift_country = profile_fields.zwift_country(profile)
    profile["country"] = Country(zwift_country).name if zwift_country else None

    # A mismatch is only meaningful when the rider has answered for themselves; a blank
    # profile field is not a disagreement, and would have been filled on connect anyway.
    # "Other" gender does count as a mismatch -- Zwift cannot express it, but the team
    # wants to see every divergence from what the rider races under.
    profile["country_mismatch"] = bool(
        zwift_country and user.country and user.country.code != zwift_country
    )
    profile["gender_mismatch"] = bool(zwift_gender and user.gender and user.gender != zwift_gender)
    return profile


def _fmt_hm(seconds: float | None) -> str | None:
    """Format a duration in seconds as ``Hh MMm`` (or ``Mm`` under an hour).

    Args:
        seconds: Duration in seconds, or None.

    Returns:
        A short human string, or None when there is no duration.

    """
    if not seconds:
        return None
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _fetch_activity_window(user: User) -> dict | None:
    """Fetch a user's recent Zwift activities + 30-day stats from the zauth service.

    Returns a template-friendly dict (distances in km, durations pre-formatted),
    or None when unconfigured / not connected / on error. Kept resilient so a
    slow or down service never breaks a profile page render.

    Args:
        user: The profile owner.

    Returns:
        The processed activity-window context, or None.

    """
    from apps.zwift import client as zwift_client

    data = zwift_client.get_activity_stats(str(user.pk))
    if not data:
        return None

    activities = []
    for row in data.get("activities", []):
        distance_m = row.get("distance_m")
        activities.append(
            {
                "date": row.get("start_date_time"),
                "name": row.get("name"),
                "sport": row.get("sport"),
                "distance_km": round(distance_m / 1000, 1) if distance_m else None,
                "duration": _fmt_hm(row.get("duration_s")),
            }
        )

    stats = data.get("stats") or {}
    total_distance_m = stats.get("total_distance_m") or 0
    return {
        "days": stats.get("days", 30),
        "count": stats.get("count", 0),
        "sports": stats.get("sports") or {},
        "total_distance_km": round(total_distance_m / 1000, 1),
        "total_duration": _fmt_hm(stats.get("total_duration_s")),
        "activities": activities,
    }


@login_required
@require_GET
def profile_view(request: HttpRequest) -> HttpResponse:
    """Display user profile page.

    Args:
        request: The HTTP request.

    Returns:
        Rendered profile page.

    """
    from apps.team.models import RaceReadyRecord
    from apps.team.services import get_user_required_verification_types

    form = ProfileForm(instance=request.user)

    # Build required verification summary with status
    required_types = get_user_required_verification_types(request.user)
    type_labels = {
        "weight_full": "Weight (Full)",
        "weight_light": "Weight (Light)",
        "height": "Height",
        "power": "Power",
    }
    verified_records = request.user.race_ready_records.filter(status=RaceReadyRecord.Status.VERIFIED)
    pending_records = request.user.race_ready_records.filter(status=RaceReadyRecord.Status.PENDING)
    latest_verified = {}
    for record in verified_records:
        if record.verify_type not in latest_verified:
            latest_verified[record.verify_type] = record
    required_summary = []
    for vtype in required_types:
        record = latest_verified.get(vtype)
        if record and not record.is_expired:
            status = "valid"
        elif record and record.is_expired:
            status = "expired"
        elif pending_records.filter(verify_type=vtype).exists():
            status = "pending"
        else:
            status = "missing"
        required_summary.append({"type": vtype, "label": type_labels.get(vtype, vtype), "status": status})

    context = _build_zwift_status_context(request.user)
    context["form"] = form
    context["required_summary"] = required_summary
    context["racing_profile"] = _fetch_racing_profile(request.user)
    return render(request, "accounts/profile.html", context)


@login_required
@require_POST
def refresh_zr(request: HttpRequest) -> HttpResponse:
    """Refresh the requesting user's Zwift Racing data (own profile only).

    Rate-limited to once per ``ZR_REFRESH_MIN_AGE``: if the ``ZRRider`` record
    was updated more recently the fetch is skipped (defends the upstream API
    even if the client button is tampered with). Re-renders the Zwift status
    partial for an HTMX swap.

    Args:
        request: The HTTP request.

    Returns:
        Rendered Zwift status partial.

    """
    from apps.zwiftracing.models import ZRRider
    from apps.zwiftracing.tasks import refresh_rider_sync

    user = request.user
    refresh_error = False
    if user.zwid_verified and user.zwid:
        zr_rider = ZRRider.objects.filter(zwid=user.zwid).first()
        recently_updated = zr_rider is not None and (timezone.now() - zr_rider.date_modified) < ZR_REFRESH_MIN_AGE
        if not recently_updated:
            status_code, rider = refresh_rider_sync(user.zwid)
            refresh_error = rider is None
            logfire.info(
                "Profile ZR refresh requested",
                user_id=user.id,
                zwid=user.zwid,
                status_code=status_code,
                success=not refresh_error,
            )

    return render(
        request,
        "accounts/partials/zwift_status.html",
        _build_zwift_status_context(user, zr_refresh_error=refresh_error),
    )


@login_required
@require_GET
def verification_view(request: HttpRequest) -> HttpResponse:
    """Display user's verification records and submission form.

    Args:
        request: The HTTP request.

    Returns:
        Rendered verification page.

    """
    # Get allowed verification types based on user's ZwiftPower category
    allowed_types = get_user_verification_types(request.user)
    race_ready_form = RaceReadyRecordForm(
        allowed_types=allowed_types,
        unit_preference=request.user.unit_preference,
    )

    # Get all race ready records for the user
    race_ready_records = request.user.race_ready_records.all()

    # Get the most recent record for each verify_type
    latest_by_type = {}
    for verify_type in ["weight_full", "weight_light", "height", "power"]:
        record = race_ready_records.filter(verify_type=verify_type).first()
        if record:
            latest_by_type[verify_type] = record

    # Build required verification summary with status for each type
    # Use latest *verified* record per type (not latest overall, which could be rejected)
    from apps.team.models import RaceReadyRecord

    verified_records = race_ready_records.filter(status=RaceReadyRecord.Status.VERIFIED)
    latest_verified = {}
    for record in verified_records:
        if record.verify_type not in latest_verified:
            latest_verified[record.verify_type] = record

    required_types = get_user_required_verification_types(request.user)

    # Which records are genuinely holding up a status right now, so the page only warns
    # about deletions that would actually cost the rider something. A record is
    # load-bearing when it is the *last* non-expired verified record of its type and that
    # type is one the rider's status depends on -- a duplicate of the same type, or a type
    # nothing needs, costs nothing to delete.
    live_types = Counter(r.verify_type for r in verified_records if not r.is_expired)
    weight_types = {"weight_light", "weight_full"}
    weight_required = weight_types & set(required_types)
    extra_types = {"weight_full", "height", "power"}

    def _load_bearing(verify_type: str) -> bool:
        if live_types[verify_type] > 1:
            return False
        # Categories 40/50 list both weight types and either one satisfies the
        # requirement, so a weight record only matters when it is the last of *any* kind.
        if len(weight_required) > 1 and verify_type in weight_required:
            races = sum(live_types[t] for t in weight_required) == 1
        else:
            races = verify_type in required_types
        # Extra Verified is a separate tier that always wants weight_full + height + power.
        extra = request.user.is_extra_verified and verify_type in extra_types
        return (races and request.user.is_race_ready) or extra

    supporting_ids = {
        record.pk for record in verified_records if not record.is_expired and _load_bearing(record.verify_type)
    }

    type_labels = {
        "weight_full": "Weight (Full)",
        "weight_light": "Weight (Light)",
        "height": "Height",
        "power": "Power",
    }
    required_summary = []
    for vtype in required_types:
        record = latest_verified.get(vtype)
        if record and not record.is_expired:
            status = "valid"
        elif record and record.is_expired:
            status = "expired"
        elif race_ready_records.filter(verify_type=vtype, status=RaceReadyRecord.Status.PENDING).exists():
            status = "pending"
        else:
            status = "missing"
        required_summary.append({"type": vtype, "label": type_labels.get(vtype, vtype), "status": status})

    return render(
        request,
        "accounts/verification.html",
        {
            "race_ready_form": race_ready_form,
            "race_ready_records": race_ready_records,
            "latest_by_type": latest_by_type,
            "verify_type_options": build_verify_type_options(request.user),
            "required_summary": required_summary,
            "supporting_record_ids": supporting_ids,
            "verification_form_message": config.VERIFICATION_FORM_MESSAGE,
            "weight_instructions_url": config.WEIGHT_INSTRUCTIONS_URL,
            "height_instructions_url": config.HEIGHT_INSTRUCTIONS_URL,
            "unit_preference": request.user.unit_preference,
        },
    )


@login_required
@require_POST
def verification_delete(request: HttpRequest) -> HttpResponse:
    """Delete verification records the rider selected on their own verification page.

    Only the requesting user's records can be reached: the ids are filtered through their
    own related manager, so an id belonging to someone else matches nothing rather than
    raising -- there is no probing signal either way.

    Args:
        request: The HTTP request.

    Returns:
        Redirect back to the verification page with a result message.

    """
    # isdigit() is not a safe guard here: "\u00b2" passes it but int() raises, and "\u0663"
    # (Arabic-Indic three) passes it and int() silently yields 3 -- which would delete a
    # record the rider never selected. Require plain ASCII decimals, and bound the value so
    # an oversized id cannot overflow the primary key column either.
    record_ids = []
    for raw in request.POST.getlist("record_ids"):
        if not (raw.isascii() and raw.isdecimal()):
            continue
        value = int(raw)
        if 0 < value <= 9223372036854775807:
            record_ids.append(value)

    if not record_ids:
        messages.info(request, "No records were selected.")
        return redirect("accounts:verification")

    result = delete_verification_records(request.user, record_ids)
    if not result["deleted"]:
        messages.info(request, "No records were selected.")
        return redirect("accounts:verification")

    deleted = result["deleted"]
    noun = "record" if deleted == 1 else "records"
    if result["was_race_ready"] and not result["is_race_ready"]:
        messages.warning(
            request,
            f"Deleted {deleted} verification {noun}. You are no longer Race Verified — "
            "submit a new verification to regain it.",
        )
    else:
        messages.success(request, f"Deleted {deleted} verification {noun}.")

    # Extra Verified is a separate tier, and losing it used to happen silently.
    if result["was_extra_verified"] and not result["is_extra_verified"]:
        messages.warning(
            request,
            "You are no longer Extra Verified — that needs valid weight, height and power records.",
        )

    if result["failed_media"]:
        messages.warning(
            request,
            "Some evidence files could not be removed from storage. They have been logged for cleanup.",
        )

    return redirect("accounts:verification")


@login_required
@team_member_required()
@require_GET
def public_profile_view(request: HttpRequest, user_id: int) -> HttpResponse:
    """Display a user's public profile for team members.

    Shows public information only (no birth_year, email, or emergency contact).
    Requires team_member permission to view.

    Args:
        request: The HTTP request.
        user_id: The ID of the user whose profile to display.

    Returns:
        Rendered public profile page.

    """
    from django.shortcuts import get_object_or_404

    from apps.team.services import ZP_DIV_TO_CATEGORY
    from apps.zwiftpower.models import ZPRiderResults, ZPTeamRiders
    from apps.zwiftracing.models import ZRRider

    profile_user = get_object_or_404(User, id=user_id)

    # Check if viewing own profile
    is_own_profile = profile_user == request.user

    # Fetch ZwiftPower and ZwiftRacing data if user is verified
    zp_data = None
    zr_data = None
    recent_results = []
    if profile_user.zwid_verified and profile_user.zwid:
        # Get ZwiftPower data
        zp_rider = ZPTeamRiders.objects.filter(zwid=profile_user.zwid).first()
        if zp_rider:
            # Use divw for females, div for everyone else
            div = zp_rider.divw if profile_user.gender == "female" else zp_rider.div
            wkg = round(float(zp_rider.ftp) / float(zp_rider.weight), 2) if zp_rider.ftp and zp_rider.weight else None
            zp_data = {
                "category": ZP_DIV_TO_CATEGORY.get(div, ""),
                "rank": zp_rider.rank,
                "ftp": zp_rider.ftp,
                "wkg": wkg,
                "weight": zp_rider.weight,
                "h_1200_watts": zp_rider.h_1200_watts,
                "h_1200_wkg": zp_rider.h_1200_wkg,
                "h_15_watts": zp_rider.h_15_watts,
                "h_15_wkg": zp_rider.h_15_wkg,
                "updated": zp_rider.date_modified,
            }
            # Include women's category for female riders
            if profile_user.gender == "female" and zp_rider.div:
                zp_data["category_mixed"] = ZP_DIV_TO_CATEGORY.get(zp_rider.div, "")

        # Get ZwiftRacing data
        zr_rider = ZRRider.objects.filter(zwid=profile_user.zwid).first()
        if zr_rider:
            zr_data = {
                "category": zr_rider.race_current_category,
                "rating": zr_rider.race_current_rating,
                "tiers": zr_rating_tiers(zr_rider),
                "best_seen": zr_rider.best_rating_seen(),
                "phenotype": zr_rider.phenotype_value,
                "phenotype_bias": zr_rider.phenotype_bias,
                "age": zr_rider.age,
                "race_finishes": zr_rider.race_finishes,
                "race_wins": zr_rider.race_wins,
                "race_podiums": zr_rider.race_podiums,
                "race_dnfs": zr_rider.race_dnfs,
                "handicap_flat": zr_rider.handicap_flat,
                "handicap_rolling": zr_rider.handicap_rolling,
                "handicap_hilly": zr_rider.handicap_hilly,
                "handicap_mountainous": zr_rider.handicap_mountainous,
                "updated": zr_rider.date_modified,
            }

        # Get last 5 race results
        recent_results = ZPRiderResults.objects.filter(zwid=profile_user.zwid).select_related("event")[:5]

    # Get YouTube videos from database (synced via background task)
    youtube_videos = profile_user.youtube_videos.all()[:5]

    # Get verification records for this user
    from apps.team.models import RaceReadyRecord

    verified_records = RaceReadyRecord.objects.filter(
        user=profile_user,
        status=RaceReadyRecord.Status.VERIFIED,
    ).order_by("-reviewed_date")
    pending_records = RaceReadyRecord.objects.filter(
        user=profile_user,
        status=RaceReadyRecord.Status.PENDING,
    ).order_by("-date_created")

    # Build verification summary: latest verified record per type
    verification_summary = {}
    for record in verified_records:
        if record.verify_type not in verification_summary:
            verification_summary[record.verify_type] = record

    return render(
        request,
        "accounts/public_profile.html",
        {
            "profile_user": profile_user,
            "zp_data": zp_data,
            "zr_data": zr_data,
            "racing_profile": _fetch_racing_profile(profile_user),
            "activity_window": _fetch_activity_window(profile_user),
            "is_own_profile": is_own_profile,
            "recent_results": recent_results,
            "youtube_videos": youtube_videos,
            "verification_summary": verification_summary,
            "pending_records": pending_records,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def profile_edit(request: HttpRequest) -> HttpResponse:
    """Edit user profile with HTMX support.

    Args:
        request: The HTTP request.

    Returns:
        Rendered profile form (partial for HTMX, full page otherwise).

    """
    from apps.accounts.services import get_approved_application, get_importable_fields

    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            # Refresh user from database to get updated is_profile_complete
            request.user.refresh_from_db()

            # Get missing banner fields for the success message
            missing_fields = []
            field_labels = {
                "first_name": "First Name",
                "last_name": "Last Name",
                "birth_year": "Birth Year",
                "gender": "Gender",
                "timezone": "Timezone",
                "country": "Country",
                "trainer": "Trainer",
                "heartrate_monitor": "Heart Rate Monitor",
                "zwid_verified": "Zwift Verification",
            }
            completion_status = request.user.profile_completion_status
            for field, is_complete in completion_status.items():
                if not is_complete:
                    missing_fields.append(field_labels.get(field, field))

            if request.headers.get("HX-Request"):
                # Return success message partial for HTMX
                return render(
                    request,
                    "accounts/partials/profile_form.html",
                    {"form": form, "success": True, "missing_fields": missing_fields},
                )
            if missing_fields:
                messages.success(
                    request,
                    f"Profile updated successfully. Still missing: {', '.join(missing_fields)}",
                )
            else:
                messages.success(request, "Profile updated successfully. Your profile is complete!")
            # Only redirect to profile if complete, otherwise stay on edit page
            if request.user.is_profile_complete:
                return redirect("accounts:profile")
            return redirect("accounts:profile_edit")
    else:
        form = ProfileForm(instance=request.user)

    # Check for approved MembershipApplication to import
    pending_application = None
    importable_fields = None
    if request.user.discord_id:
        pending_application = get_approved_application(request.user.discord_id)
        if pending_application:
            importable_fields = get_importable_fields(pending_application)
            # Only show import banner if there are fields to import
            if not importable_fields:
                pending_application = None

    context = {
        "form": form,
        "pending_application": pending_application,
        "importable_fields": importable_fields,
    }

    if request.headers.get("HX-Request"):
        template = "accounts/partials/profile_form.html"
    else:
        template = "accounts/profile_edit.html"
    return render(request, template, context)


@login_required
@require_GET
def profile_delete_confirm(request: HttpRequest) -> HttpResponse:
    """Show delete account confirmation page.

    Args:
        request: The HTTP request.

    Returns:
        Rendered delete confirmation page.

    """
    return render(request, "accounts/profile_delete.html")


@login_required
@require_POST
def profile_delete(request: HttpRequest) -> HttpResponse:
    """Delete user account.

    Requires user to type "Delete" (case-insensitive) to confirm.

    Args:
        request: The HTTP request.

    Returns:
        Redirect to home page after deletion, or back to confirmation if invalid.

    """
    confirmation = request.POST.get("confirmation", "").strip()
    if confirmation.lower() != "delete":
        logfire.info(
            "Account deletion not confirmed",
            user_id=request.user.pk,
            discord_id=request.user.discord_id,
        )
        messages.error(request, "Please type 'Delete' to confirm account deletion.")
        return redirect("accounts:profile_delete_confirm")

    user = request.user
    # logout() before the delete: the session row is keyed to the user, and the request
    # still needs a valid session to carry the success message to the next page.
    logout(request)
    delete_user_account(user)
    messages.success(request, "Your account has been deleted.")
    return redirect("/")


@login_required
@require_http_methods(["GET", "POST"])
def import_application_view(request: HttpRequest, application_id: str) -> HttpResponse:
    """Import data from approved MembershipApplication to user profile.

    GET: Show confirmation page with fields to import.
    POST: Perform import and redirect to profile_edit.

    Args:
        request: The HTTP request.
        application_id: UUID of the MembershipApplication.

    Returns:
        Rendered confirmation page or redirect after import.

    Raises:
        PermissionDenied: If application doesn't belong to this user.

    """
    import uuid

    from django.shortcuts import get_object_or_404

    from apps.accounts.services import get_importable_fields, import_application_to_user
    from apps.team.models import MembershipApplication

    # Parse UUID
    try:
        app_uuid = uuid.UUID(str(application_id))
    except ValueError:
        logfire.warning(
            "Invalid application UUID in import request",
            user_id=request.user.id,
            application_id=str(application_id),
        )
        messages.error(request, "Invalid application ID.")
        return redirect("accounts:profile_edit")

    # Get the application
    application = get_object_or_404(MembershipApplication, pk=app_uuid)

    # Security check: verify application belongs to this user
    if application.discord_id != request.user.discord_id:
        logfire.warning(
            "User attempted to import application belonging to another user",
            user_id=request.user.id,
            user_discord_id=request.user.discord_id,
            application_discord_id=application.discord_id,
            application_id=str(application_id),
        )
        raise PermissionDenied("You don't have permission to import this application.")

    # Verify application is approved
    if application.status != MembershipApplication.Status.APPROVED:
        logfire.warning(
            "User attempted to import non-approved application",
            user_id=request.user.id,
            application_id=str(application_id),
            application_status=application.status,
        )
        messages.error(request, "Only approved registrations can be imported.")
        return redirect("accounts:profile_edit")

    # Get importable fields
    importable_fields = get_importable_fields(application)

    if not importable_fields:
        messages.info(request, "No new data to import from your registration.")
        return redirect("accounts:profile_edit")

    if request.method == "POST":
        # Perform the import
        imported_fields = import_application_to_user(request.user, application)

        if imported_fields:
            field_list = ", ".join(imported_fields)
            messages.success(request, f"Successfully imported: {field_list}")
        else:
            messages.info(request, "No new data was imported (fields already filled).")

        return redirect("accounts:profile_edit")

    # GET: Show confirmation page
    return render(
        request,
        "accounts/import_application.html",
        {
            "application": application,
            "importable_fields": importable_fields,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def manual_zwift_verify(request: HttpRequest) -> HttpResponse:
    """Allow user to set their ZWID via ZwiftPower profile URL without marking as verified.

    Args:
        request: The HTTP request.

    Returns:
        Rendered manual verification modal partial.

    """
    import re

    error = None
    if request.method == "POST":
        raw_input = request.POST.get("zwiftpower_url", "").strip()
        zwid = None

        # Try to extract ZWID from ZwiftPower URL
        match = re.search(r"zwiftpower\.com/profile\.php\?z=(\d+)", raw_input)
        if match:
            zwid = int(match.group(1))
        elif raw_input.isdigit() and int(raw_input) > 0:
            zwid = int(raw_input)

        if zwid:
            request.user.zwid = zwid
            request.user.save(update_fields=["zwid"])
            logfire.info(
                "Manual ZWID set via verification page",
                user_id=request.user.id,
                discord_id=request.user.discord_id,
                zwid=zwid,
            )
            return render(
                request,
                "accounts/partials/manual_zwift_verify_modal.html",
                {"success": True, "zwid": zwid},
            )
        error = "Please enter a valid ZwiftPower profile URL or numeric Zwift ID."
        logfire.warning(
            "Invalid manual ZWID input",
            user_id=request.user.id,
            raw_input=raw_input,
        )

    return render(
        request,
        "accounts/partials/manual_zwift_verify_modal.html",
        {"error": error},
    )


ZAUTH_BANNER_DISMISSED_KEY = "zauth_banner_dismissed"


@login_required
@require_POST
def dismiss_zauth_banner(request: HttpRequest) -> HttpResponse:
    """Hide the Zwift OAuth banner for the rest of this browser session.

    Session-scoped on purpose: the prompt is an invitation rather than an error,
    so it should not nag on every page load, but it should come back next session
    until the account is actually connected. No model field, no migration.

    Args:
        request: The HTTP request.

    Returns:
        An empty 200 so htmx can swap the banner away.

    """
    request.session[ZAUTH_BANNER_DISMISSED_KEY] = True
    return HttpResponse("")


@login_required
@require_POST
def unverify_zwift(request: HttpRequest) -> HttpResponse:
    """Remove Zwift verification from user's account.

    Args:
        request: The HTTP request.

    Returns:
        Rendered Zwift status partial for HTMX requests.

    """
    request.user.zwid = None
    request.user.zwid_verified = False
    request.user.save(update_fields=["zwid", "zwid_verified"])

    return render(
        request,
        "accounts/partials/zwift_status.html",
        {"user": request.user},
    )


@login_required
@require_POST
def submit_race_ready(request: HttpRequest) -> HttpResponse:
    """Submit a race ready verification record.

    Args:
        request: The HTTP request.

    Returns:
        Redirect to profile or rendered partial for HTMX.

    """
    # Get allowed verification types to validate and filter form choices
    allowed_types = get_user_verification_types(request.user)
    logfire.info(
        "Race ready form submission attempt",
        user_id=request.user.id,
        discord_username=request.user.discord_username,
        verify_type=request.POST.get("verify_type"),
        has_media_file=bool(request.FILES.get("media_file")),
        has_url=bool(request.POST.get("url")),
    )
    form = RaceReadyRecordForm(
        request.POST,
        request.FILES,
        allowed_types=allowed_types,
        unit_preference=request.user.unit_preference,
    )

    if form.is_valid():
        record = form.save(commit=False)
        record.user = request.user
        record.save()
        logfire.info(
            "Race ready record created",
            user_id=request.user.id,
            discord_username=request.user.discord_username,
            record_id=record.id,
            verify_type=record.verify_type,
        )
        messages.success(request, "Race ready record submitted successfully.")

        # Notify squad captains about new submission
        from apps.team.tasks import notify_captains_verification

        notify_captains_verification.enqueue(
            user_id=request.user.id,
            record_id=record.id,
            notification_type="submitted",
        )

        # Notify the performance verification team when a power record is submitted
        if record.verify_type == "power":
            from apps.team.tasks import notify_pvt_power_submission

            notify_pvt_power_submission.enqueue(record_id=record.id)

        if request.headers.get("HX-Request"):
            # Return updated race ready section
            race_ready_records = request.user.race_ready_records.all()
            latest_by_type = {}
            for verify_type in ["weight_full", "weight_light", "height", "power"]:
                rec = race_ready_records.filter(verify_type=verify_type).first()
                if rec:
                    latest_by_type[verify_type] = rec
            return render(
                request,
                "accounts/partials/race_ready_form.html",
                {
                    "race_ready_form": RaceReadyRecordForm(
                        allowed_types=allowed_types,
                        unit_preference=request.user.unit_preference,
                    ),
                    "race_ready_records": race_ready_records,
                    "latest_by_type": latest_by_type,
                    "verify_type_options": build_verify_type_options(request.user),
                    "success": True,
                    "weight_instructions_url": config.WEIGHT_INSTRUCTIONS_URL,
                    "height_instructions_url": config.HEIGHT_INSTRUCTIONS_URL,
                    "unit_preference": request.user.unit_preference,
                },
            )
        return redirect("accounts:verification")
    logfire.warning(
        "Race ready form validation failed",
        user_id=request.user.id,
        discord_username=request.user.discord_username,
        verify_type=request.POST.get("verify_type"),
        form_errors=dict(form.errors),
    )
    if request.headers.get("HX-Request"):
        return render(
            request,
            "accounts/partials/race_ready_form.html",
            {
                "race_ready_form": form,
                "verify_type_options": build_verify_type_options(request.user),
                "weight_instructions_url": config.WEIGHT_INSTRUCTIONS_URL,
                "height_instructions_url": config.HEIGHT_INSTRUCTIONS_URL,
                "unit_preference": request.user.unit_preference,
            },
        )
    messages.error(request, "Please correct the errors below.")
    return redirect("accounts:verification")


def _get_config_sections() -> dict:
    """Build configuration sections from CONSTANCE_CONFIG_FIELDSETS.

    Returns:
        Dictionary with section keys, names, and setting details.

    """
    constance_config = settings.CONSTANCE_CONFIG
    fieldsets = settings.CONSTANCE_CONFIG_FIELDSETS

    sections = {}
    for section_name, setting_keys in fieldsets.items():
        section_key = section_name.lower().replace(" ", "_")
        section_settings = []

        for key in setting_keys:
            if key in constance_config:
                setting_def = constance_config[key]
                default_value, description, field_type = setting_def[0], setting_def[1], setting_def[2]

                # Determine the input type
                if field_type == "password_field":
                    input_type = "password"
                elif field_type == "json_list_field":
                    input_type = "json_list"
                elif field_type == "string_list_field":
                    input_type = "string_list"
                elif field_type == "json_field":
                    input_type = "json"
                elif field_type == "textarea_field":
                    input_type = "textarea"
                elif field_type is bool:
                    input_type = "boolean"
                elif field_type is int:
                    input_type = "number"
                else:
                    input_type = "text"

                # Get current value from constance
                current_value = getattr(config, key, default_value)

                section_settings.append({
                    "key": key,
                    "description": description,
                    "input_type": input_type,
                    "default_value": default_value,
                    "current_value": current_value,
                })

        sections[section_key] = {
            "name": section_name,
            "key": section_key,
            "settings": section_settings,
        }

    return sections


@login_required
@require_POST
def compliance_block_add(request: HttpRequest) -> HttpResponse:
    """Bar a Discord account from signing in.

    Args:
        request: The HTTP request, carrying ``discord_id`` and an optional ``note``.

    Returns:
        Redirect back to the Compliance section.

    Raises:
        PermissionDenied: If the user lacks app_admin and is not a superuser.

    """
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    discord_id = request.POST.get("discord_id", "").strip()
    if not (discord_id.isascii() and discord_id.isdecimal()):
        messages.error(request, "Enter a Discord ID: digits only, no username.")
        return redirect("config_section_page", section_key="compliance")

    if request.user.discord_id == discord_id:
        # Cheap guard against the most annoying possible mistake.
        messages.error(request, "That is your own Discord account.")
        return redirect("config_section_page", section_key="compliance")

    _, created = BlockedDiscordId.objects.get_or_create(
        discord_id=discord_id,
        defaults={"note": request.POST.get("note", "").strip(), "blocked_by": request.user},
    )

    # The block only bites at the next login, and sessions last two weeks by default — so
    # end any session they already hold. Rotating the (unusable) password changes the
    # session auth hash, which is what AuthenticationMiddleware checks on every request.
    signed_out = False
    if created:
        blocked_user = User.objects.filter(discord_id=discord_id).first()
        if blocked_user:
            blocked_user.set_unusable_password()
            blocked_user.save(update_fields=["password"])
            signed_out = True

    logfire.info(
        "Discord login block added" if created else "Discord login block already existed",
        discord_id=discord_id,
        admin_user_id=request.user.pk,
        sessions_invalidated=signed_out,
    )
    if created:
        tail = " They have been signed out of any active session." if signed_out else ""
        messages.success(request, f"{discord_id} can no longer sign in.{tail}")
    else:
        messages.info(request, f"{discord_id} was already blocked.")
    return redirect("config_section_page", section_key="compliance")


@login_required
@require_POST
def compliance_block_remove(request: HttpRequest) -> HttpResponse:
    """Lift a login block.

    Args:
        request: The HTTP request, carrying ``block_id``.

    Returns:
        Redirect back to the Compliance section.

    Raises:
        PermissionDenied: If the user lacks app_admin and is not a superuser.

    """
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    block = get_object_or_404(BlockedDiscordId, pk=request.POST.get("block_id") or 0)
    discord_id = block.discord_id
    block.delete()
    logfire.info("Discord login block removed", discord_id=discord_id, admin_user_id=request.user.pk)
    messages.success(request, f"{discord_id} can sign in again.")
    return redirect("config_section_page", section_key="compliance")


@login_required
@require_GET
def compliance_delete_confirm(request: HttpRequest) -> HttpResponse:
    """Show what deleting a chosen member's account would do, before it is done.

    Deliberately a separate page rather than a modal on the picker: choosing the wrong
    person from a long list is the likeliest way this goes wrong, so the account is named
    on its own screen alongside the same effects the rider would see themselves.

    Args:
        request: The HTTP request, carrying ``user_id``.

    Returns:
        The confirmation page.

    Raises:
        PermissionDenied: If the user lacks app_admin and is not a superuser.

    """
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    target = get_object_or_404(User, pk=request.GET.get("user_id") or 0)
    return render(
        request,
        "accounts/config_compliance_confirm.html",
        {"target": target, "sections": _get_config_sections(), "current_section_key": "compliance"},
    )


@login_required
@require_POST
def compliance_delete_user(request: HttpRequest) -> HttpResponse:
    """Delete another member's account, as if they had done it themselves.

    Runs the same ``delete_user_account`` service the rider's own page uses, so an erasure
    carried out on someone's behalf reaches exactly what theirs would.

    Confirmation is the target's username rather than the word "Delete" used on the
    self-serve page. The risk here is different: the rider deleting their own account
    cannot pick the wrong person, and an admin working from a list of hundreds can.

    Args:
        request: The HTTP request, carrying ``user_id`` and ``confirmation``.

    Returns:
        Redirect back to the Compliance section.

    Raises:
        PermissionDenied: If the user lacks app_admin and is not a superuser.

    """
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    target = get_object_or_404(User, pk=request.POST.get("user_id") or 0)

    if request.POST.get("confirmation", "").strip() != target.username:
        logfire.info(
            "Admin account deletion not confirmed",
            target_user_id=target.pk,
            admin_user_id=request.user.pk,
        )
        messages.error(request, f"Type the username {target.username} exactly to confirm.")
        return redirect(f"{reverse('compliance_delete_confirm')}?user_id={target.pk}")

    if target.pk == request.user.pk:
        # Not forbidden as such, but the self-serve page logs the actor out afterwards and
        # this one does not -- so send them there rather than leave a dead session behind.
        messages.error(request, "Use your own profile page to delete your own account.")
        return redirect("accounts:profile_delete_confirm")

    label = target.get_full_name() or target.username
    delete_user_account(target, deleted_by=request.user)
    messages.success(request, f"Deleted the account for {label}.")
    return redirect("config_section_page", section_key="compliance")


@login_required
@require_GET
def config_settings(request: HttpRequest) -> HttpResponse:
    """Redirect to first configuration section.

    Args:
        request: The HTTP request.

    Returns:
        Redirect to first section page.

    Raises:
        PermissionDenied: If user lacks app_admin permission and is not superuser.

    """
    # Check permissions: app_admin OR superuser
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    sections = _get_config_sections()
    first_section_key = next(iter(sections.keys()))
    return redirect("config_section_page", section_key=first_section_key)


@login_required
@require_GET
def config_section_page(request: HttpRequest, section_key: str) -> HttpResponse:
    """Display a single configuration section with sidebar navigation.

    Args:
        request: The HTTP request.
        section_key: The section key to display.

    Returns:
        Rendered configuration section page.

    Raises:
        PermissionDenied: If user lacks app_admin permission and is not superuser.

    """
    # Check permissions: app_admin OR superuser
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    sections = _get_config_sections()

    # Handle special "site_images" section
    if section_key == "site_images":
        from gotta_bike_platform.models import SiteSettings

        site_settings_obj = SiteSettings.get_settings()
        return render(
            request,
            "accounts/config_section_page.html",
            {
                "sections": sections,
                "current_section_key": section_key,
                "current_section": {"name": "Site Images", "key": "site_images"},
                "is_site_images": True,
                "site_settings_obj": site_settings_obj,
                "zp_emoji_items": _build_zp_emoji_items(site_settings_obj),
                "zr_emoji_items": _build_zr_emoji_items(site_settings_obj),
                "phenotype_emoji_items": _build_phenotype_emoji_items(site_settings_obj),
                "available_roles": [],
            },
        )

    # Handle special "compliance" section
    if section_key == "compliance":
        return render(
            request,
            "accounts/config_section_page.html",
            {
                "sections": sections,
                "current_section_key": section_key,
                "current_section": {"name": "Compliance", "key": "compliance"},
                "is_compliance": True,
                # Everyone with an account, so an erasure request can be honoured for
                # someone who has already lost their team role.
                "deletable_users": User.objects.order_by("first_name", "last_name", "username"),
                "blocked_logins": BlockedDiscordId.objects.select_related("blocked_by"),
                "available_roles": [],
            },
        )

    # Handle special "background_tasks" section
    if section_key == "background_tasks":
        tasks = _get_task_registry()
        _enrich_tasks_with_last_run(tasks)
        return render(
            request,
            "accounts/config_section_page.html",
            {
                "sections": sections,
                "current_section_key": section_key,
                "current_section": {"name": "Background Tasks", "key": "background_tasks"},
                "is_background_tasks": True,
                "tasks": tasks,
                "available_roles": [],
            },
        )

    if section_key not in sections:
        return redirect("config_settings")

    section = sections[section_key]

    # Get Discord roles for permission mapping selects
    from apps.team.models import DiscordRole

    available_roles = DiscordRole.objects.filter(managed=False).order_by("-position")

    return render(
        request,
        "accounts/config_section_page.html",
        {
            "sections": sections,
            "current_section_key": section_key,
            "current_section": section,
            "is_site_images": False,
            "available_roles": available_roles,
        },
    )


@login_required
@require_POST
def config_section_update(request: HttpRequest, section_key: str) -> HttpResponse:
    """Update configuration settings for a specific section via HTMX.

    Args:
        request: The HTTP request.
        section_key: The section key to update.

    Returns:
        Rendered section partial with updated values and success message.

    Raises:
        PermissionDenied: If user lacks app_admin permission and is not superuser.

    """
    # Check permissions: app_admin OR superuser
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    sections = _get_config_sections()

    if section_key not in sections:
        return HttpResponse("Section not found", status=404)

    section = sections[section_key]
    errors = []

    # Process each setting in the section
    for setting in section["settings"]:
        key = setting["key"]
        input_type = setting["input_type"]

        if input_type == "boolean":
            # Checkbox - if present, True; if absent, False
            value = key in request.POST
        elif input_type == "number":
            try:
                value = int(request.POST.get(key, 0))
            except ValueError:
                errors.append(f"{key}: Invalid number")
                continue
        elif input_type == "json_list":
            # Multi-select returns list of values
            selected_values = request.POST.getlist(key)
            value = json.dumps(selected_values)
        elif input_type == "string_list":
            # Sortable list returns multiple values with same name
            list_values = request.POST.getlist(key)
            # Filter out empty strings
            list_values = [v.strip() for v in list_values if v.strip()]
            value = json.dumps(list_values)
        elif input_type == "json":
            raw_value = request.POST.get(key, "")
            if raw_value:
                try:
                    # Validate JSON
                    json.loads(raw_value)
                    value = raw_value
                except json.JSONDecodeError:
                    errors.append(f"{key}: Invalid JSON format")
                    continue
            else:
                value = "{}"
        else:
            # Text and password fields
            value = request.POST.get(key, "")

        # Record what actually changed before writing it.
        #
        # These settings decide who holds every permission in the app, including who may
        # view verification photographs -- so an admin can widen their own access here. That
        # was previously untraceable: no actor, no before, no after. Secrets are noted as
        # changed without their values.
        previous = getattr(config, key, None)
        if previous != value:
            secret = setting.get("input_type") == "password"
            logfire.info(
                "Site setting changed",
                section=section_key,
                setting=key,
                changed_by_id=request.user.pk,
                changed_by=request.user.get_username(),
                old_value="(hidden)" if secret else previous,
                new_value="(hidden)" if secret else value,
                is_permission_mapping=key.startswith("PERM_"),
            )

        # Save to constance
        setattr(config, key, value)

    # Refresh sections to get updated values
    sections = _get_config_sections()
    section = sections[section_key]

    # Get Discord roles for permission mapping selects
    from apps.team.models import DiscordRole

    available_roles = DiscordRole.objects.filter(managed=False).order_by("-position")

    return render(
        request,
        "accounts/partials/config_section.html",
        {
            "section": section,
            "available_roles": available_roles,
            "success": not errors,
            "errors": errors,
        },
    )


def _build_zp_emoji_items(site_settings_obj) -> list[dict]:
    """Build list of ZP category emoji items for template rendering.

    Args:
        site_settings_obj: SiteSettings instance.

    Returns:
        List of dicts with field_name, label, and file for each ZP category emoji.

    """
    items = []
    for field_name, label in [
        ("zp_a_plus_emoji", "ZP A+ Category"),
        ("zp_a_emoji", "ZP A Category"),
        ("zp_b_emoji", "ZP B Category"),
        ("zp_c_emoji", "ZP C Category"),
        ("zp_d_emoji", "ZP D Category"),
        ("zp_e_emoji", "ZP E Category"),
    ]:
        file_field = getattr(site_settings_obj, field_name, None)
        items.append({
            "field_name": field_name,
            "label": label,
            "file": file_field if file_field else None,
        })
    return items


def _build_zr_emoji_items(site_settings_obj) -> list[dict]:
    """Build list of ZR category emoji items for template rendering.

    Args:
        site_settings_obj: SiteSettings instance.

    Returns:
        List of dicts with field_name, label, and file for each ZR category emoji.

    """
    items = []
    for field_name, label in [
        ("zr_diamond_emoji", "Diamond"),
        ("zr_ruby_emoji", "Ruby"),
        ("zr_emerald_emoji", "Emerald"),
        ("zr_sapphire_emoji", "Sapphire"),
        ("zr_amethyst_emoji", "Amethyst"),
        ("zr_platinum_emoji", "Platinum"),
        ("zr_gold_emoji", "Gold"),
        ("zr_silver_emoji", "Silver"),
        ("zr_bronze_emoji", "Bronze"),
        ("zr_copper_emoji", "Copper"),
    ]:
        file_field = getattr(site_settings_obj, field_name, None)
        items.append({
            "field_name": field_name,
            "label": label,
            "file": file_field if file_field else None,
        })
    return items


def _build_phenotype_emoji_items(site_settings_obj) -> list[dict]:
    """Build list of phenotype emoji items for template rendering.

    Args:
        site_settings_obj: SiteSettings instance.

    Returns:
        List of dicts with field_name, label, and file for each phenotype emoji.

    """
    items = []
    for field_name, label in [
        ("phenotype_allrounder_emoji", "All-Rounder"),
        ("phenotype_climber_emoji", "Climber"),
        ("phenotype_puncheur_emoji", "Puncheur"),
        ("phenotype_tt_emoji", "Time Trialist"),
        ("phenotype_sprinter_emoji", "Sprinter"),
        ("phenotype_pursuiter_emoji", "Pursuiter"),
    ]:
        file_field = getattr(site_settings_obj, field_name, None)
        items.append({
            "field_name": field_name,
            "label": label,
            "file": file_field if file_field else None,
        })
    return items


@login_required
@require_POST
def config_site_images_update(request: HttpRequest) -> HttpResponse:
    """Update site images (logo and hero) via HTMX.

    Args:
        request: The HTTP request with uploaded files.

    Returns:
        Rendered site images partial with updated values.

    Raises:
        PermissionDenied: If user lacks app_admin permission and is not superuser.

    """
    from gotta_bike_platform.models import SiteSettings

    # Check permissions: app_admin OR superuser
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    site_settings_obj = SiteSettings.get_settings()
    success = False
    errors = []
    user_id = request.user.id
    username = request.user.username

    # Handle logo upload
    if "site_logo" in request.FILES:
        uploaded_file = request.FILES["site_logo"]
        site_settings_obj.site_logo = uploaded_file
        success = True
        logfire.info(
            "Site logo uploaded",
            user_id=user_id,
            username=username,
            filename=uploaded_file.name,
            file_size=uploaded_file.size,
        )

    # Handle logo deletion
    if request.POST.get("delete_logo") == "true" and site_settings_obj.site_logo:
        old_logo_name = site_settings_obj.site_logo.name
        site_settings_obj.site_logo.delete(save=False)
        site_settings_obj.site_logo = None
        success = True
        logfire.info(
            "Site logo deleted",
            user_id=user_id,
            username=username,
            deleted_file=old_logo_name,
        )

    # Handle favicon upload - convert to PNG and resize to 64x64
    if "favicon" in request.FILES:
        from io import BytesIO

        from django.core.files.base import ContentFile
        from PIL import Image

        try:
            uploaded_file = request.FILES["favicon"]
            img = Image.open(uploaded_file)

            # Convert to RGBA if necessary (for transparency support)
            if img.mode not in ("RGBA", "RGB"):
                img = img.convert("RGBA")

            # Resize to fit within 64x64, maintaining aspect ratio
            img.thumbnail((64, 64), Image.Resampling.LANCZOS)

            # Save as PNG to a BytesIO buffer
            buffer = BytesIO()
            img.save(buffer, format="PNG", optimize=True)
            buffer.seek(0)

            # Create a new file with .png extension
            site_settings_obj.favicon.save("favicon.png", ContentFile(buffer.read()), save=False)
            success = True
            logfire.info(
                "Favicon uploaded",
                user_id=user_id,
                username=username,
                original_filename=uploaded_file.name,
                original_size=uploaded_file.size,
            )
        except Exception as e:
            errors.append(f"Favicon: {e!s}")
            logfire.error(
                "Favicon upload failed",
                user_id=user_id,
                username=username,
                error=str(e),
            )

    # Handle favicon deletion
    if request.POST.get("delete_favicon") == "true" and site_settings_obj.favicon:
        old_favicon_name = site_settings_obj.favicon.name
        site_settings_obj.favicon.delete(save=False)
        site_settings_obj.favicon = None
        success = True
        logfire.info(
            "Favicon deleted",
            user_id=user_id,
            username=username,
            deleted_file=old_favicon_name,
        )

    # Handle hero image upload
    if "hero_image" in request.FILES:
        uploaded_file = request.FILES["hero_image"]
        site_settings_obj.hero_image = uploaded_file
        success = True
        logfire.info(
            "Hero image uploaded",
            user_id=user_id,
            username=username,
            filename=uploaded_file.name,
            file_size=uploaded_file.size,
        )

    # Handle hero image deletion
    if request.POST.get("delete_hero") == "true" and site_settings_obj.hero_image:
        old_hero_name = site_settings_obj.hero_image.name
        site_settings_obj.hero_image.delete(save=False)
        site_settings_obj.hero_image = None
        success = True
        logfire.info(
            "Hero image deleted",
            user_id=user_id,
            username=username,
            deleted_file=old_hero_name,
        )

    # Handle verification emoji and ZP category emoji uploads and deletions
    for emoji_field, label in [
        ("not_verified_emoji", "Not Verified Emoji"),
        ("verified_emoji", "Verified Emoji"),
        ("extra_verified_emoji", "Extra Verified Emoji"),
        ("zp_a_plus_emoji", "ZP A+ Category Emoji"),
        ("zp_a_emoji", "ZP A Category Emoji"),
        ("zp_b_emoji", "ZP B Category Emoji"),
        ("zp_c_emoji", "ZP C Category Emoji"),
        ("zp_d_emoji", "ZP D Category Emoji"),
        ("zp_e_emoji", "ZP E Category Emoji"),
        ("zr_diamond_emoji", "ZR Diamond Emoji"),
        ("zr_ruby_emoji", "ZR Ruby Emoji"),
        ("zr_emerald_emoji", "ZR Emerald Emoji"),
        ("zr_sapphire_emoji", "ZR Sapphire Emoji"),
        ("zr_amethyst_emoji", "ZR Amethyst Emoji"),
        ("zr_platinum_emoji", "ZR Platinum Emoji"),
        ("zr_gold_emoji", "ZR Gold Emoji"),
        ("zr_silver_emoji", "ZR Silver Emoji"),
        ("zr_bronze_emoji", "ZR Bronze Emoji"),
        ("zr_copper_emoji", "ZR Copper Emoji"),
        ("phenotype_allrounder_emoji", "Phenotype All-Rounder"),
        ("phenotype_climber_emoji", "Phenotype Climber"),
        ("phenotype_puncheur_emoji", "Phenotype Puncheur"),
        ("phenotype_tt_emoji", "Phenotype Time Trialist"),
        ("phenotype_sprinter_emoji", "Phenotype Sprinter"),
        ("phenotype_pursuiter_emoji", "Phenotype Pursuiter"),
    ]:
        if emoji_field in request.FILES:
            uploaded_file = request.FILES[emoji_field]
            setattr(site_settings_obj, emoji_field, uploaded_file)
            success = True
            logfire.info(
                f"{label} uploaded",
                user_id=user_id,
                username=username,
                filename=uploaded_file.name,
                file_size=uploaded_file.size,
            )
        delete_key = f"delete_{emoji_field}"
        if request.POST.get(delete_key) == "true" and getattr(site_settings_obj, emoji_field):
            old_name = getattr(site_settings_obj, emoji_field).name
            getattr(site_settings_obj, emoji_field).delete(save=False)
            setattr(site_settings_obj, emoji_field, None)
            success = True
            logfire.info(
                f"{label} deleted",
                user_id=user_id,
                username=username,
                deleted_file=old_name,
            )

    if success:
        site_settings_obj.save()

    return render(
        request,
        "accounts/partials/config_site_images.html",
        {
            "site_settings_obj": site_settings_obj,
            "zp_emoji_items": _build_zp_emoji_items(site_settings_obj),
            "zr_emoji_items": _build_zr_emoji_items(site_settings_obj),
            "phenotype_emoji_items": _build_phenotype_emoji_items(site_settings_obj),
            "success": success,
            "errors": errors,
        },
    )


def _get_task_registry() -> dict:
    """Get the unified task registry.

    Returns:
        Dictionary mapping task names to task info (task function and description).

    """
    from gotta_bike_platform.task_registry import TASK_REGISTRY

    return TASK_REGISTRY


def _enrich_tasks_with_last_run(tasks: dict) -> None:
    """Add last_run info to each task in the registry.

    Queries DBTaskResult for the most recent run of each task and adds
    ``last_status``, ``last_finished_at``, and ``time_since_last_run`` keys.

    Args:
        tasks: The task registry dict (mutated in place).

    """
    from django.apps import apps
    from django.db.models import Q
    from django.utils import timezone

    DBTaskResult = apps.get_model("django_tasks_database", "DBTaskResult")

    for task_name, task_info in tasks.items():
        task_func = task_info["task"]
        task_path = task_func.module_path

        last_run = (
            DBTaskResult.objects.filter(
                Q(task_path=task_path) | Q(task_path=task_name),
                finished_at__isnull=False,
            )
            .order_by("-finished_at")
            .values("status", "finished_at")
            .first()
        )

        if last_run and last_run["finished_at"]:
            task_info["last_status"] = last_run["status"]
            task_info["last_finished_at"] = last_run["finished_at"]
            delta = timezone.now() - last_run["finished_at"]
            total_minutes = int(delta.total_seconds() // 60)
            hours, minutes = divmod(total_minutes, 60)
            task_info["time_since_last_run"] = f"{hours}h {minutes}m ago"
        else:
            task_info["last_status"] = None
            task_info["last_finished_at"] = None
            task_info["time_since_last_run"] = "Never"


@login_required
@require_POST
def config_trigger_task(request: HttpRequest) -> HttpResponse:
    """Trigger a background task manually via HTMX.

    Args:
        request: The HTTP request with task_name in POST data.

    Returns:
        Rendered tasks partial with success/error message.

    Raises:
        PermissionDenied: If user lacks app_admin permission and is not superuser.

    """
    # Check permissions: app_admin OR superuser
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    task_name = request.POST.get("task_name", "")
    tasks = _get_task_registry()
    triggered_task = None
    dry_run_result = None
    error = None

    if task_name not in tasks:
        error = f"Task '{task_name}' not found."
        logfire.warning(
            "Attempted to trigger unknown task",
            user_id=request.user.id,
            username=request.user.username,
            task_name=task_name,
        )
    else:
        task_info = tasks[task_name]
        task_func = task_info["task"]
        kwargs = {}
        for param in task_info.get("params", []):
            value = request.POST.get(param["name"])
            if param["type"] == "number" and value:
                kwargs[param["name"]] = int(value)
            elif param["type"] == "checkbox":
                kwargs[param["name"]] = value == "on"
        # Run dry_run tasks synchronously so results display in the UI
        if kwargs.get("dry_run"):
            try:
                dry_run_result = task_func.call(**kwargs)
            except Exception as e:
                logfire.error("Dry run task failed", task_name=task_name, error=str(e))
                error = f"Dry run failed: {e}"
            else:
                triggered_task = task_name
        else:
            task_func.enqueue(**kwargs)
            triggered_task = task_name
        logfire.info(
            "Background task triggered manually",
            user_id=request.user.id,
            username=request.user.username,
            task_name=task_name,
            task_kwargs=kwargs,
        )

    return render(
        request,
        "accounts/partials/config_tasks.html",
        {
            "tasks": tasks,
            "triggered_task": triggered_task,
            "dry_run_result": dry_run_result,
            "error": error,
        },
    )


@login_required
@require_POST
def markdown_preview(request: HttpRequest) -> HttpResponse:
    """Render markdown text as HTML for preview.

    Args:
        request: The HTTP request with 'text' in POST data.

    Returns:
        Rendered HTML content.

    Raises:
        PermissionDenied: If user lacks app_admin permission and is not superuser.

    """
    import markdown

    # Check permissions: app_admin OR superuser
    if not request.user.is_superuser and not request.user.is_app_admin:
        raise PermissionDenied("You don't have permission to access this page.")

    text = request.POST.get("text", "")
    if not text:
        return HttpResponse('<p class="text-base-content/50 italic">No content to preview</p>')

    # Render markdown with same extensions as render_markdown template filter
    html = markdown.markdown(
        text,
        extensions=[
            "nl2br",  # Convert newlines to <br>
            "sane_lists",  # Better list handling
            "tables",  # Support tables
        ],
    )
    return HttpResponse(html)

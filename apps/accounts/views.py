"""Views for accounts app."""

from constance import config
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.forms import ProfileForm, ZwiftVerificationForm
from apps.team.forms import RaceReadyRecordForm
from apps.zwift.utils import fetch_zwift_id


@login_required
@require_GET
def profile_view(request: HttpRequest) -> HttpResponse:
    """Display user profile page.

    Args:
        request: The HTTP request.

    Returns:
        Rendered profile page.

    """
    form = ProfileForm(instance=request.user)
    race_ready_form = RaceReadyRecordForm()

    # Get all race ready records for the user
    race_ready_records = request.user.race_ready_records.all()

    # Get the most recent record for each verify_type
    latest_by_type = {}
    for verify_type in ["weight_full", "weight_light", "height", "power"]:
        record = race_ready_records.filter(verify_type=verify_type).first()
        if record:
            latest_by_type[verify_type] = record

    return render(
        request,
        "accounts/profile.html",
        {
            "form": form,
            "race_ready_form": race_ready_form,
            "race_ready_records": race_ready_records,
            "latest_by_type": latest_by_type,
            "weight_instructions_url": config.WEIGHT_INSTRUCTIONS_URL,
            "height_instructions_url": config.HEIGHT_INSTRUCTIONS_URL,
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
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            # Refresh user from database to get updated is_profile_complete
            request.user.refresh_from_db()
            if request.headers.get("HX-Request"):
                # Return success message partial for HTMX
                return render(
                    request,
                    "accounts/partials/profile_form.html",
                    {"form": form, "success": True},
                )
            messages.success(request, "Profile updated successfully.")
            # Only redirect to profile if complete, otherwise stay on edit page
            if request.user.is_profile_complete:
                return redirect("accounts:profile")
            return redirect("accounts:profile_edit")
    else:
        form = ProfileForm(instance=request.user)

    if request.headers.get("HX-Request"):
        template = "accounts/partials/profile_form.html"
    else:
        template = "accounts/profile_edit.html"
    return render(request, template, {"form": form})


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

    Args:
        request: The HTTP request.

    Returns:
        Redirect to home page after deletion.

    """
    user = request.user
    logout(request)
    user.delete()
    messages.success(request, "Your account has been deleted.")
    return redirect("/")


@login_required
@require_http_methods(["GET", "POST"])
def verify_zwift(request: HttpRequest) -> HttpResponse:
    """Verify user's Zwift account and fetch their Zwift ID.

    Args:
        request: The HTTP request.

    Returns:
        Rendered verification modal partial for HTMX requests.

    """
    if request.method == "POST":
        form = ZwiftVerificationForm(request.POST)
        if form.is_valid():
            zwift_username = form.cleaned_data["zwift_username"]
            zwift_password = form.cleaned_data["zwift_password"]

            # Fetch Zwift ID using the credentials
            zwift_id = fetch_zwift_id(zwift_username, zwift_password)

            if zwift_id:
                # Update user's Zwift ID and mark as verified
                request.user.zwid = int(zwift_id)
                request.user.zwid_verified = True
                request.user.save(update_fields=["zwid", "zwid_verified"])

                return render(
                    request,
                    "accounts/partials/zwift_verify_modal.html",
                    {"success": True, "zwift_id": zwift_id},
                )
            else:
                form.add_error(None, "Could not verify Zwift credentials. Please check your email and password.")
    else:
        form = ZwiftVerificationForm()

    return render(
        request,
        "accounts/partials/zwift_verify_modal.html",
        {"form": form},
    )


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
    form = RaceReadyRecordForm(request.POST, request.FILES)
    if form.is_valid():
        record = form.save(commit=False)
        record.user = request.user
        record.save()
        messages.success(request, "Race ready record submitted successfully.")

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
                    "race_ready_form": RaceReadyRecordForm(),
                    "race_ready_records": race_ready_records,
                    "latest_by_type": latest_by_type,
                    "success": True,
                    "weight_instructions_url": config.WEIGHT_INSTRUCTIONS_URL,
                    "height_instructions_url": config.HEIGHT_INSTRUCTIONS_URL,
                },
            )
        return redirect("accounts:profile")
    else:
        if request.headers.get("HX-Request"):
            return render(
                request,
                "accounts/partials/race_ready_form.html",
                {
                    "race_ready_form": form,
                    "weight_instructions_url": config.WEIGHT_INSTRUCTIONS_URL,
                    "height_instructions_url": config.HEIGHT_INSTRUCTIONS_URL,
                },
            )
        messages.error(request, "Please correct the errors below.")
        return redirect("accounts:profile")

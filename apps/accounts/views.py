"""Views for accounts app."""

import json

import logfire
from constance import config
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import team_member_required
from apps.accounts.forms import ProfileForm, ZwiftVerificationForm
from apps.accounts.models import User
from apps.team.forms import RaceReadyRecordForm
from apps.team.services import get_user_verification_types
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
            "unit_preference": request.user.unit_preference,
        },
    )


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

    profile_user = get_object_or_404(User, id=user_id)

    # Don't allow viewing own profile via public URL (redirect to private profile)
    if profile_user == request.user:
        return redirect("accounts:profile")

    return render(request, "accounts/public_profile.html", {
        "profile_user": profile_user,
    })


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

    Requires user to type "Delete" (case-insensitive) to confirm.

    Args:
        request: The HTTP request.

    Returns:
        Redirect to home page after deletion, or back to confirmation if invalid.

    """
    confirmation = request.POST.get("confirmation", "").strip()
    if confirmation.lower() != "delete":
        messages.error(request, "Please type 'Delete' to confirm account deletion.")
        return redirect("accounts:profile_delete_confirm")

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
    # Get allowed verification types to validate and filter form choices
    allowed_types = get_user_verification_types(request.user)
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
                    "race_ready_form": RaceReadyRecordForm(
                        allowed_types=allowed_types,
                        unit_preference=request.user.unit_preference,
                    ),
                    "race_ready_records": race_ready_records,
                    "latest_by_type": latest_by_type,
                    "success": True,
                    "weight_instructions_url": config.WEIGHT_INSTRUCTIONS_URL,
                    "height_instructions_url": config.HEIGHT_INSTRUCTIONS_URL,
                    "unit_preference": request.user.unit_preference,
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
                    "unit_preference": request.user.unit_preference,
                },
            )
        messages.error(request, "Please correct the errors below.")
        return redirect("accounts:profile")


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

    if success:
        site_settings_obj.save()

    return render(
        request,
        "accounts/partials/config_site_images.html",
        {
            "site_settings_obj": site_settings_obj,
            "success": success,
            "errors": errors,
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
            "nl2br",       # Convert newlines to <br>
            "sane_lists",  # Better list handling
            "tables",      # Support tables
        ],
    )
    return HttpResponse(html)

"""Service functions for accounts app."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import logfire

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.team.models import MembershipApplication


def get_approved_application(discord_id: str) -> MembershipApplication | None:
    """Find approved MembershipApplication matching discord_id.

    Args:
        discord_id: The Discord user ID to match.

    Returns:
        The approved MembershipApplication if found, None otherwise.

    """
    from apps.team.models import MembershipApplication

    if not discord_id:
        return None

    try:
        return MembershipApplication.objects.get(
            discord_id=discord_id,
            status=MembershipApplication.Status.APPROVED,
        )
    except MembershipApplication.DoesNotExist:
        return None


# Field mapping from MembershipApplication to User
# Format: (application_field, user_field, label, transform_func)
FIELD_MAPPING: list[tuple[str, str, str, Callable | None]] = [
    ("first_name", "first_name", "First Name", None),
    ("last_name", "last_name", "Last Name", None),
    ("email", "email", "Email", None),
    ("zwift_id", "zwid", "Zwift ID", lambda v: int(v) if v else None),
    ("zwift_verified", "zwid_verified", "Zwift Verified", None),
    ("country", "country", "Country", None),
    ("timezone", "timezone", "Timezone", None),
    ("birth_year", "birth_year", "Birth Year", None),
    ("gender", "gender", "Gender", None),
    ("unit_preference", "unit_preference", "Unit Preference", None),
    ("trainer", "trainer", "Trainer", None),
    ("power_meter", "powermeter", "Power Meter", None),
    ("dual_recording", "dual_recording", "Dual Recording", None),
    ("heartrate_monitor", "heartrate_monitor", "Heart Rate Monitor", None),
    ("strava_profile", "strava_url", "Strava Profile", None),
    ("tpv_profile_url", "tpv_profile_url", "TPV Profile URL", None),
]


def _format_display_value(value: Any, field_name: str) -> str:
    """Format a value for display in the import preview.

    Args:
        value: The value to format.
        field_name: The field name for context.

    Returns:
        Human-readable string representation.

    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if field_name == "country" and value:
        # CountryField returns a Country object with a name attribute
        return str(value.name) if hasattr(value, "name") else str(value)
    return str(value)


def get_importable_fields(application: MembershipApplication) -> dict[str, dict]:
    """Return importable fields from application with their values.

    Args:
        application: The MembershipApplication to extract fields from.

    Returns:
        Dictionary mapping field names to {label, value, display_value, user_field}.

    """
    result = {}

    for app_field, user_field, label, transform in FIELD_MAPPING:
        value = getattr(application, app_field, None)

        # Skip empty values
        if value is None or value == "":
            continue

        # Transform the value if needed
        transformed_value = transform(value) if transform else value

        # Skip if transform returns None
        if transformed_value is None:
            continue

        result[app_field] = {
            "label": label,
            "value": transformed_value,
            "display_value": _format_display_value(value, app_field),
            "user_field": user_field,
        }

    return result


def import_application_to_user(user: User, application: MembershipApplication) -> list[str]:
    """Copy fields from application to user.

    Args:
        user: The User to update.
        application: The MembershipApplication to copy from.

    Returns:
        List of imported field names.

    """
    imported_fields = []
    update_fields = []

    for app_field, user_field, label, transform in FIELD_MAPPING:
        app_value = getattr(application, app_field, None)

        # Skip empty values
        if app_value is None or app_value == "":
            continue

        # Get current user value
        user_value = getattr(user, user_field, None)

        # Skip if user already has a value (don't overwrite)
        if user_value is not None and user_value != "" and user_value is not False:
            # Special handling for boolean fields - False is a valid value
            if isinstance(user_value, bool) and user_value is False:
                pass  # Continue to potentially overwrite
            else:
                continue

        # Transform the value if needed
        final_value = transform(app_value) if transform else app_value

        # Skip if transform returns None
        if final_value is None:
            continue

        # Set the value
        setattr(user, user_field, final_value)
        imported_fields.append(label)
        update_fields.append(user_field)

    if update_fields:
        user.save(update_fields=update_fields)
        logfire.info(
            "Imported application data to user profile",
            user_id=user.id,
            discord_id=user.discord_id,
            application_id=str(application.id),
            imported_fields=imported_fields,
        )

    return imported_fields

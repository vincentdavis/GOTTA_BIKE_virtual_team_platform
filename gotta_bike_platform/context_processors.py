"""Context processors for gotta_bike_platform app."""

from gotta_bike_platform.models import SiteSettings


def site_settings(request):
    """Add site settings to template context.

    Makes the SiteSettings singleton available as 'site_settings' in all templates.

    Args:
        request: The HTTP request.

    Returns:
        Dictionary with site_settings key.

    """
    return {"site_settings": SiteSettings.get_settings()}

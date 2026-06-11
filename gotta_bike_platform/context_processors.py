"""Context processors for gotta_bike_platform app."""

from gotta_bike_platform.models import SiteSettings
from gotta_bike_platform.version import DEPLOY_TIME, DEPLOY_VERSION


def deploy_info(request):
    """Add deploy timestamp and commit SHA to template context.

    Args:
        request: The HTTP request.

    Returns:
        Dictionary with deploy_time (aware datetime) and deploy_version (short SHA).

    """
    return {"deploy_time": DEPLOY_TIME, "deploy_version": DEPLOY_VERSION}


def site_settings(request):
    """Add site settings to template context.

    Makes the SiteSettings singleton available as 'site_settings' in all templates.

    Args:
        request: The HTTP request.

    Returns:
        Dictionary with site_settings key.

    """
    return {"site_settings": SiteSettings.get_settings()}

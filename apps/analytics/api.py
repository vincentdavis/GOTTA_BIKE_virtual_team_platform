"""API endpoints for analytics tracking."""

import logfire
from django.http import HttpRequest
from ninja import NinjaAPI, Schema

from apps.analytics.models import PageVisit

api = NinjaAPI(urls_namespace="analytics", docs_url=None)


class PageVisitSchema(Schema):
    """Schema for page visit tracking data from JavaScript."""

    path: str
    referer: str = ""
    screen_width: int | None = None
    screen_height: int | None = None
    viewport_width: int | None = None
    timezone: str = ""


class SuccessResponse(Schema):
    """Success response schema."""

    success: bool


def get_client_ip(request: HttpRequest) -> str | None:
    """Extract client IP address from request.

    Handles X-Forwarded-For header for proxied requests.

    Args:
        request: The HTTP request.

    Returns:
        IP address string or None.

    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # Take the first IP in the chain (client's real IP)
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def parse_user_agent(user_agent: str) -> dict:
    """Parse user agent string to extract browser, OS, and device info.

    Args:
        user_agent: The user agent string.

    Returns:
        Dictionary with browser, browser_version, os, and device_type.

    """
    result = {
        "browser": "",
        "browser_version": "",
        "os": "",
        "device_type": "desktop",
    }

    if not user_agent:
        return result

    ua_lower = user_agent.lower()

    # Detect device type
    if "mobile" in ua_lower or ("android" in ua_lower and "mobile" in ua_lower):
        result["device_type"] = "mobile"
    elif "tablet" in ua_lower or "ipad" in ua_lower:
        result["device_type"] = "tablet"

    # Detect OS
    if "windows" in ua_lower:
        result["os"] = "Windows"
    elif "macintosh" in ua_lower or "mac os" in ua_lower:
        result["os"] = "macOS"
    elif "linux" in ua_lower:
        result["os"] = "Linux"
    elif "android" in ua_lower:
        result["os"] = "Android"
    elif "iphone" in ua_lower or "ipad" in ua_lower:
        result["os"] = "iOS"

    # Detect browser (order matters - check specific browsers first)
    if "edg/" in ua_lower:
        result["browser"] = "Edge"
    elif "chrome" in ua_lower and "safari" in ua_lower:
        result["browser"] = "Chrome"
    elif "firefox" in ua_lower:
        result["browser"] = "Firefox"
    elif "safari" in ua_lower and "chrome" not in ua_lower:
        result["browser"] = "Safari"
    elif "opera" in ua_lower or "opr/" in ua_lower:
        result["browser"] = "Opera"

    return result


@api.post("/track/", response=SuccessResponse)
def track_page_visit(request: HttpRequest, data: PageVisitSchema) -> dict:
    """Record a page visit.

    Combines client-side data from JavaScript with server-side data
    from the request (user, IP, user agent).

    Args:
        request: The HTTP request.
        data: Page visit data from JavaScript.

    Returns:
        Success response.

    """
    # Skip tracking for static files and common non-page paths
    skip_paths = ["/static/", "/media/", "/favicon.ico", "/robots.txt", "/api/analytics/"]
    if any(data.path.startswith(p) for p in skip_paths):
        return {"success": True}

    user_agent = request.META.get("HTTP_USER_AGENT", "")
    ua_parsed = parse_user_agent(user_agent)

    try:
        PageVisit.objects.create(
            # Server-side data
            user=request.user if request.user.is_authenticated else None,
            ip_address=get_client_ip(request),
            user_agent=user_agent,
            # Page data
            path=data.path[:500],  # Truncate to field max length
            referer=data.referer[:1000] if data.referer else "",
            # Client-side data
            screen_width=data.screen_width,
            screen_height=data.screen_height,
            viewport_width=data.viewport_width,
            timezone=data.timezone[:50] if data.timezone else "",
            # Parsed user agent
            browser=ua_parsed["browser"],
            browser_version=ua_parsed["browser_version"],
            os=ua_parsed["os"],
            device_type=ua_parsed["device_type"],
        )
    except Exception as e:
        logfire.error("Failed to record page visit", error=str(e), path=data.path)

    return {"success": True}

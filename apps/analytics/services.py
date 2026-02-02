"""Services for analytics dashboard."""

from datetime import datetime, timedelta
from typing import Any

from django.db.models import Count
from django.utils import timezone

from apps.analytics.models import PageVisit


def get_date_range_for_period(period: str) -> tuple[datetime, datetime]:
    """Calculate date range for the given period.

    Args:
        period: One of 'day', 'week', 'month', 'year'.

    Returns:
        Tuple of (start_datetime, end_datetime).

    """
    now = timezone.now()
    end_date = now

    if period == "day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = now - timedelta(days=7)
    elif period == "month":
        start_date = now - timedelta(days=30)
    elif period == "year":
        start_date = now - timedelta(days=365)
    else:
        # Default to week
        start_date = now - timedelta(days=7)

    return start_date, end_date


def get_analytics_data(start_date: datetime, end_date: datetime) -> dict[str, Any]:
    """Get all analytics data for the given date range.

    Args:
        start_date: Start of the date range.
        end_date: End of the date range.

    Returns:
        Dictionary containing all analytics metrics.

    """
    visits = PageVisit.objects.filter(timestamp__gte=start_date, timestamp__lte=end_date)

    # Total page views
    total_views = visits.count()

    # Unique visitors (distinct IP addresses)
    unique_visitors = visits.exclude(ip_address__isnull=True).values("ip_address").distinct().count()

    # Logged in percentage
    logged_in_count = visits.filter(user__isnull=False).count()
    logged_in_percent = (logged_in_count / total_views * 100) if total_views > 0 else 0

    # Device breakdown
    device_breakdown = get_device_breakdown(visits)

    # Top pages
    top_pages = get_top_pages(visits, total_views)

    # Browser breakdown
    browsers = get_browser_breakdown(visits)

    # Operating system breakdown
    operating_systems = get_os_breakdown(visits)

    # Timezone breakdown
    timezones = get_timezone_breakdown(visits)

    return {
        "total_views": total_views,
        "unique_visitors": unique_visitors,
        "logged_in_percent": round(logged_in_percent, 1),
        "logged_in_count": logged_in_count,
        "anonymous_count": total_views - logged_in_count,
        "device_breakdown": device_breakdown,
        "top_pages": top_pages,
        "browsers": browsers,
        "operating_systems": operating_systems,
        "timezones": timezones,
    }


def get_device_breakdown(visits) -> dict[str, Any]:
    """Get device type breakdown.

    Args:
        visits: QuerySet of PageVisit objects.

    Returns:
        Dictionary with device counts and percentages.

    """
    total = visits.count()
    if total == 0:
        return {"desktop": 0, "mobile": 0, "tablet": 0, "unknown": 0, "percentages": {}}

    device_counts = visits.exclude(device_type="").values("device_type").annotate(count=Count("id")).order_by("-count")

    breakdown = {"desktop": 0, "mobile": 0, "tablet": 0, "unknown": 0}
    for item in device_counts:
        device = item["device_type"].lower() if item["device_type"] else "unknown"
        if device in breakdown:
            breakdown[device] = item["count"]
        else:
            breakdown["unknown"] += item["count"]

    # Add visits without device_type to unknown
    unknown_count = visits.filter(device_type="").count()
    breakdown["unknown"] += unknown_count

    # Calculate percentages
    percentages = {device: round((count / total * 100), 1) if total > 0 else 0 for device, count in breakdown.items()}

    return {**breakdown, "percentages": percentages, "total": total}


def get_top_pages(visits, total_views: int, limit: int = 10) -> list[dict[str, Any]]:
    """Get top visited pages.

    Args:
        visits: QuerySet of PageVisit objects.
        total_views: Total number of views for percentage calculation.
        limit: Maximum number of pages to return.

    Returns:
        List of dictionaries with path, count, and percentage.

    """
    pages = visits.values("path").annotate(count=Count("id")).order_by("-count")[:limit]

    return [
        {
            "path": page["path"],
            "count": page["count"],
            "percent": round((page["count"] / total_views * 100), 1) if total_views > 0 else 0,
        }
        for page in pages
    ]


def get_browser_breakdown(visits, limit: int = 10) -> list[dict[str, Any]]:
    """Get browser breakdown.

    Args:
        visits: QuerySet of PageVisit objects.
        limit: Maximum number of browsers to return.

    Returns:
        List of dictionaries with browser name, count, and percentage.

    """
    total = visits.exclude(browser="").count()
    if total == 0:
        return []

    browsers = visits.exclude(browser="").values("browser").annotate(count=Count("id")).order_by("-count")[:limit]

    return [
        {
            "name": browser["browser"] or "Unknown",
            "count": browser["count"],
            "percent": round((browser["count"] / total * 100), 1) if total > 0 else 0,
        }
        for browser in browsers
    ]


def get_os_breakdown(visits, limit: int = 10) -> list[dict[str, Any]]:
    """Get operating system breakdown.

    Args:
        visits: QuerySet of PageVisit objects.
        limit: Maximum number of OSes to return.

    Returns:
        List of dictionaries with OS name, count, and percentage.

    """
    total = visits.exclude(os="").count()
    if total == 0:
        return []

    operating_systems = visits.exclude(os="").values("os").annotate(count=Count("id")).order_by("-count")[:limit]

    return [
        {
            "name": os["os"] or "Unknown",
            "count": os["count"],
            "percent": round((os["count"] / total * 100), 1) if total > 0 else 0,
        }
        for os in operating_systems
    ]


def get_timezone_breakdown(visits, limit: int = 10) -> list[dict[str, Any]]:
    """Get timezone breakdown.

    Args:
        visits: QuerySet of PageVisit objects.
        limit: Maximum number of timezones to return.

    Returns:
        List of dictionaries with timezone name, count, and percentage.

    """
    total = visits.exclude(timezone="").count()
    if total == 0:
        return []

    timezones = visits.exclude(timezone="").values("timezone").annotate(count=Count("id")).order_by("-count")[:limit]

    return [
        {
            "name": tz["timezone"] or "Unknown",
            "count": tz["count"],
            "percent": round((tz["count"] / total * 100), 1) if total > 0 else 0,
        }
        for tz in timezones
    ]

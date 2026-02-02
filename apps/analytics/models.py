"""Models for analytics app."""

from typing import ClassVar

from django.conf import settings
from django.db import models


class PageVisit(models.Model):
    """Tracks individual page visits.

    Combines server-side data (user, IP, user agent) with client-side data
    (screen size, viewport, timezone) sent via JavaScript.
    """

    # Server-side data (reliable, can't be spoofed)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Authenticated user (if logged in)",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True, help_text="Client IP address")
    user_agent = models.TextField(blank=True, help_text="Browser user agent string")

    # Page data
    path = models.CharField(max_length=500, help_text="URL path visited")
    referer = models.URLField(max_length=1000, blank=True, help_text="Referring URL")

    # Client-side data (from JavaScript)
    screen_width = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Screen width in pixels")
    screen_height = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Screen height in pixels")
    viewport_width = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Viewport width in pixels")
    timezone = models.CharField(max_length=50, blank=True, help_text="Client timezone (e.g., America/New_York)")

    # Parsed user agent fields (optional, for easier querying)
    browser = models.CharField(max_length=50, blank=True, help_text="Browser name")
    browser_version = models.CharField(max_length=20, blank=True, help_text="Browser version")
    os = models.CharField(max_length=50, blank=True, help_text="Operating system")
    device_type = models.CharField(max_length=20, blank=True, help_text="Device type (mobile/tablet/desktop)")

    # Timestamp
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True, help_text="When the visit occurred")

    class Meta:
        """Meta options for PageVisit model."""

        verbose_name = "Page Visit"
        verbose_name_plural = "Page Visits"
        ordering: ClassVar[list[str]] = ["-timestamp"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["path", "timestamp"]),
            models.Index(fields=["user", "timestamp"]),
        ]

    def __str__(self) -> str:
        """Return string representation.

        Returns:
            String with path and timestamp.

        """
        return f"{self.path} at {self.timestamp}"

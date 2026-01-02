"""Magic link models for passwordless authentication."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class MagicLink(models.Model):
    """Token-based passwordless authentication link.

    Links expire in 300 seconds (5 minutes) and are single-use.
    """

    EXPIRY_SECONDS = 300

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="magic_links",
        help_text="User this magic link authenticates",
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique token for URL",
    )
    redirect_url = models.CharField(
        max_length=500,
        default="/",
        help_text="URL to redirect after successful authentication",
    )
    used = models.BooleanField(
        default=False,
        help_text="Whether this link has been used",
    )
    date_created = models.DateTimeField(auto_now_add=True)
    date_expires = models.DateTimeField(
        help_text="When this link expires",
    )

    class Meta:
        """Meta options for MagicLink."""

        verbose_name = "Magic Link"
        verbose_name_plural = "Magic Links"
        ordering: ClassVar[list[str]] = ["-date_created"]

    def __str__(self) -> str:
        """Return string representation.

        Returns:
            String with user and expiry info.

        """
        return f"MagicLink for {self.user} (expires {self.date_expires})"

    def save(self, *args, **kwargs) -> None:
        """Generate token and expiry on first save.

        Args:
            *args: Positional arguments passed to parent save.
            **kwargs: Keyword arguments passed to parent save.

        """
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        if not self.date_expires:
            self.date_expires = timezone.now() + timedelta(seconds=self.EXPIRY_SECONDS)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        """Return the full magic link URL path.

        Returns:
            URL path for this magic link.

        """
        return reverse("magic_links:validate", kwargs={"token": self.token})

    def is_valid(self) -> bool:
        """Check if the link is valid (not used and not expired).

        Returns:
            True if link can still be used, False otherwise.

        """
        if self.used:
            return False
        return timezone.now() <= self.date_expires

    def consume(self) -> bool:
        """Mark the link as used.

        Returns:
            True if successfully consumed, False if already invalid.

        """
        if not self.is_valid():
            return False
        self.used = True
        self.save(update_fields=["used"])
        return True

    @classmethod
    def create_for_user(cls, user, redirect_url: str = "/") -> MagicLink:
        """Create a new magic link for the given user.

        Args:
            user: The User to create a link for.
            redirect_url: URL to redirect after authentication.

        Returns:
            The created MagicLink instance.

        """
        return cls.objects.create(user=user, redirect_url=redirect_url)

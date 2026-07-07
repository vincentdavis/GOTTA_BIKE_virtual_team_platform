"""Models for per-user API keys."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class UserApiKey(models.Model):
    """A per-user API key with a 30-day default lifetime.

    Only the SHA-256 hash of the key is stored. The raw key is shown to the user
    exactly once at issuance and never again.

    Attributes:
        user: Owner of the key.
        name: User-chosen label, e.g. "ZR Sync Script".
        key_hash: SHA-256 hex digest of the raw key (used for lookup).
        prefix: First 8 chars of the raw key (display only).
        last4: Last 4 chars of the raw key (display only).
        created_at: When the key was issued.
        expires_at: When the key expires (issuance + 30 days by default).
        revoked_at: When the user revoked the key (None if still active).
        last_used_at: Last time the key authenticated a request (best-effort).

    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
        help_text="Owner of the API key",
    )
    name = models.CharField(
        max_length=80,
        help_text="User-chosen label for the key",
    )
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hex digest of the raw key",
    )
    prefix = models.CharField(
        max_length=8,
        help_text="Display-only prefix of the raw key",
    )
    last4 = models.CharField(
        max_length=4,
        help_text="Display-only last 4 chars of the raw key",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text="Set to created_at + 30 days at issuance",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Meta options for UserApiKey."""

        ordering = ["-created_at"]  # noqa: RUF012
        verbose_name = "User API Key"
        verbose_name_plural = "User API Keys"

    def __str__(self) -> str:
        """Return a short display string.

        Returns:
            ``{user} — {name} ({prefix}…{last4})``.

        """
        return f"{self.user} — {self.name} ({self.prefix}…{self.last4})"

    @property
    def is_active(self) -> bool:
        """Whether the key has not been revoked and has not expired.

        Returns:
            True when revoked_at is None and expires_at is in the future.

        """
        return self.revoked_at is None and self.expires_at > timezone.now()

    @property
    def is_revoked(self) -> bool:
        """Whether the key has been revoked."""
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        """Whether the key has expired."""
        return self.expires_at <= timezone.now()

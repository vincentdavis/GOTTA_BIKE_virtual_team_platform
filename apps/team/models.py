"""Models for team app."""

import uuid
from datetime import datetime, timedelta
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class RaceReadyRecord(models.Model):
    """Track race ready verification records for team members.

    Race ready records are used to verify rider measurements (weight, height, power)
    for competitive racing compliance. Each record includes evidence (file upload or URL)
    and the measured value, and goes through a review process by team captains.

    Verification Types:
        - weight_full: Full weight verification (valid for WEIGHT_FULL_DAYS)
        - weight_light: Light weight verification (valid for WEIGHT_LIGHT_DAYS)
        - height: Height verification (valid for HEIGHT_VERIFICATION_DAYS, 0=forever)
        - power: FTP/power verification (valid for POWER_VERIFICATION_DAYS)

    Workflow:
        1. User submits record with verify_type, measurement value, and evidence
        2. Record is created with status=PENDING
        3. Team captain reviews and verifies or rejects
        4. On verification, media_file is deleted for privacy
        5. Verification expires after validity period (configurable in constance)

    Attributes:
        user: The team member this record belongs to.
        verify_type: Type of verification (weight_full, weight_light, height, power).
        media_type: Type of evidence media (video, photo, link, other).
        media_file: Uploaded photo/video file (deleted on verification).
        url: External URL to evidence (YouTube, Vimeo, image link).
        weight: Weight in kg (required for weight verifications).
        height: Height in cm (required for height verification).
        ftp: Functional Threshold Power in watts (required for power verification).
        status: Review status (pending, verified, rejected).
        reviewed_by: Team captain who reviewed the record.
        reviewed_date: When the record was reviewed.
        rejection_reason: Explanation if rejected.
        notes: Optional notes from the user about the measurement.
        date_created: When the record was submitted.

    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="race_ready_records",
        help_text="User this record belongs to",
    )
    verify_type = models.CharField(
        max_length=15,
        choices=[
            ("weight_full", "Weight Full"),
            ("weight_light", "Weight Light"),
            ("height", "Height"),
            ("power", "Power"),
        ],
        help_text="Type of verification",
    )
    media_type = models.CharField(
        max_length=10,
        choices=[
            ("video", "Video"),
            ("photo", "Photo"),
            ("link", "Link"),
            ("other", "Other"),
        ],
        help_text="Type of media",
    )
    media_file = models.FileField(
        upload_to="race_ready/%Y/%m/",
        null=True,
        blank=True,
        help_text="Uploaded photo or video file",
    )
    url = models.URLField(
        max_length=500,
        blank=True,
        help_text="URL to external evidence (e.g., YouTube video, image link)",
    )
    weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Weight in kg (for weight verification)",
    )
    height = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Height in cm (for height verification)",
    )
    ftp = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Functional Threshold Power in watts (for power verification)",
    )

    class Status(models.TextChoices):
        """Status choices for verification records."""

        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Verification status",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_records",
        help_text="User who reviewed this record",
    )
    reviewed_date = models.DateTimeField(null=True, blank=True, help_text="When this record was reviewed")
    rejection_reason = models.TextField(blank=True, help_text="Reason for rejection (if rejected)")
    notes = models.TextField(blank=True, help_text="Notes about the measurement")
    same_gender = models.BooleanField(
        default=False,
        help_text="Same gender as user should review record",
    )
    date_created = models.DateTimeField(
        default=timezone.now,
        help_text="When this record was created",
    )

    class Meta:
        """Meta options for RaceReadyRecord model."""

        verbose_name = "Race Ready Record"
        verbose_name_plural = "Race Ready Records"
        ordering: ClassVar[list[str]] = ["-date_created"]

    def __str__(self) -> str:
        """Return string representation of record.

        Returns:
            Description with user, type, and date.

        """
        return f"{self.user.username} - {self.verify_type} ({self.date_created:%Y-%m-%d})"

    def clean(self) -> None:
        """Validate that at least one of media_file or url is provided.

        Raises:
            ValidationError: If neither media_file nor url is provided.

        """
        super().clean()
        if not self.media_file and not self.url:
            raise ValidationError("You must provide either a file upload or a URL (or both).")

    def delete_media_file(self) -> bool:
        """Delete the uploaded media file if it exists.

        Returns:
            True if file was deleted, False otherwise.

        """
        if self.media_file:
            self.media_file.delete(save=False)
            return True
        return False

    @property
    def is_verified(self) -> bool:
        """Check if record is verified."""
        return self.status == self.Status.VERIFIED

    @property
    def is_rejected(self) -> bool:
        """Check if record is rejected."""
        return self.status == self.Status.REJECTED

    @property
    def is_pending(self) -> bool:
        """Check if record is pending."""
        return self.status == self.Status.PENDING

    @property
    def url_type(self) -> str:
        """Determine the type of URL for display purposes.

        Returns:
            One of: 'youtube', 'vimeo', 'image', or 'other'.

        """
        if not self.url:
            return "other"
        url_lower = self.url.lower()
        if "youtube.com/watch" in url_lower or "youtu.be/" in url_lower:
            return "youtube"
        if "vimeo.com/" in url_lower:
            return "vimeo"
        if url_lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            return "image"
        return "other"

    @property
    def embed_url(self) -> str:
        """Get the embed URL for YouTube or Vimeo videos.

        Returns:
            Embed URL if applicable, otherwise empty string.

        """
        import re

        if not self.url:
            return ""

        # YouTube: youtube.com/watch?v=VIDEO_ID or youtu.be/VIDEO_ID
        # Also handle youtube.com/shorts/VIDEO_ID
        youtube_match = re.search(
            r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})", self.url
        )
        if youtube_match:
            video_id = youtube_match.group(1)
            # Use youtube-nocookie.com for better privacy/compatibility
            # rel=0 prevents showing related videos at the end
            return f"https://www.youtube-nocookie.com/embed/{video_id}?rel=0"

        # Vimeo: vimeo.com/VIDEO_ID
        vimeo_match = re.search(r"vimeo\.com/(\d+)", self.url)
        if vimeo_match:
            return f"https://player.vimeo.com/video/{vimeo_match.group(1)}"

        return ""

    @property
    def validity_days(self) -> int:
        """Get the validity period in days for this verification type.

        Returns:
            Number of days the verification is valid (0 = forever).

        """
        from constance import config

        validity_map = {
            "weight_full": config.WEIGHT_FULL_DAYS,
            "weight_light": config.WEIGHT_LIGHT_DAYS,
            "height": config.HEIGHT_VERIFICATION_DAYS,
            "power": config.POWER_VERIFICATION_DAYS,
        }
        return validity_map.get(self.verify_type, 0)

    @property
    def expires_date(self) -> datetime | None:
        """Get the expiration date for this verification.

        Returns:
            Expiration datetime if applicable, None if never expires or not verified.

        """
        if not self.is_verified or not self.reviewed_date:
            return None

        validity = self.validity_days
        if validity == 0:
            return None  # Never expires

        return self.reviewed_date + timedelta(days=validity)

    @property
    def is_expired(self) -> bool:
        """Check if verification has expired.

        Returns:
            True if expired, False otherwise.

        """
        expires = self.expires_date
        if expires is None:
            return False
        return timezone.now() > expires

    @property
    def days_remaining(self) -> int | None:
        """Get the number of days remaining until expiration.

        Returns:
            Days remaining (negative if expired), None if never expires or not verified.

        """
        expires = self.expires_date
        if expires is None:
            return None
        delta = expires - timezone.now()
        return delta.days

    @property
    def validity_status(self) -> str:
        """Get a human-readable validity status.

        Returns:
            Status string: 'Valid (X days)', 'Expired', 'Never expires', or 'Not verified'.

        """
        if not self.is_verified:
            return "Not verified"

        days = self.days_remaining
        if days is None:
            return "Never expires"
        if days < 0:
            return "Expired"
        return f"Valid ({days} days)"


class TeamLink(models.Model):
    """External links to team resources, forms, and event information.

    TeamLinks provide a centralized way to share important URLs with team members.
    Links can be categorized by type, scheduled to appear/disappear at specific times,
    and filtered by users on the team links page.

    Visibility Rules:
        A link is visible when ALL conditions are met:
        - active=True
        - date_open is None OR date_open <= now
        - date_closed is None OR date_closed > now

    Use Cases:
        - Race signup forms (with open/close dates matching registration windows)
        - Event-specific spreadsheets (availability, results)
        - Permanent resources (team rules, ZwiftPower page)
        - Time-limited announcements or forms

    Attributes:
        title: Display name for the link.
        description: Optional longer description of the resource.
        url: The destination URL.
        link_types: List of LinkType values for categorization/filtering.
        active: Master toggle to hide link regardless of dates.
        date_open: When link becomes visible (None = immediately).
        date_closed: When link stops being visible (None = never).
        date_added: Auto-set timestamp when created.
        date_edited: Auto-updated timestamp on save.

    Properties:
        is_visible: Whether link should be shown based on active and dates.
        link_types_display: Comma-separated display names of link types.

    """

    class LinkType(models.TextChoices):
        """Types of external links."""

        AVAILABILITY = "availability", "Availability"
        EVENT = "event", "Event"
        FORM = "form", "Form"
        FRR = "frr", "FRR"
        SIGNUP = "signup", "Signup"
        SPREADSHEET = "spreadsheet", "Spreadsheet"
        TTT = "ttt", "TTT"
        WEBSITE = "website", "Website"
        CLUBLADDER = "club_ladder", "Club Ladder"
        ZRL = "zrl", "ZRL"
        ZWIFTPOWER = "zwiftpower", "ZwiftPower"
        ZWIFTRACING = "zwiftracing", "Zwift Racing"
        OTHER = "other", "Other"

    title = models.CharField(max_length=255, help_text="Title of the link")
    description = models.TextField(blank=True, help_text="Description of the link")
    url = models.URLField(max_length=500, help_text="URL to the resource")
    link_types = models.JSONField(
        default=list,
        blank=True,
        help_text="Types/tags for this link (e.g., ['form', 'signup', 'zrl'])",
    )
    active = models.BooleanField(default=True, help_text="Whether this link is active")
    date_open = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this link becomes visible (blank = immediately)",
    )
    date_closed = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this link stops being visible (blank = never)",
    )
    date_added = models.DateTimeField(auto_now_add=True, help_text="When this link was added")
    date_edited = models.DateTimeField(auto_now=True, help_text="When this link was last edited")

    class Meta:
        """Meta options for TeamLink model."""

        verbose_name = "Team Link"
        verbose_name_plural = "Team Links"
        ordering: ClassVar[list[str]] = ["title"]

    def __str__(self) -> str:
        """Return string representation of link.

        Returns:
            The title of the link.

        """
        return self.title

    @property
    def is_visible(self) -> bool:
        """Check if link is currently visible based on date_open and date_closed.

        Returns:
            True if link should be visible now.

        """
        if not self.active:
            return False
        now = timezone.now()
        if self.date_open and now < self.date_open:
            return False
        return not (self.date_closed and now >= self.date_closed)

    @property
    def link_types_display(self) -> str:
        """Return comma-separated display names of link types.

        Returns:
            Formatted string of link type labels.

        """
        if not self.link_types:
            return ""
        type_map = dict(self.LinkType.choices)
        return ", ".join(type_map.get(t, t) for t in self.link_types)


class DiscordRole(models.Model):
    """Discord guild roles synced from the server."""

    role_id = models.CharField(
        max_length=20,
        unique=True,
        help_text="Discord role ID (snowflake)",
    )
    name = models.CharField(
        max_length=100,
        help_text="Role name",
    )
    color = models.IntegerField(
        default=0,
        help_text="Role color as integer",
    )
    position = models.IntegerField(
        default=0,
        help_text="Role position in hierarchy (higher = more authority)",
    )
    managed = models.BooleanField(
        default=False,
        help_text="Whether this role is managed by an integration/bot",
    )
    mentionable = models.BooleanField(
        default=False,
        help_text="Whether this role can be mentioned",
    )
    date_synced = models.DateTimeField(
        auto_now=True,
        help_text="When this role was last synced from Discord",
    )

    class Meta:
        """Meta options for DiscordRole model."""

        verbose_name = "Discord Role"
        verbose_name_plural = "Discord Roles"
        ordering: ClassVar[list[str]] = ["-position"]

    def __str__(self) -> str:
        """Return string representation of role.

        Returns:
            The role name.

        """
        return self.name

    @property
    def color_hex(self) -> str:
        """Return the role color as a hex string.

        Returns:
            Hex color string (e.g., '#3498db') or empty if no color.

        """
        if self.color == 0:
            return ""
        return f"#{self.color:06x}"


class RosterFilter(models.Model):
    """Temporary filtered roster view created from Discord channel members.

    Created via the Discord bot /in_channel command to generate a link
    showing only team members who have access to a specific Discord channel.

    Attributes:
        id: UUID primary key for URL-safe access.
        discord_ids: List of Discord user IDs to filter by.
        channel_name: Name of the Discord channel (for display).
        created_by_discord_id: Discord ID of user who created the filter.
        created_at: When the filter was created.
        expires_at: When the filter expires (default: 5 minutes after creation).

    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="UUID for URL-safe access",
    )
    discord_ids = models.JSONField(
        default=list,
        help_text="List of Discord user IDs to filter roster by",
    )
    channel_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Name of the Discord channel",
    )
    created_by_discord_id = models.CharField(
        max_length=30,
        blank=True,
        help_text="Discord ID of user who created this filter",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this filter was created",
    )
    expires_at = models.DateTimeField(
        help_text="When this filter expires",
    )

    class Meta:
        """Meta options for RosterFilter model."""

        verbose_name = "Roster Filter"
        verbose_name_plural = "Roster Filters"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        """Return string representation of filter.

        Returns:
            Description with channel name and creation date.

        """
        return f"Filter for #{self.channel_name} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def is_expired(self) -> bool:
        """Check if this filter has expired.

        Returns:
            True if expired, False otherwise.

        """
        return timezone.now() > self.expires_at

    @property
    def member_count(self) -> int:
        """Get the number of Discord IDs in this filter.

        Returns:
            Count of Discord IDs.

        """
        return len(self.discord_ids) if self.discord_ids else 0

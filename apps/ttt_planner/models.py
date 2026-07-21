"""Models for the TTT planner: power-ups, plans, and per-plan riders.

Routes and segments are the canonical ``apps.zwift_data`` models — the planner's
``TttPlan.route`` FKs ``zwift_data.ZwiftRoute`` directly.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils.text import slugify

from apps.ttt_planner import terrain


class PowerUp(models.Model):
    """A Zwift PowerUp, shown on the routes reference page.

    Shared reference data that race-verified members can curate. The
    ``excluded_from_ladder`` flag marks PowerUps that do not count for the Club
    Ladder (the XP bonuses and Boost).
    """

    name = models.CharField(max_length=100, unique=True, help_text="PowerUp name (e.g. Feather)")
    aka = models.CharField(max_length=100, blank=True, help_text="Alternate name (e.g. Lightweight)")
    slug = models.SlugField(max_length=120, unique=True, blank=True, help_text="URL/icon slug (auto from name)")
    effect = models.TextField(blank=True, help_text="What the PowerUp does, including duration")
    duration_seconds = models.PositiveIntegerField(default=0, help_text="Effect duration in seconds (0 = instant/none)")
    event_only = models.BooleanField(default=False, help_text="Only available in events")
    excluded_from_ladder = models.BooleanField(default=False, help_text="Does not count for the Club Ladder")
    icon = models.ImageField(upload_to="powerup_icons/", blank=True, help_text="PowerUp icon image")
    discord_emoji = models.CharField(
        max_length=100,
        blank=True,
        help_text="Discord custom-emoji code to show in race threads, e.g. <:feather:123456789012345678>",
    )
    order = models.PositiveSmallIntegerField(default=0, help_text="Display order on the routes page")
    is_active = models.BooleanField(default=True, help_text="Show in the PowerUps list")

    class Meta:
        """Meta options for PowerUp."""

        verbose_name = "PowerUp"
        verbose_name_plural = "PowerUps"
        ordering: ClassVar[list[str]] = ["order", "name"]

    def __str__(self) -> str:
        """Return the PowerUp name.

        Returns:
            The PowerUp name.

        """
        return self.name

    def save(self, *args: object, **kwargs: object) -> None:
        """Populate ``slug`` from ``name`` when missing, then save.

        Args:
            *args: Positional args passed through to ``Model.save``.
            **kwargs: Keyword args passed through to ``Model.save``.

        """
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class TttPlan(models.Model):
    """A saved TTT plan. The UUID primary key doubles as the share token."""

    class EventType(models.TextChoices):
        """The kind of event this plan targets."""

        ZRL = "zrl", "ZRL"
        DRS = "drs", "DRS"
        WTRL_TTT = "wtrl_ttt", "WTRL TTT"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, blank=True, help_text="Plan name")
    team_name = models.CharField(max_length=200, blank=True, help_text="Team name shown on the plan")
    event_type = models.CharField(
        max_length=20, blank=True, default="", choices=EventType.choices, help_text="Event this plan targets"
    )
    route = models.ForeignKey(
        "zwift_data.ZwiftRoute",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ttt_plans",
        help_text="Selected route",
    )
    course_name = models.CharField(max_length=200, blank=True, help_text="Course / route name (free text)")
    course_type = models.CharField(
        max_length=20,
        blank=True,
        choices=terrain.TERRAIN_CHOICES,
        help_text="Terrain type (informational; prefilled from the route)",
    )
    target_speed_kph = models.DecimalField(
        max_digits=5, decimal_places=1, default=40.0, help_text="Target flat speed in km/h"
    )
    target_if = models.FloatField(
        default=0.95, help_text="Target intensity factor used by Calculate / Auto-balance (e.g. 0.95)"
    )
    draft_savings = models.JSONField(
        default=list,
        blank=True,
        help_text="Per-plan aero draft savings fractions by wheel position, e.g. [0.0, 0.233, 0.30]. "
        "Index 0 is the front rider (no draft). Empty list uses the global TTT_DRAFT_SAVINGS default.",
    )
    cda_coef = models.FloatField(
        null=True,
        blank=True,
        help_text="Per-plan aero CdA coefficient. Blank uses the global TTT_CDA_COEF default.",
    )

    class GopherStatus(models.TextChoices):
        """Status of the most recent zwiftgopher optimize run."""

        NONE = "", "Not run"
        PENDING = "pending", "Running"
        DONE = "done", "Done"
        ERROR = "error", "Error"

    zwiftgopher_status = models.CharField(
        max_length=10, blank=True, default="", choices=GopherStatus.choices, help_text="zwiftgopher run status"
    )
    zwiftgopher_result = models.JSONField(
        null=True, blank=True, help_text="Normalized result from the last zwiftgopher optimize run"
    )
    zwiftgopher_request = models.JSONField(
        null=True, blank=True, help_text="Raw request body sent to the zwiftgopher optimize API"
    )
    zwiftgopher_raw_response = models.JSONField(
        null=True, blank=True, help_text="Raw JSON response from the zwiftgopher optimize API"
    )
    zwiftgopher_error = models.CharField(max_length=300, blank=True, help_text="Error from the last zwiftgopher run")
    zwiftgopher_fetched_at = models.DateTimeField(
        null=True, blank=True, help_text="When the last zwiftgopher result was fetched"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ttt_plans",
        help_text="User who created the plan",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for TttPlan."""

        verbose_name = "TTT Plan"
        verbose_name_plural = "TTT Plans"
        ordering: ClassVar[list[str]] = ["-updated_at"]

    def __str__(self) -> str:
        """Return the plan name or a fallback.

        Returns:
            Human-readable plan label.

        """
        return self.name or f"TTT Plan {self.pk}"


class PlanRider(models.Model):
    """A rider on a plan, in pull order.

    Weight/height/FTP are snapshotted at add time so later changes to team data
    do not mutate a saved plan.
    """

    plan = models.ForeignKey(TttPlan, on_delete=models.CASCADE, related_name="riders")
    order = models.PositiveIntegerField(default=0, help_text="Pull order (0-based)")
    zwid = models.PositiveIntegerField(null=True, blank=True, help_text="Zwift ID, if linked to team data")
    name = models.CharField(max_length=200, help_text="Rider display name")
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="Weight in kg")
    height_cm = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Height in cm")
    ftp_w = models.PositiveSmallIntegerField(null=True, blank=True, help_text="FTP in watts")
    pull_power_w = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Override pull power in watts")
    pull_duration_s = models.PositiveIntegerField(default=60, help_text="Pull duration in seconds")
    zero_pull = models.BooleanField(default=False, help_text="Rider takes no pulls (sits in for recovery)")

    class Meta:
        """Meta options for PlanRider."""

        verbose_name = "Plan Rider"
        verbose_name_plural = "Plan Riders"
        ordering: ClassVar[list[str]] = ["order", "id"]

    def __str__(self) -> str:
        """Return the rider name.

        Returns:
            Rider display name.

        """
        return self.name

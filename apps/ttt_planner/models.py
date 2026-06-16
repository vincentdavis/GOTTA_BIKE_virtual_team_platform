"""Models for the TTT planner: routes, plans, and per-plan riders."""

from __future__ import annotations

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class Route(models.Model):
    """A Zwift route a TTT can be planned on.

    Seeded with popular WTRL TTT routes; admins can add more in Django admin.
    """

    name = models.CharField(max_length=200, help_text="Route name")
    world = models.CharField(max_length=100, blank=True, help_text="Zwift world (e.g. Watopia)")
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, help_text="Route distance in km")
    elevation_m = models.PositiveIntegerField(default=0, help_text="Total elevation gain in metres")
    zwift_route_id = models.CharField(max_length=50, blank=True, help_text="Zwift route identifier, if known")
    is_active = models.BooleanField(default=True, help_text="Show in the route picker")

    class Meta:
        """Meta options for Route."""

        verbose_name = "TTT Route"
        verbose_name_plural = "TTT Routes"
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        """Return the route name with distance.

        Returns:
            Human-readable route label.

        """
        return f"{self.name} ({self.distance_km} km)"


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
        Route, on_delete=models.SET_NULL, null=True, blank=True, related_name="plans", help_text="Selected route"
    )
    target_speed_kph = models.DecimalField(
        max_digits=5, decimal_places=1, default=40.0, help_text="Target flat speed in km/h"
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

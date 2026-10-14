"""Models for the TTT planner: power-ups, plans, and per-plan riders.

Routes and segments are the canonical ``apps.zwift_data`` models — the planner's
``TttPlan.route`` FKs ``zwift_data.ZwiftRoute`` directly.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.text import slugify

from apps.ttt_planner import terrain
from gotta_bike_platform.retention import RetentionPolicy


class PowerUp(models.Model):
    """A Zwift PowerUp, shown on the routes reference page.

    Shared reference data that race-verified members can curate. The
    ``excluded_from_ladder`` flag marks PowerUps that do not count for the Club
    Ladder (the XP bonuses and Boost).
    """

    retention = RetentionPolicy.keep(
        "Locally curated reference list shown on the routes page. No personal content."
    )

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

    class SpeedGoal(models.TextChoices):
        """How fast Optimize makes the plan (the first half of its goal)."""

        FASTEST = "fastest", "As fast as the limits allow"
        HOLD = "hold", "Hold the target speed"

    class PullRule(models.TextChoices):
        """How Optimize sets the pull lengths (the second half of its goal)."""

        EVEN = "even", "Even effort"
        RESERVE = "reserve", "Most W′ in reserve"
        KEEP = "keep", "Keep my pull lengths"

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
    speed_goal = models.CharField(
        max_length=10,
        choices=SpeedGoal.choices,
        default=SpeedGoal.FASTEST,
        help_text="What Optimize does to the speed: the fastest the limits allow, or hold the target speed",
    )
    pull_rule = models.CharField(
        max_length=10,
        choices=PullRule.choices,
        default=PullRule.EVEN,
        help_text="How Optimize sets the pull lengths: for even effort, for the most W′ in reserve, or not at all",
    )
    target_if = models.FloatField(
        default=0.95,
        help_text="Target intensity factor (NP / FTP) that Optimize holds every rider to (e.g. 0.95)",
    )
    w_prime_reserve_pct = models.PositiveSmallIntegerField(
        default=25,
        validators=[MaxValueValidator(90)],
        help_text="Share of W′ (%) every rider keeps in hand through the race; Optimize holds to it",
    )
    min_pull_s = models.PositiveSmallIntegerField(default=30, help_text="Shortest pull Optimize gives, in seconds")
    max_pull_s = models.PositiveSmallIntegerField(default=180, help_text="Longest pull Optimize gives, in seconds")
    pull_step_s = models.PositiveSmallIntegerField(
        default=15, help_text="Optimize's pull lengths are multiples of this, in seconds (5, 10, 15 or 30)"
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
    edit_squad = models.ForeignKey(
        "events.Squad",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=(
            "Optional squad whose members (incl. captains/vice-captains) edit this plan as its creator does; "
            "any other team member is asked to confirm first (gotta_bike_platform/planner_access.py)"
        ),
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Who last changed the plan",
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

    Weight/height/FTP, and CP/W′ from Zwift Racing, are snapshotted at add time so later
    changes to team data do not mutate a saved plan.
    """

    # Ceilings for manually entered values. Optimize never gives more than the plan's
    # longest pull, which is itself capped at the duration ceiling. Both
    # are enforced three ways: as field validators (the admin inline, which is where
    # Pull W is actually set, runs full_clean), as a clamp in the inline edit view
    # (which does not), and as the number input's max attribute.
    MAX_PULL_DURATION_S = 300
    MAX_PULL_POWER_W = 1500

    plan = models.ForeignKey(TttPlan, on_delete=models.CASCADE, related_name="riders")
    order = models.PositiveIntegerField(default=0, help_text="Pull order (0-based)")
    zwid = models.PositiveIntegerField(null=True, blank=True, help_text="Zwift ID, if linked to team data")
    name = models.CharField(max_length=200, help_text="Rider display name")
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="Weight in kg")
    height_cm = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Height in cm")
    ftp_w = models.PositiveSmallIntegerField(null=True, blank=True, help_text="FTP in watts")
    cp_w = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Critical power in watts, from Zwift Racing (blank: FTP stands in)"
    )
    w_prime_j = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="W′ (anaerobic work capacity) in joules, from Zwift Racing (blank: 20 kJ stands in)",
    )
    pull_power_w = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(MAX_PULL_POWER_W)],
        help_text="Override pull power in watts",
    )
    pull_duration_s = models.PositiveIntegerField(
        default=60,
        validators=[MaxValueValidator(MAX_PULL_DURATION_S)],
        help_text="Pull duration in seconds",
    )
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

"""Models for ZwiftPower data."""

from typing import ClassVar

from django.db import models
from simple_history.models import HistoricalRecords


class ZPTeamRiders(models.Model):
    """ZwiftPower team member data from the team admin API.

    Each row is a team member.
    Fetch from: https://zwiftpower.com/api3.php?do=team_riders&id={team_id}
    Example data: test/example_data/zwiftpower/team_admin_api.json

    """

    # Zwift/ZwiftPower identifiers
    zwid = models.PositiveIntegerField(unique=True, help_text="Zwift ID")
    aid = models.CharField(max_length=20, blank=True, help_text="ZwiftPower athlete ID")
    name = models.CharField(max_length=255, help_text="Rider display name")

    # Demographics
    flag = models.CharField(max_length=10, blank=True, help_text="Country code")
    age = models.CharField(max_length=10, blank=True, help_text="Age category (e.g., '50+', 'Mas', '-')")

    # Division/Category
    div = models.PositiveSmallIntegerField(default=0, help_text="Division")
    divw = models.PositiveSmallIntegerField(default=0, help_text="Women's division")

    # Ranking
    r = models.CharField(max_length=10, blank=True, help_text="Rank position")
    rank = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Ranking score")

    # Physical stats
    ftp = models.PositiveSmallIntegerField(null=True, blank=True, help_text="FTP in watts")
    weight = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="Weight in kg")

    # Skill scores
    skill = models.PositiveIntegerField(default=0, help_text="Total skill score")
    skill_race = models.PositiveIntegerField(default=0, help_text="Racing skill score")
    skill_seg = models.PositiveIntegerField(default=0, help_text="Segment skill score")
    skill_power = models.PositiveIntegerField(default=0, help_text="Power skill score")

    # Cumulative stats
    distance = models.PositiveBigIntegerField(default=0, help_text="Total distance in meters")
    climbed = models.PositiveIntegerField(default=0, help_text="Total elevation in meters")
    energy = models.PositiveIntegerField(default=0, help_text="Total energy in kJ")
    time = models.PositiveIntegerField(default=0, help_text="Total time in seconds")

    # Power records - 20 minute (1200 seconds)
    h_1200_watts = models.PositiveSmallIntegerField(null=True, blank=True, help_text="20-min power in watts")
    h_1200_wkg = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="20-min w/kg")

    # Power records - 15 seconds
    h_15_watts = models.PositiveSmallIntegerField(null=True, blank=True, help_text="15-sec power in watts")
    h_15_wkg = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="15-sec w/kg")

    # Status fields
    status = models.CharField(max_length=50, blank=True, help_text="Member status")
    reg = models.BooleanField(default=False, help_text="Registered flag")
    email = models.EmailField(blank=True, help_text="Email address (if available)")
    zada = models.SmallIntegerField(default=0, help_text="ZADA status flag (-1=not checked, 0=clean)")

    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)
    date_left = models.DateTimeField(null=True, blank=True, help_text="Date member left team")

    # History tracking
    history = HistoricalRecords()

    # Fields that trigger a new history record when changed
    TRACKED_FIELDS: ClassVar[list[str]] = [
        "weight",
        "ftp",
        "div",
        "divw",
        "rank",
        "skill",
        "skill_race",
        "skill_seg",
        "skill_power",
        "h_1200_watts",
        "h_1200_wkg",
        "h_15_watts",
        "h_15_wkg",
    ]

    class Meta:
        """Meta options for ZPTeamRiders model."""

        verbose_name = "ZP Team Member"
        verbose_name_plural = "ZP Team Members"
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        """Return string representation of rider.

        Returns:
            Name and Zwift ID.

        """
        return f"{self.name} ({self.zwid})"

    def save(self, *args, **kwargs) -> None:
        """Save with conditional history creation.

        Only creates a history record if tracked fields changed.

        Args:
            *args: Positional arguments passed to parent save.
            **kwargs: Keyword arguments passed to parent save.

        """
        if self.pk:
            # Existing record - check if tracked fields changed
            try:
                old = ZPTeamRiders.objects.get(pk=self.pk)
                tracked_changed = any(getattr(self, f) != getattr(old, f) for f in self.TRACKED_FIELDS)
                if not tracked_changed:
                    # Skip history for this save
                    self.skip_history_when_saving = True
            except ZPTeamRiders.DoesNotExist:
                pass

        super().save(*args, **kwargs)

        # Reset flag
        if hasattr(self, "skip_history_when_saving"):
            del self.skip_history_when_saving

    @classmethod
    def get_field_history(cls, zwid: int, field: str) -> list[tuple]:
        """Get history of a specific field for a rider.

        Args:
            zwid: Zwift rider ID.
            field: Field name to get history for.

        Returns:
            List of (date, value) tuples, newest first.

        """
        return list(
            cls.history.filter(zwid=zwid)
            .exclude(**{f"{field}__isnull": True})
            .order_by("-history_date")
            .values_list("history_date", field)
        )

    @classmethod
    def get_weight_history(cls, zwid: int) -> list[tuple]:
        """Get weight history for a rider.

        Args:
            zwid: Zwift rider ID.

        Returns:
            List of (date, weight) tuples, newest first.

        """
        return cls.get_field_history(zwid, "weight")

    @classmethod
    def get_ftp_history(cls, zwid: int) -> list[tuple]:
        """Get FTP history for a rider.

        Args:
            zwid: Zwift rider ID.

        Returns:
            List of (date, ftp) tuples, newest first.

        """
        return cls.get_field_history(zwid, "ftp")


class ZPEvent(models.Model):
    """Event data from ZwiftPower team results.

    API URL: https://zwiftpower.com/api3.php?do=team_results&id={TEAM_ID}
    Example data: test/example_data/zwiftpower/team_results.json (events dict)
    """

    zid = models.PositiveIntegerField(unique=True, help_text="ZwiftPower event ID")
    title = models.CharField(max_length=500, help_text="Event title")
    event_date = models.DateTimeField(help_text="Event date/time")

    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for ZPEvent model."""

        verbose_name = "ZP Event"
        verbose_name_plural = "ZP Events"
        ordering: ClassVar[list[str]] = ["-event_date"]

    def __str__(self) -> str:
        """Return string representation of event.

        Returns:
            Title and ZP event ID.

        """
        return f"{self.title} ({self.zid})"


class ZPRiderResults(models.Model):
    """Team rider result data from ZwiftPower.

    API URL: https://zwiftpower.com/api3.php?do=team_results&id={TEAM_ID}
    Example data: test/example_data/zwiftpower/team_results.json (data array)
    """

    # Foreign key to event
    event = models.ForeignKey(
        ZPEvent,
        on_delete=models.CASCADE,
        related_name="results",
        help_text="Related event",
    )

    # Identifiers
    zid = models.PositiveIntegerField(help_text="ZwiftPower event ID")
    zwid = models.PositiveIntegerField(help_text="Zwift rider ID")
    res_id = models.CharField(max_length=50, blank=True, help_text="Result ID (zid.pos)")

    # Rider info
    name = models.CharField(max_length=255, help_text="Rider display name")
    flag = models.CharField(max_length=10, blank=True, help_text="Country code")
    age = models.CharField(max_length=10, blank=True, help_text="Age category")
    male = models.BooleanField(default=True, help_text="Male rider")

    # Team info
    tid = models.CharField(max_length=20, blank=True, help_text="Team ID")
    tname = models.CharField(max_length=100, blank=True, help_text="Team name")

    # Position/Results
    pos = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Overall position")
    position_in_cat = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Position in category")
    category = models.CharField(max_length=10, blank=True, help_text="Race category (A, B, C, D, E)")
    label = models.CharField(max_length=10, blank=True, help_text="Category label")

    # Timing
    time_seconds = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True, help_text="Finish time in seconds"
    )
    time_gun = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True, help_text="Gun time in seconds"
    )
    gap = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, help_text="Gap to winner")

    # Physical stats
    ftp = models.PositiveSmallIntegerField(null=True, blank=True, help_text="FTP in watts")
    weight = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="Weight in kg")
    height = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Height in cm")

    # Power data
    avg_power = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Average power in watts")
    avg_wkg = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="Average w/kg")
    np = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Normalized power")
    wftp = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Weighted FTP")
    wkg_ftp = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="FTP w/kg")

    # Power curve - watts
    w5 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="5-sec power")
    w15 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="15-sec power")
    w30 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="30-sec power")
    w60 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1-min power")
    w120 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="2-min power")
    w300 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="5-min power")
    w1200 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="20-min power")

    # Power curve - w/kg
    wkg5 = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="5-sec w/kg")
    wkg15 = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="15-sec w/kg")
    wkg30 = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="30-sec w/kg")
    wkg60 = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="1-min w/kg")
    wkg120 = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="2-min w/kg")
    wkg300 = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="5-min w/kg")
    wkg1200 = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="20-min w/kg")

    # Heart rate
    avg_hr = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Average heart rate")
    max_hr = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Max heart rate")
    hrm = models.BooleanField(default=False, help_text="Heart rate monitor used")

    # Division/Skill
    div = models.PositiveSmallIntegerField(default=0, help_text="Division")
    divw = models.PositiveSmallIntegerField(default=0, help_text="Women's division")
    skill = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Skill rating")
    skill_gain = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Skill gain from event"
    )

    # Status flags
    zada = models.SmallIntegerField(default=0, help_text="ZADA status (-1=not checked, 0=clean)")
    reg = models.BooleanField(default=False, help_text="Registered")
    penalty = models.CharField(max_length=50, blank=True, help_text="Penalty info")
    upg = models.BooleanField(default=False, help_text="Upgrade flag")

    # Event type
    f_t = models.CharField(max_length=50, blank=True, help_text="Event type (TYPE_RACE, TYPE_RIDE)")

    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for ZPRiderResults model."""

        verbose_name = "ZP Rider Result"
        verbose_name_plural = "ZP Rider Results"
        ordering: ClassVar[list[str]] = ["-event__event_date", "pos"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(fields=["zid", "zwid"], name="unique_zid_zwid"),
        ]

    def __str__(self) -> str:
        """Return string representation of result.

        Returns:
            Rider name, event title, and position.

        """
        return f"{self.name} - {self.event.title} (P{self.pos})"

    @classmethod
    def get_weight_height_history(cls, zwid: int) -> list[tuple]:
        """Get weight and height history for a rider ordered by date (newest first).

        Args:
            zwid: The Zwift rider ID.

        Returns:
            List of tuples (event_date, weight, height) ordered newest to oldest.
            Only includes records where weight or height is not None.

        """
        results = (
            cls.objects.filter(zwid=zwid)
            .exclude(weight__isnull=True, height__isnull=True)
            .select_related("event")
            .order_by("-event__event_date")
            .values_list("event__event_date", "weight", "height")
        )
        return list(results)

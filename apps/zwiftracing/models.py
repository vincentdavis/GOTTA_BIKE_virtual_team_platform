"""Models for Zwift Racing data."""

from typing import ClassVar

from django.db import models
from simple_history.models import HistoricalRecords


class ZRRider(models.Model):
    """Zwift Racing rider data from the club, rider and riders APIs.

    Example data: test/example_data/zwiftracing/rider_api.json
    """

    # Basic rider info
    zwid = models.PositiveIntegerField(unique=True, help_text="Zwift ID")
    name = models.CharField(max_length=255, help_text="Rider display name")
    gender = models.CharField(max_length=10, blank=True, help_text="Gender (M/F)")
    country = models.CharField(max_length=10, blank=True, help_text="Country code")
    age = models.CharField(max_length=10, blank=True, help_text="Age category")
    height = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Height in cm")
    weight = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="Weight in kg")

    # ZwiftPower category
    zp_category = models.CharField(max_length=5, blank=True, help_text="ZwiftPower category (A/B/C/D/E)")
    zp_ftp = models.PositiveSmallIntegerField(null=True, blank=True, help_text="ZwiftPower FTP")

    # Power data - watts per kg
    power_wkg5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="5-sec w/kg")
    power_wkg15 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="15-sec w/kg")
    power_wkg30 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="30-sec w/kg")
    power_wkg60 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="60-sec w/kg")
    power_wkg120 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="2-min w/kg")
    power_wkg300 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="5-min w/kg")
    power_wkg1200 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="20-min w/kg")

    # Power data - watts
    power_w5 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="5-sec watts")
    power_w15 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="15-sec watts")
    power_w30 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="30-sec watts")
    power_w60 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="60-sec watts")
    power_w120 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="2-min watts")
    power_w300 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="5-min watts")
    power_w1200 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="20-min watts")

    # Power metrics
    power_cp = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, help_text="Critical Power")
    power_awc = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Anaerobic Work Capacity"
    )
    power_compound_score = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Compound score"
    )

    # Race rating - current
    race_current_rating = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Current race rating"
    )
    race_current_date = models.BigIntegerField(null=True, blank=True, help_text="Current rating date (timestamp)")
    race_current_category = models.CharField(max_length=20, blank=True, help_text="Current mixed category")
    race_current_category_num = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Current category number"
    )

    # Race rating - last
    race_last_rating = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Last race rating"
    )
    race_last_date = models.BigIntegerField(null=True, blank=True, help_text="Last rating date (timestamp)")
    race_last_category = models.CharField(max_length=20, blank=True, help_text="Last mixed category")
    race_last_category_num = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Last category number")

    # Race rating - max30
    race_max30_rating = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Max 30-day rating"
    )
    race_max30_date = models.BigIntegerField(null=True, blank=True, help_text="Max 30-day date (timestamp)")
    race_max30_expires = models.BigIntegerField(null=True, blank=True, help_text="Max 30-day expires (timestamp)")
    race_max30_category = models.CharField(max_length=20, blank=True, help_text="Max 30-day category")
    race_max30_category_num = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Max 30-day category number"
    )

    # Race rating - max90
    race_max90_rating = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Max 90-day rating"
    )
    race_max90_date = models.BigIntegerField(null=True, blank=True, help_text="Max 90-day date (timestamp)")
    race_max90_expires = models.BigIntegerField(null=True, blank=True, help_text="Max 90-day expires (timestamp)")
    race_max90_category = models.CharField(max_length=20, blank=True, help_text="Max 90-day category")
    race_max90_category_num = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Max 90-day category number"
    )

    # Race stats
    race_finishes = models.PositiveIntegerField(default=0, help_text="Total race finishes")
    race_dnfs = models.PositiveIntegerField(default=0, help_text="Total DNFs")
    race_wins = models.PositiveIntegerField(default=0, help_text="Total wins")
    race_podiums = models.PositiveIntegerField(default=0, help_text="Total podiums")

    # Handicaps - profile
    handicap_flat = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, help_text="Flat profile handicap"
    )
    handicap_rolling = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, help_text="Rolling profile handicap"
    )
    handicap_hilly = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, help_text="Hilly profile handicap"
    )
    handicap_mountainous = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, help_text="Mountainous profile handicap"
    )

    # Phenotype
    phenotype_value = models.CharField(max_length=50, blank=True, help_text="Phenotype classification")
    phenotype_bias = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, help_text="Phenotype bias"
    )
    phenotype_sprinter = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True, help_text="Sprinter score"
    )
    phenotype_puncheur = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True, help_text="Puncheur score"
    )
    phenotype_pursuiter = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True, help_text="Pursuiter score"
    )
    phenotype_climber = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True, help_text="Climber score"
    )
    phenotype_tt = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True, help_text="Time trial score"
    )

    # Club info
    club_id = models.PositiveIntegerField(null=True, blank=True, help_text="Club ID")
    club_name = models.CharField(max_length=255, blank=True, help_text="Club name")

    # Seed ratings
    seed_race = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Seed race rating"
    )
    seed_time_trial = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Seed time trial rating"
    )
    seed_endurance = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Seed endurance factor"
    )
    seed_pursuit = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Seed pursuit factor"
    )
    seed_sprint = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Seed sprint factor"
    )
    seed_punch = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Seed punch factor"
    )
    seed_climb = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Seed climb factor"
    )
    seed_tt_factor = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Seed time trial factor"
    )

    # Velo ratings
    velo_race = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Velo race rating"
    )
    velo_time_trial = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Velo time trial rating"
    )
    velo_endurance = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Velo endurance factor"
    )
    velo_pursuit = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Velo pursuit factor"
    )
    velo_sprint = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Velo sprint factor"
    )
    velo_punch = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Velo punch factor"
    )
    velo_climb = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Velo climb factor"
    )
    velo_tt_factor = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Velo time trial factor"
    )

    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True, help_text="Record created date")
    date_modified = models.DateTimeField(auto_now=True, help_text="Record last modified date")
    date_left = models.DateTimeField(null=True, blank=True, help_text="Date rider left club")

    # History tracking
    history = HistoricalRecords()

    # Fields that trigger a new history record when changed
    TRACKED_FIELDS: ClassVar[list[str]] = [
        # Physical
        "weight",
        "height",
        # ZwiftPower
        "zp_ftp",
        "zp_category",
        # Power metrics
        "power_cp",
        "power_wkg5",
        "power_wkg15",
        "power_wkg60",
        "power_wkg300",
        "power_wkg1200",
        # Race ratings
        "race_current_rating",
        "race_current_category",
        "race_max30_rating",
        "race_max30_category",
        "race_max90_rating",
        "race_max90_category",
        # Phenotype
        "phenotype_value",
        # Seed and Velo
        "seed_race",
        "velo_race",
        # Club
        "club_id",
        "club_name",
    ]

    class Meta:
        """Meta options for ZRRider model."""

        verbose_name = "ZR Rider"
        verbose_name_plural = "ZR Riders"
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        """Return string representation of the rider.

        Returns:
            String with rider name and ID.

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
                old = ZRRider.objects.get(pk=self.pk)
                tracked_changed = any(getattr(self, f) != getattr(old, f) for f in self.TRACKED_FIELDS)
                if not tracked_changed:
                    # Skip history for this save
                    self.skip_history_when_saving = True
            except ZRRider.DoesNotExist:
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
    def get_rating_history(cls, zwid: int) -> list[tuple]:
        """Get race rating history for a rider.

        Args:
            zwid: Zwift rider ID.

        Returns:
            List of (date, rating, category) tuples, newest first.

        """
        return list(
            cls.history.filter(zwid=zwid)
            .exclude(race_current_rating__isnull=True)
            .order_by("-history_date")
            .values_list("history_date", "race_current_rating", "race_current_category")
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

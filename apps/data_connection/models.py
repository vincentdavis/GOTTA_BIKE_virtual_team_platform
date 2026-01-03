"""Models for data_connection app."""

from datetime import timedelta
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone


class DataConnection(models.Model):
    """Configuration for syncing team roster data to Google Sheets.

    DataConnections allow team administrators to export unified roster data
    (from User accounts, ZwiftPower, and Zwift Racing) to Google Sheets for
    external analysis, race planning, or integration with other tools.

    Data Flow:
        1. Admin creates connection with spreadsheet URL and field selections
        2. Optionally, a new Google Sheet can be created automatically
        3. Sync task exports data to the specified sheet tab
        4. Headers are set based on selected fields
        5. Connection expires after date_expires (default: 1 year)

    Field Categories:
        - BASE_FIELDS: Always included (zwid, discord_username, discord_id)
        - USER_FIELDS: User profile data (name, location, gender, etc.)
        - ZWIFTPOWER_FIELDS: ZwiftPower stats (FTP, ranking, power records)
        - ZWIFTRACING_FIELDS: Zwift Racing data (ratings, phenotype, race stats)

    Google Sheets Integration:
        - Requires GOOGLE_CREDENTIALS_BASE64 env var with service account credentials
        - Service account email must have edit access to existing sheets
        - New sheets are created and shared with specified email

    Attributes:
        title: Display name for the connection (also used as spreadsheet name if creating new).
        description: Optional notes about the connection's purpose.
        spreadsheet_url: Full Google Sheets URL.
        data_sheet: Name of the sheet tab for data export (default: DATA_CONN).
        selected_fields: JSON list of field keys to include beyond BASE_FIELDS.
        date_created: Auto-set when connection is created.
        date_updated: Auto-updated on each save.
        date_last_synced: Timestamp of last successful data sync.
        date_expires: When connection becomes inactive (default: 1 year from creation).
        created_by: User who created the connection.

    Properties:
        is_expired: Whether connection has passed its expiry date.
        days_until_expiry: Days remaining (negative if expired).

    """

    title = models.CharField(
        max_length=255,
        help_text="Title for this data connection",
    )
    description = models.TextField(
        blank=True,
        help_text="Description of this data connection",
    )
    spreadsheet_url = models.URLField(
        max_length=500,
        help_text="Google Sheets URL (e.g., https://docs.google.com/spreadsheets/d/.../edit)",
    )
    data_sheet = models.CharField(
        max_length=100,
        default="DATA_CONN",
        verbose_name="Sheet Name (tab)",
        help_text="Name of the sheet tab where data will be sent",
    )
    selected_fields = models.JSONField(
        default=list,
        blank=True,
        help_text="List of fields to include in export (User, ZwiftPower, ZwiftRacing fields)",
    )
    filters = models.JSONField(
        default=dict,
        blank=True,
        help_text="Filter criteria for data export",
    )
    date_created = models.DateTimeField(
        auto_now_add=True,
        help_text="When this connection was created",
    )
    date_updated = models.DateTimeField(
        auto_now=True,
        help_text="When this connection was last updated",
    )
    date_last_synced = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When data was last synced to the spreadsheet",
    )
    date_expires = models.DateTimeField(
        help_text="When this connection expires",
    )
    owner_email = models.EmailField(
        blank=True,
        help_text="Email of the Google Sheet owner",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="data_connections",
        help_text="User who created this connection",
    )

    class Meta:
        """Meta options for DataConnection model."""

        verbose_name = "Data Connection"
        verbose_name_plural = "Data Connections"
        ordering: ClassVar[list[str]] = ["-date_created"]

    def __str__(self) -> str:
        """Return string representation of connection.

        Returns:
            The title of the connection.

        """
        return self.title

    def save(self, *args, **kwargs) -> None:
        """Save the connection, setting default expiry if not set.

        Args:
            *args: Positional arguments passed to parent save.
            **kwargs: Keyword arguments passed to parent save.

        """
        if not self.date_expires:
            self.date_expires = timezone.now() + timedelta(days=365)
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        """Check if connection has expired.

        Returns:
            True if expired, False otherwise.

        """
        return timezone.now() > self.date_expires

    @property
    def days_until_expiry(self) -> int:
        """Get days until expiry.

        Returns:
            Number of days until expiry (negative if expired).

        """
        delta = self.date_expires - timezone.now()
        return delta.days

    # Base fields always included in exports
    BASE_FIELDS: ClassVar[list[str]] = ["zwid", "discord_username", "discord_id"]

    # Available fields for selection
    USER_FIELDS: ClassVar[list[tuple[str, str]]] = [
        ("first_name", "First Name"),
        ("last_name", "Last Name"),
        ("birth_year", "Birth Year"),
        ("city", "City"),
        ("country", "Country"),
        ("gender", "Gender"),
        ("race_ready", "Race Ready"),
        ("youtube_channel", "YouTube Channel"),
    ]

    ZWIFTPOWER_FIELDS: ClassVar[list[tuple[str, str]]] = [
        # Identifiers
        ("zp_aid", "ZP Athlete ID"),
        ("zp_name", "ZP Name"),
        # Demographics
        ("zp_flag", "ZP Country"),
        ("zp_age", "ZP Age Category"),
        # Division/Category
        ("zp_div", "ZP Division"),
        ("zp_divw", "ZP Women's Division"),
        # Ranking
        ("zp_r", "ZP Rank Position"),
        ("zp_rank", "ZP Ranking Score"),
        # Physical stats
        ("zp_ftp", "ZP FTP"),
        ("zp_weight", "ZP Weight"),
        # Skill scores
        ("zp_skill", "ZP Skill Total"),
        ("zp_skill_race", "ZP Skill Race"),
        ("zp_skill_seg", "ZP Skill Segment"),
        ("zp_skill_power", "ZP Skill Power"),
        # Cumulative stats
        ("zp_distance", "ZP Total Distance"),
        ("zp_climbed", "ZP Total Elevation"),
        ("zp_energy", "ZP Total Energy"),
        ("zp_time", "ZP Total Time"),
        # Power records
        ("zp_h_1200_watts", "ZP 20min Power"),
        ("zp_h_1200_wkg", "ZP 20min W/kg"),
        ("zp_h_15_watts", "ZP 15sec Power"),
        ("zp_h_15_wkg", "ZP 15sec W/kg"),
        # Status
        ("zp_status", "ZP Status"),
        ("zp_reg", "ZP Registered"),
        ("zp_zada", "ZP ZADA Status"),
        ("zp_date_left", "ZP Date Left Team"),
    ]

    ZWIFTRACING_FIELDS: ClassVar[list[tuple[str, str]]] = [
        # Basic info
        ("zr_name", "ZR Name"),
        ("zr_gender", "ZR Gender"),
        ("zr_country", "ZR Country"),
        ("zr_age", "ZR Age Category"),
        ("zr_height", "ZR Height"),
        ("zr_weight", "ZR Weight"),
        # ZwiftPower category
        ("zr_zp_category", "ZR ZP Category"),
        ("zr_zp_ftp", "ZR ZP FTP"),
        # Power data - w/kg
        ("zr_power_wkg5", "ZR 5sec W/kg"),
        ("zr_power_wkg15", "ZR 15sec W/kg"),
        ("zr_power_wkg30", "ZR 30sec W/kg"),
        ("zr_power_wkg60", "ZR 60sec W/kg"),
        ("zr_power_wkg120", "ZR 2min W/kg"),
        ("zr_power_wkg300", "ZR 5min W/kg"),
        ("zr_power_wkg1200", "ZR 20min W/kg"),
        # Power data - watts
        ("zr_power_w5", "ZR 5sec Watts"),
        ("zr_power_w15", "ZR 15sec Watts"),
        ("zr_power_w30", "ZR 30sec Watts"),
        ("zr_power_w60", "ZR 60sec Watts"),
        ("zr_power_w120", "ZR 2min Watts"),
        ("zr_power_w300", "ZR 5min Watts"),
        ("zr_power_w1200", "ZR 20min Watts"),
        # Power metrics
        ("zr_power_cp", "ZR Critical Power"),
        ("zr_power_awc", "ZR Anaerobic Work Capacity"),
        ("zr_power_compound_score", "ZR Compound Score"),
        # Race rating - current
        ("zr_race_current_rating", "ZR Current Rating"),
        ("zr_race_current_category", "ZR Current Category"),
        # Race rating - max30
        ("zr_race_max30_rating", "ZR Max 30-day Rating"),
        ("zr_race_max30_category", "ZR Max 30-day Category"),
        # Race rating - max90
        ("zr_race_max90_rating", "ZR Max 90-day Rating"),
        ("zr_race_max90_category", "ZR Max 90-day Category"),
        # Race stats
        ("zr_race_finishes", "ZR Race Finishes"),
        ("zr_race_dnfs", "ZR DNFs"),
        ("zr_race_wins", "ZR Wins"),
        ("zr_race_podiums", "ZR Podiums"),
        # Handicaps
        ("zr_handicap_flat", "ZR Handicap Flat"),
        ("zr_handicap_rolling", "ZR Handicap Rolling"),
        ("zr_handicap_hilly", "ZR Handicap Hilly"),
        ("zr_handicap_mountainous", "ZR Handicap Mountainous"),
        # Phenotype
        ("zr_phenotype_value", "ZR Phenotype"),
        ("zr_phenotype_sprinter", "ZR Sprinter Score"),
        ("zr_phenotype_puncheur", "ZR Puncheur Score"),
        ("zr_phenotype_pursuiter", "ZR Pursuiter Score"),
        ("zr_phenotype_climber", "ZR Climber Score"),
        ("zr_phenotype_tt", "ZR TT Score"),
        # Club
        ("zr_club_name", "ZR Club Name"),
        ("zr_date_left", "ZR Date Left Club"),
    ]

    # Filter choices
    GENDER_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("", "All"),
        ("M", "Male"),
        ("F", "Female"),
    ]

    ZP_DIVISION_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("", "All"),
        ("5", "A+ (5)"),
        ("10", "A (10)"),
        ("20", "B (20)"),
        ("30", "C (30)"),
        ("40", "D (40)"),
        ("50", "E (50)"),
    ]

    ZR_PHENOTYPE_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("", "All"),
        ("Sprinter", "Sprinter"),
        ("Puncheur", "Puncheur"),
        ("Pursuiter", "Pursuiter"),
        ("Climber", "Climber"),
        ("Time Trialist", "Time Trialist"),
    ]

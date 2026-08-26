"""Models for events app."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

ZR_CATEGORY_ORDER = [
    "Diamond",
    "Ruby",
    "Emerald",
    "Sapphire",
    "Amethyst",
    "Platinum",
    "Gold",
    "Silver",
    "Bronze",
    "Copper",
]
ZR_CATEGORY_CHOICES = [(cat, cat) for cat in ZR_CATEGORY_ORDER]

# Zwift category ranking, highest -> lowest. "A+" is a real ZwiftPower category
# (division 5); squads only set bounds from A..E, but riders may be A+, so the rank
# used for comparison includes it.
ZWIFT_CATEGORY_ORDER = ["A+", "A", "B", "C", "D", "E"]
ZWIFT_CATEGORY_BOUND_CHOICES = [(c, c) for c in ("A", "B", "C", "D", "E")]


DEFAULT_TIMEZONE_OPTIONS = ["US EAST", "US WEST", "Atlantic", "EMEA Central", "EMEA West"]
# Fixed squad-gender options. "Male"/"Female" require a matching User.gender when enforced;
# "COED" allows any gender. This list is intentionally not user-configurable.
DEFAULT_SQUAD_GENDER_OPTIONS = ["Male", "Female", "COED"]
SQUAD_GENDER_CHOICES = [(g, g) for g in DEFAULT_SQUAD_GENDER_OPTIONS]


def _default_timezone_options() -> list[str]:
    """Return a copy of the default timezone options list.

    Returns:
        List of default timezone option strings.

    """
    return list(DEFAULT_TIMEZONE_OPTIONS)


def _default_squad_gender_options() -> list[str]:
    """Return a copy of the default squad gender options list.

    Returns:
        List of default squad gender option strings.

    """
    return list(DEFAULT_SQUAD_GENDER_OPTIONS)


class Event(models.Model):
    """A team event such as a race series, time trial, or club ride.

    Attributes:
        title: Display name for the event.
        description: Longer description of the event (supports Markdown).
        config_option: Configuration profile (LADDER/SERIES/TTT) gating optional event behaviors.
        start_date: Event start date.
        end_date: Event end date.
        visible: Whether the event is visible to team members.
        head_captain_role_id: Discord role ID for the head captain of this event.
        url: External URL for event details or signup.
        discord_channel_id: Discord channel ID for event coordination.
        created_at: When the record was created.
        updated_at: When the record was last modified.
        created_by: User who created the event.

    """

    class ConfigOption(models.TextChoices):
        """Event configuration profiles that gate optional event behaviors."""

        LADDER = "LADDER", "Ladder"
        SERIES = "SERIES", "Series"
        TTT = "TTT", "TTT"

    prefixes = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Channel/role prefixes (list), e.g. ["$", "~"]. '
            "Roles matching any of these prefixes appear in event/squad selectors."
        ),
    )
    config_option = models.CharField(
        max_length=20,
        choices=ConfigOption.choices,
        blank=True,
        default="",
        help_text="Configuration profile used to enable optional event behaviors",
    )
    title = models.CharField(max_length=200, help_text="Event title")
    description = models.TextField(blank=True, help_text="Event description (supports Markdown)")
    start_date = models.DateField(help_text="Event start date")
    end_date = models.DateField(help_text="Event end date")
    visible = models.BooleanField(default=True, help_text="Whether the event is visible to team members")
    signups_open = models.BooleanField(default=False, help_text="Whether signups are currently open")
    show_signups = models.BooleanField(
        default=False,
        help_text="Let all logged-in members expand the signup list (names only); admins see full details",
    )
    signup_instructions = models.TextField(blank=True, help_text="Instructions shown at the top of the signup form")
    timezone_options = models.JSONField(
        default=_default_timezone_options,
        blank=True,
        help_text="Timezone options available at signup",
    )
    timezone_required = models.BooleanField(default=False, help_text="Whether timezone selection is required at signup")
    timezone_role_map = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Optional map of timezone/region option -> Discord role ID, granted on signup. "
            "Empty means the feature is off for this event."
        ),
    )
    squad_gender_options = models.JSONField(
        default=_default_squad_gender_options,
        blank=True,
        help_text="Squad gender preference options available at signup",
    )
    squad_gender_required = models.BooleanField(
        default=False,
        help_text="Require squad gender preference at signup (also gates whether the field appears)",
    )
    require_race_verified_availability = models.BooleanField(
        default=False,
        help_text="Require Race Verified status before a member can submit availability for this event",
    )

    # Event-level defaults for the availability builder's toggles. Each is a pair:
    #
    #   grid_default_*  null means "no event default" -- the builder keeps its own.
    #                   True/False seeds a new grid with that value.
    #   grid_enforce_*  locks the control to the default, in both directions, so an
    #                   event can require a setting be off as well as on.
    #
    # Deliberately not retroactive: changing a default seeds new grids and is applied
    # on save, rather than silently rewriting grids captains have already published.
    # Enforcement is re-applied server-side because the builder posts JSON.
    grid_default_max_races_question = models.BooleanField(
        null=True,
        blank=True,
        help_text='Default for "Ask: Max number of races" on new availability grids. Unset leaves it to the captain.',
    )
    grid_enforce_max_races_question = models.BooleanField(
        default=False,
        help_text='Lock "Ask: Max number of races" to the event default; captains cannot change it.',
    )
    grid_default_rest_days_question = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            'Default for "Ask: Rest days between races" on new availability grids. '
            "Unset leaves it to the captain."
        ),
    )
    grid_enforce_rest_days_question = models.BooleanField(
        default=False,
        help_text='Lock "Ask: Rest days between races" to the event default; captains cannot change it.',
    )
    grid_default_hide_empty_days = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            'Default for "Hide days with no available times" on new availability grids. '
            "Unset leaves it to the captain."
        ),
    )
    grid_enforce_hide_empty_days = models.BooleanField(
        default=False,
        help_text='Lock "Hide days with no available times" to the event default; captains cannot change it.',
    )
    grid_default_single_slot = models.BooleanField(
        null=True,
        blank=True,
        help_text='Default for "Single Time Slot" on new availability grids. Unset leaves it to the captain.',
    )
    grid_enforce_single_slot = models.BooleanField(
        default=False,
        help_text='Lock "Single Time Slot" to the event default; captains cannot change it.',
    )
    grid_default_expanded_features = models.BooleanField(
        null=True,
        blank=True,
        help_text='Default for "Expand Features" on new availability grids. Unset leaves it to the captain.',
    )
    grid_enforce_expanded_features = models.BooleanField(
        default=False,
        help_text='Lock "Expand Features" to the event default; captains cannot change it.',
    )

    logo = models.ImageField(upload_to="event_logos/", blank=True, help_text="Optional logo image for the event")
    url = models.URLField(max_length=500, blank=True, help_text="External URL for event details or signup")
    discord_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord channel ID for event coordination (0 = none)",
    )
    signup_notification_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord channel ID for rider signup notifications (0 = disabled)",
    )
    head_captain_role_id = models.BigIntegerField(
        default=0,
        help_text="Discord role ID for the head captain of this event (0 = none)",
    )
    event_role = models.BigIntegerField(
        default=0,
        help_text="Discord role ID for the event (0 = none)",
    )
    coordinator_role_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Discord role IDs (strings) for regional/group coordinators",
    )
    region_role_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Discord role IDs (strings) a squad's Region Role may be chosen from",
    )
    captain_role_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Discord role IDs (strings) a squad's Captain Discord Role may be chosen from",
    )
    created_at = models.DateTimeField(default=timezone.now, help_text="When the event was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the event was last updated")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_events",
        help_text="User who created this event",
    )

    class Meta:
        """Meta options for Event model."""

        ordering = ["-start_date"]  # noqa: RUF012
        verbose_name = "Event"
        verbose_name_plural = "Events"

    def __str__(self) -> str:
        """Return the event title.

        Returns:
            The event title string.

        """
        return self.title


class Race(models.Model):
    """A single race within an event.

    Each race belongs to exactly one Event. An Event can have many Races
    (e.g., weekly rounds in a ZRL season).

    Attributes:
        event: The parent event this race belongs to.
        title: Display name for the race.
        description: Details about the race.
        zwift_category: Zwift category letter (e.g., A, B, C, D, E).
        zwift_rating: Zwift Racing rating range or threshold.
        start_date: Date the race takes place.
        start_time: Scheduled start time (optional).
        end_date: End date if the race spans multiple days.
        url: External URL for race details.
        race_pass: URL for the Zwift race pass/join link.
        discord_channel_id: Discord channel ID for race coordination.
        created_at: When the record was created.
        updated_at: When the record was last modified.
        created_by: User who created the race.

    """

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="races",
        help_text="The event this race belongs to",
    )
    title = models.CharField(max_length=200, help_text="Race title")
    description = models.TextField(help_text="Race description")
    zwift_category = models.CharField(max_length=20, blank=True, help_text="Zwift category (e.g., A, B, C, D, E)")
    zwift_rating = models.CharField(max_length=50, blank=True, help_text="Zwift Racing rating range or threshold")
    start_date = models.DateField(help_text="Race date")
    start_time = models.TimeField(null=True, blank=True, help_text="Scheduled start time")
    end_date = models.DateField(null=True, blank=True, help_text="End date if race spans multiple days")
    url = models.URLField(max_length=500, blank=True, help_text="External URL for race details")
    race_pass = models.URLField(max_length=500, blank=True, help_text="Zwift race pass/join link URL")
    discord_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord channel ID for race coordination (0 = none)",
    )
    created_at = models.DateTimeField(default=timezone.now, help_text="When the race was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the race was last updated")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_races",
        help_text="User who created this race",
    )

    class Meta:
        """Meta options for Race model."""

        ordering = ["start_date", "start_time"]  # noqa: RUF012
        verbose_name = "Race"
        verbose_name_plural = "Races"

    def __str__(self) -> str:
        """Return the race title with event name.

        Returns:
            String in format "Event Title - Race Title".

        """
        return f"{self.event.title} - {self.title}"


class RaceRegistration(models.Model):
    """Links a user to a race they have registered for.

    A user can register for many races, and a race can have many registered users.
    The unique constraint on (user, race) prevents duplicate registrations.

    Attributes:
        user: The registered user.
        race: The race the user registered for.
        status: Registration status (registered, confirmed, withdrawn, no_show).
        notes: Optional notes from the user or admin.
        created_at: When the registration was created.
        updated_at: When the registration was last modified.

    """

    class Status(models.TextChoices):
        """Registration status choices."""

        REGISTERED = "registered", "Registered"
        CONFIRMED = "confirmed", "Confirmed"
        WITHDRAWN = "withdrawn", "Withdrawn"
        NO_SHOW = "no_show", "No Show"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="race_registrations",
        help_text="The registered user",
    )
    race = models.ForeignKey(
        Race,
        on_delete=models.CASCADE,
        related_name="registrations",
        help_text="The race the user registered for",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REGISTERED,
        help_text="Registration status",
    )
    notes = models.TextField(blank=True, help_text="Optional notes from the user or admin")
    created_at = models.DateTimeField(default=timezone.now, help_text="When the registration was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the registration was last updated")

    class Meta:
        """Meta options for RaceRegistration model."""

        ordering = ["-created_at"]  # noqa: RUF012
        unique_together = [("user", "race")]  # noqa: RUF012
        verbose_name = "Race Registration"
        verbose_name_plural = "Race Registrations"

    def __str__(self) -> str:
        """Return user and race description.

        Returns:
            String in format "username - Race Title".

        """
        return f"{self.user} - {self.race.title}"


class EventSignup(models.Model):
    """Links a user to an event they have signed up for.

    Event-level signup independent of squads and races. Squad/race assignment
    happens separately after signup.

    Attributes:
        event: The event the user signed up for.
        user: The signed-up user.
        signup_timezone: Selected timezone from event's timezone_options.
        status: Signup status (registered or withdrawn).
        notes: Optional notes.
        created_at: When the signup was created.
        updated_at: When the signup was last modified.

    """

    class Status(models.TextChoices):
        """Signup status choices."""

        REGISTERED = "registered", "Registered"
        WITHDRAWN = "withdrawn", "Withdrawn"

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="signups",
        help_text="The event the user signed up for",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_signups",
        help_text="The signed-up user",
    )
    signup_timezone = models.JSONField(default=list, blank=True, help_text="Selected timezones from event options")
    signup_squad_gender = models.JSONField(
        default=list,
        blank=True,
        help_text="Selected squad gender preferences from event options",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REGISTERED,
        help_text="Signup status",
    )
    notes = models.TextField(blank=True, help_text="Optional notes")
    custom_answers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Answers to the event's custom signup questions, keyed by question id (as a string)",
    )
    created_at = models.DateTimeField(default=timezone.now, help_text="When the signup was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the signup was last updated")

    class Meta:
        """Meta options for EventSignup model."""

        ordering = ["-created_at"]  # noqa: RUF012
        unique_together = [("event", "user")]  # noqa: RUF012
        verbose_name = "Event Signup"
        verbose_name_plural = "Event Signups"

    def __str__(self) -> str:
        """Return user and event description.

        Returns:
            String in format "username - Event Title".

        """
        return f"{self.user} - {self.event.title}"


class SignupQuestion(models.Model):
    """A custom question an admin adds to an event's signup form.

    Riders answer these when they sign up (and can edit their answers afterwards).
    Answers are stored on ``EventSignup.custom_answers`` keyed by ``str(question.id)``.
    ``options`` applies only to the single/multi choice types and is ignored for
    text and boolean questions.

    Attributes:
        event: The event this question belongs to.
        label: The question text shown to riders.
        question_type: The answer type (text/single/multi/boolean).
        options: Choices for single/multi questions (list of label strings).
        required: Whether riders must answer before signing up.
        help_text: Optional helper text shown under the question.
        order: Display order (lower first).

    """

    class Type(models.TextChoices):
        """Custom signup-question answer types."""

        TEXT = "text", "Short text"
        SINGLE = "single", "Single choice"
        MULTI = "multi", "Multiple choice"
        BOOLEAN = "boolean", "Yes / No"

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="signup_questions",
        help_text="The event this signup question belongs to",
    )
    label = models.CharField(max_length=200, help_text="The question shown to riders")
    question_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.TEXT,
        help_text="Answer type",
    )
    options = models.JSONField(
        default=list,
        blank=True,
        help_text="Choices for single/multiple-choice questions (ignored for text and yes/no)",
    )
    required = models.BooleanField(default=False, help_text="Riders must answer before signing up")
    help_text = models.CharField(max_length=300, blank=True, help_text="Optional helper text shown under the question")
    order = models.PositiveSmallIntegerField(default=0, help_text="Display order (lower numbers first)")
    created_at = models.DateTimeField(default=timezone.now, help_text="When the question was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the question was last updated")

    class Meta:
        """Meta options for SignupQuestion model."""

        ordering = ["order", "id"]  # noqa: RUF012
        verbose_name = "Signup Question"
        verbose_name_plural = "Signup Questions"

    def __str__(self) -> str:
        """Return event and question label.

        Returns:
            String in format "Event Title: label".

        """
        return f"{self.event.title}: {self.label}"

    @property
    def is_choice(self) -> bool:
        """Whether this question type uses the ``options`` list.

        Returns:
            True for single/multiple choice questions.

        """
        return self.question_type in {self.Type.SINGLE, self.Type.MULTI}

    @property
    def has_answers(self) -> bool:
        """Whether any signup for this event has answered this question.

        Used to freeze ``question_type`` once answers exist.

        Returns:
            True if at least one EventSignup stores an answer for this question.

        """
        return self.event.signups.filter(custom_answers__has_key=str(self.pk)).exists()


class Squad(models.Model):
    """A squad within an event (e.g., racing squads/divisions).

    An event has many squads. Users join squads via the SquadMember through model.

    Attributes:
        event: The parent event this squad belongs to.
        name: Squad name.
        squad_timezone: Optional timezone string for the squad.
        discord_channel_id: Discord channel ID for squad coordination.
        audio_channel_id: Discord voice/stage channel ID for squad audio.
        captain: Squad captain.
        vice_captain: Squad vice captain.
        team_discord_role: Discord role ID for the squad.
        discord_captain_role: Discord role ID for the squad captain.
        regional_coordinator_role: Discord role ID for this squad's regional
            coordinator, chosen from the event's coordinator roles.
        region_role: Discord role auto-added to riders assigned to this squad
            and removed on leave, unless the rider still belongs to another
            squad carrying the same region role.
        min_zwift_category: Minimum Zwift category letter.
        max_zwift_category: Maximum Zwift category letter.
        min_womens_zwift_category: Minimum women's Zwift category letter.
        max_womens_zwift_category: Maximum women's Zwift category letter.
        min_zwift_racing_category: Minimum Zwift Racing category.
        max_zwift_racing_category: Maximum Zwift Racing category.
        url: External URL for squad details.
        invite_url: Invite URL for joining the squad.
        members: Many-to-many relation to users via SquadMember.
        created_by: User who created this squad.
        created_at: When the record was created.
        updated_at: When the record was last modified.

    """

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="squads",
        help_text="The event this squad belongs to",
    )
    name = models.CharField(max_length=200, help_text="Squad name")
    squad_timezone = models.CharField(max_length=50, blank=True, help_text="Optional timezone string")
    gender = models.CharField(
        max_length=50,
        blank=True,
        choices=SQUAD_GENDER_CHOICES,
        help_text="Squad gender (Male, Female, or COED)",
    )
    enforce_gender = models.BooleanField(
        default=False,
        help_text="Block adding a rider whose gender does not match the squad gender (COED allows any)",
    )
    discord_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord channel ID for squad coordination (0 = none)",
    )
    audio_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord voice/stage channel ID for squad audio (0 = none)",
    )
    captains = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="captain_squads",
        help_text="Squad captains",
    )
    vice_captains = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="vice_captain_squads",
        help_text="Squad vice captains",
    )
    team_discord_role = models.BigIntegerField(
        default=0,
        help_text="Discord role ID for the squad (0 = none)",
    )
    discord_captain_role = models.BigIntegerField(
        default=0,
        help_text="Discord role ID for the squad captain (0 = none)",
    )
    regional_coordinator_role = models.BigIntegerField(
        default=0,
        help_text="Discord role ID for this squad's regional coordinator, "
        "chosen from the event's coordinator roles (0 = none)",
    )
    region_role = models.BigIntegerField(
        default=0,
        help_text="Discord role added to riders assigned to this squad and removed when they leave — "
        "unless they still belong to another squad with the same region role (0 = none)",
    )
    min_zwift_category = models.CharField(
        max_length=20,
        blank=True,
        choices=ZWIFT_CATEGORY_BOUND_CHOICES,
        help_text="Minimum Zwift category (lowest allowed; A high, E low)",
    )
    max_zwift_category = models.CharField(
        max_length=20,
        blank=True,
        choices=ZWIFT_CATEGORY_BOUND_CHOICES,
        help_text="Maximum Zwift category (highest allowed; A high, E low)",
    )
    enforce_min_zwift_category = models.BooleanField(
        default=False,
        help_text="Block adding a rider lower than the minimum Zwift category",
    )
    enforce_max_zwift_category = models.BooleanField(
        default=False,
        help_text="Block adding a rider higher than the maximum Zwift category",
    )
    min_womens_zwift_category = models.CharField(
        max_length=20,
        blank=True,
        choices=ZWIFT_CATEGORY_BOUND_CHOICES,
        help_text="Minimum women's Zwift category (lowest allowed; A high, E low)",
    )
    max_womens_zwift_category = models.CharField(
        max_length=20,
        blank=True,
        choices=ZWIFT_CATEGORY_BOUND_CHOICES,
        help_text="Maximum women's Zwift category (highest allowed; A high, E low)",
    )
    enforce_min_womens_zwift_category = models.BooleanField(
        default=False,
        help_text="Block adding a woman lower than the minimum women's Zwift category",
    )
    enforce_max_womens_zwift_category = models.BooleanField(
        default=False,
        help_text="Block adding a woman higher than the maximum women's Zwift category",
    )
    min_zwift_racing_category = models.CharField(
        max_length=20,
        blank=True,
        choices=ZR_CATEGORY_CHOICES,
        help_text="Minimum Zwift Racing category",
    )
    max_zwift_racing_category = models.CharField(
        max_length=20,
        blank=True,
        choices=ZR_CATEGORY_CHOICES,
        help_text="Maximum Zwift Racing category",
    )
    enforce_min_zwift_racing_category = models.BooleanField(
        default=False,
        help_text="Block adding a rider lower than the minimum Zwift Racing category",
    )
    enforce_max_zwift_racing_category = models.BooleanField(
        default=False,
        help_text="Block adding a rider higher than the maximum Zwift Racing category",
    )

    require_zauth = models.BooleanField(
        default=False,
        help_text="Require members to have connected their Zwift account through zauth",
    )

    # zFTP / zMAP bounds. Both units are offered because the ZRL division tables
    # combine them -- e.g. "zFTP < 3.74 W/kg AND >= 200W" -- so a W/kg-only bound
    # cannot express a real division. W/kg is derived from the weight Zwift used
    # when it computed the metric (see User.z_ftp_wkg), never the rider's own.
    min_zftp_wkg = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Minimum zFTP in W/kg",
    )
    max_zftp_wkg = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Maximum zFTP in W/kg",
    )
    enforce_min_zftp_wkg = models.BooleanField(
        default=False,
        help_text="Block adding a rider below the minimum zFTP in W/kg",
    )
    enforce_max_zftp_wkg = models.BooleanField(
        default=False,
        help_text="Block adding a rider above the maximum zFTP in W/kg",
    )
    min_zftp_w = models.DecimalField(
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Minimum zFTP in watts",
    )
    max_zftp_w = models.DecimalField(
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Maximum zFTP in watts",
    )
    enforce_min_zftp_w = models.BooleanField(
        default=False,
        help_text="Block adding a rider below the minimum zFTP in watts",
    )
    enforce_max_zftp_w = models.BooleanField(
        default=False,
        help_text="Block adding a rider above the maximum zFTP in watts",
    )
    min_zmap_wkg = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Minimum zMAP in W/kg",
    )
    max_zmap_wkg = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Maximum zMAP in W/kg",
    )
    enforce_min_zmap_wkg = models.BooleanField(
        default=False,
        help_text="Block adding a rider below the minimum zMAP in W/kg",
    )
    enforce_max_zmap_wkg = models.BooleanField(
        default=False,
        help_text="Block adding a rider above the maximum zMAP in W/kg",
    )
    min_zmap_w = models.DecimalField(
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Minimum zMAP in watts",
    )
    max_zmap_w = models.DecimalField(
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Maximum zMAP in watts",
    )
    enforce_min_zmap_w = models.BooleanField(
        default=False,
        help_text="Block adding a rider below the minimum zMAP in watts",
    )
    enforce_max_zmap_w = models.BooleanField(
        default=False,
        help_text="Block adding a rider above the maximum zMAP in watts",
    )
    url = models.URLField(max_length=500, blank=True, help_text="External URL for squad details")
    invite_url = models.URLField(max_length=500, blank=True, help_text="Invite URL for joining the squad")
    captain_notifications = models.BooleanField(
        default=True,
        help_text="Notify captain/vice-captain via Discord DM when squad members' verification records change",
    )
    invite_token = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        editable=False,
        help_text="Token for shareable squad invite links",
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="SquadMember",
        related_name="squads",
        help_text="Squad members",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_squads",
        help_text="User who created this squad",
    )
    created_at = models.DateTimeField(default=timezone.now, help_text="When the squad was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the squad was last updated")

    class Meta:
        """Meta options for Squad model."""

        ordering = ["name"]  # noqa: RUF012
        verbose_name = "Squad"
        verbose_name_plural = "Squads"

    def __str__(self) -> str:
        """Return event and squad name.

        Returns:
            String in format "Event Title - Squad Name".

        """
        return f"{self.event.title} - {self.name}"

    def regenerate_invite_token(self) -> None:
        """Generate or regenerate the squad invite token, invalidating the old one."""
        self.invite_token = uuid.uuid4()
        self.save(update_fields=["invite_token"])

    @property
    def captain_pks(self) -> set[int]:
        """The set of captain user PKs (uses prefetched ``captains`` when available)."""
        return {u.pk for u in self.captains.all()}

    @property
    def vice_captain_pks(self) -> set[int]:
        """The set of vice-captain user PKs (uses prefetched ``vice_captains`` when available)."""
        return {u.pk for u in self.vice_captains.all()}

    def is_leader(self, user) -> bool:
        """Return whether ``user`` is a captain or vice-captain of this squad.

        Args:
            user: The user to check (may be None or anonymous).

        Returns:
            True if the user is a captain or vice-captain.

        """
        if user is None or not getattr(user, "pk", None):
            return False
        return user.pk in self.captain_pks or user.pk in self.vice_captain_pks

    @property
    def zr_requirement_text(self) -> str:
        """A human description of the enforced ZR category bounds (empty if none enforced).

        ZR tiers rank Diamond (highest) to Copper (lowest). ``min_zwift_racing_category`` is the
        lowest tier allowed and ``max_zwift_racing_category`` is the highest tier allowed.
        """
        enforce_min = self.enforce_min_zwift_racing_category and self.min_zwift_racing_category
        enforce_max = self.enforce_max_zwift_racing_category and self.max_zwift_racing_category
        if enforce_min and enforce_max:
            return f"{self.max_zwift_racing_category} to {self.min_zwift_racing_category}"
        if enforce_max:
            return f"{self.max_zwift_racing_category} or lower"
        if enforce_min:
            return f"{self.min_zwift_racing_category} or higher"
        return ""

    def check_zr_eligibility(self, zr_category: str) -> tuple[bool, str]:
        """Check a rider's ZR category against this squad's enforced bounds.

        ZR tiers rank Diamond (highest) to Copper (lowest). A rider must be no higher than
        ``max_zwift_racing_category`` and no lower than ``min_zwift_racing_category`` for the bounds
        that are enforced.

        Args:
            zr_category: The rider's current ZR category (e.g. "Gold"); blank/unknown if not in ZR.

        Returns:
            ``(ok, reason)`` where ``reason`` is a human-readable explanation when ``ok`` is False.

        """
        enforce_min = self.enforce_min_zwift_racing_category and self.min_zwift_racing_category
        enforce_max = self.enforce_max_zwift_racing_category and self.max_zwift_racing_category
        if not enforce_min and not enforce_max:
            return True, ""

        order = ZR_CATEGORY_ORDER  # index 0 = Diamond (highest) ... index 9 = Copper (lowest)
        cat = (zr_category or "").strip()
        if cat not in order:
            return False, f"no ZR category on record; this squad requires {self.zr_requirement_text}"

        rider_idx = order.index(cat)
        if enforce_max and self.max_zwift_racing_category in order:
            max_idx = order.index(self.max_zwift_racing_category)
            if rider_idx < max_idx:
                return False, (
                    f"ZR category {cat} is above this squad's maximum ({self.max_zwift_racing_category})"
                )
        if enforce_min and self.min_zwift_racing_category in order:
            min_idx = order.index(self.min_zwift_racing_category)
            if rider_idx > min_idx:
                return False, (
                    f"ZR category {cat} is below this squad's minimum ({self.min_zwift_racing_category})"
                )
        return True, ""

    def check_gender_eligibility(self, user_gender: str) -> tuple[bool, str]:
        """Check a rider's gender against this squad's enforced gender.

        A "Male" squad requires ``User.gender == "male"``, "Female" requires ``"female"``, and
        "COED" allows any gender. Only enforced when ``enforce_gender`` is set and a squad gender
        is configured.

        Args:
            user_gender: The rider's ``User.gender`` value ("male"/"female"/"other"/blank).

        Returns:
            ``(ok, reason)`` where ``reason`` is a human-readable explanation when ``ok`` is False.

        """
        if not self.enforce_gender or not self.gender or self.gender == "COED":
            return True, ""
        required = {"Male": "male", "Female": "female"}.get(self.gender)
        if required is None:
            return True, ""  # unknown squad gender value; do not block
        if (user_gender or "").strip().lower() == required:
            return True, ""
        shown = user_gender or "unset"
        return False, f"gender ({shown}) does not match this squad's required gender ({self.gender})"

    @staticmethod
    def _check_zwift_category_bounds(
        category: str,
        min_cat: str,
        max_cat: str,
        *,
        enforce_min: bool,
        enforce_max: bool,
        label: str,
    ) -> tuple[bool, str]:
        """Shared A+/A-E category-bounds check for the men's and women's Zwift categories.

        Categories rank A (highest) to E (lowest), with A+ higher than A. A rider must be no
        higher than ``max_cat`` and no lower than ``min_cat`` for the bounds that are enforced.
        Riders without a category of this kind are not blocked (the bounds simply don't apply).

        Args:
            category: The rider's category letter (e.g. "B"); blank/unknown if not categorized.
            min_cat: The squad's minimum (lowest allowed) category.
            max_cat: The squad's maximum (highest allowed) category.
            enforce_min: Whether the minimum bound is enforced.
            enforce_max: Whether the maximum bound is enforced.
            label: Human label for the category kind, used in the block message.

        Returns:
            ``(ok, reason)`` where ``reason`` explains a block.

        """
        enforce_min = enforce_min and bool(min_cat)
        enforce_max = enforce_max and bool(max_cat)
        if not enforce_min and not enforce_max:
            return True, ""

        order = ZWIFT_CATEGORY_ORDER  # index 0 = A+ (highest) ... index 5 = E (lowest)
        cat = (category or "").strip().upper()
        if cat not in order:
            return True, ""  # no category of this kind on record; bounds don't apply

        rider_idx = order.index(cat)
        if enforce_max and max_cat in order and rider_idx < order.index(max_cat):
            return False, f"{label} {cat} is above this squad's maximum ({max_cat})"
        if enforce_min and min_cat in order and rider_idx > order.index(min_cat):
            return False, f"{label} {cat} is below this squad's minimum ({min_cat})"
        return True, ""

    def check_zwift_eligibility(self, zwift_category: str) -> tuple[bool, str]:
        """Check a rider's (overall) Zwift category against this squad's enforced bounds.

        Args:
            zwift_category: The rider's overall ZwiftPower category letter; blank if uncategorized.

        Returns:
            ``(ok, reason)`` where ``reason`` explains a block.

        """
        return self._check_zwift_category_bounds(
            zwift_category,
            self.min_zwift_category,
            self.max_zwift_category,
            enforce_min=self.enforce_min_zwift_category,
            enforce_max=self.enforce_max_zwift_category,
            label="Zwift category",
        )

    def check_womens_zwift_eligibility(self, womens_category: str) -> tuple[bool, str]:
        """Check a rider's women's Zwift category against this squad's enforced bounds.

        Riders without a women's category (e.g. men, or women not yet categorized) are not
        blocked — the women's bounds only apply to riders who have a women's Zwift category.

        Args:
            womens_category: The rider's women's Zwift category letter (e.g. "B"); blank if none.

        Returns:
            ``(ok, reason)`` where ``reason`` explains a block.

        """
        return self._check_zwift_category_bounds(
            womens_category,
            self.min_womens_zwift_category,
            self.max_womens_zwift_category,
            enforce_min=self.enforce_min_womens_zwift_category,
            enforce_max=self.enforce_max_womens_zwift_category,
            label="women's Zwift category",
        )

    # Field-name contract for the metric bounds below: for prefix "zftp" and unit
    # "wkg" the four fields are min_zftp_wkg / max_zftp_wkg and their two
    # enforce_ twins. Sixteen explicit parameters would be worse than this getattr.
    _METRIC_UNITS = (("wkg", "W/kg", 2), ("w", "W", 0))

    def _check_metric_bounds(
        self,
        prefix: str,
        label: str,
        watts: float | None,
        wkg: float | None,
    ) -> tuple[bool, str]:
        """Check one Zwift metric against this squad's enforced W/kg and watt bounds.

        A rider with no value for an enforced bound is blocked rather than waved
        through -- unlike the category rules, which ignore riders they can't classify.
        These metrics only exist for zauth-connected riders, so "no data" here means
        "not connected", and a squad that enforces a power floor means it.

        Args:
            prefix: Field-name stem, "zftp" or "zmap".
            label: Human name for messages, e.g. "zFTP".
            watts: The rider's metric in watts, or None.
            wkg: The rider's metric in W/kg, or None.

        Returns:
            ``(ok, reason)`` where ``reason`` explains a block.

        """
        for unit, unit_label, places in self._METRIC_UNITS:
            value = wkg if unit == "wkg" else watts
            for bound in ("min", "max"):
                if not getattr(self, f"enforce_{bound}_{prefix}_{unit}"):
                    continue
                limit = getattr(self, f"{bound}_{prefix}_{unit}")
                if limit is None:
                    continue
                if value is None:
                    return False, f"No {label} data - connect Zwift to join"
                shown = f"{value:.{places}f}"
                if bound == "min" and Decimal(str(value)) < limit:
                    return False, f"{label} {shown} {unit_label} is below this squad's minimum ({limit})"
                if bound == "max" and Decimal(str(value)) > limit:
                    return False, f"{label} {shown} {unit_label} is above this squad's maximum ({limit})"
        return True, ""

    def check_zauth_eligibility(self, is_zauth_verified: bool) -> tuple[bool, str]:
        """Check whether a rider satisfies this squad's zauth requirement.

        A legacy or admin-granted Zwift verification does not count: the point of the
        requirement is that Zwift itself confirmed the account, which is also what makes
        zFTP/zMAP available. Squads enforcing a power bound will usually want this on,
        so the reason a rider is turned away names the connection rather than the metric.

        Args:
            is_zauth_verified: Whether the rider verified through Zwift OAuth.

        Returns:
            ``(ok, reason)`` where ``reason`` explains a block.

        """
        if self.require_zauth and not is_zauth_verified:
            return False, "This squad requires a Zwift account connected through zauth"
        return True, ""

    def check_zftp_eligibility(self, watts: float | None, wkg: float | None) -> tuple[bool, str]:
        """Check a rider's zFTP against this squad's enforced bounds.

        Args:
            watts: The rider's zFTP in watts, or None if not connected.
            wkg: The rider's zFTP in W/kg, or None if unknown.

        Returns:
            ``(ok, reason)`` where ``reason`` explains a block.

        """
        return self._check_metric_bounds("zftp", "zFTP", watts, wkg)

    def check_zmap_eligibility(self, watts: float | None, wkg: float | None) -> tuple[bool, str]:
        """Check a rider's zMAP against this squad's enforced bounds.

        Args:
            watts: The rider's zMAP in watts, or None if not connected.
            wkg: The rider's zMAP in W/kg, or None if unknown.

        Returns:
            ``(ok, reason)`` where ``reason`` explains a block.

        """
        return self._check_metric_bounds("zmap", "zMAP", watts, wkg)

    @staticmethod
    def _bounds_text(min_cat: str, max_cat: str, enforce_min: bool, enforce_max: bool) -> str:
        """Describe an enforced category range. ``max_cat`` is highest, ``min_cat`` lowest.

        Returns:
            A short range label (e.g. "B-D", "B or lower"), or "" if nothing is enforced.

        """
        emin = enforce_min and min_cat
        emax = enforce_max and max_cat
        if emin and emax:
            return f"{max_cat}-{min_cat}"
        if emax:
            return f"{max_cat} or lower"
        if emin:
            return f"{min_cat} or higher"
        return ""

    @property
    def enforcement_summary(self) -> list[str]:
        """Short labels for each membership requirement this squad enforces.

        Used to show enforcement status on the squad list. Empty when nothing is enforced.
        """
        items: list[str] = []
        if self.enforce_gender and self.gender:
            items.append(f"Gender: {self.gender}")
        zwift = self._bounds_text(
            self.min_zwift_category,
            self.max_zwift_category,
            self.enforce_min_zwift_category,
            self.enforce_max_zwift_category,
        )
        if zwift:
            items.append(f"Zwift: {zwift}")
        womens = self._bounds_text(
            self.min_womens_zwift_category,
            self.max_womens_zwift_category,
            self.enforce_min_womens_zwift_category,
            self.enforce_max_womens_zwift_category,
        )
        if womens:
            items.append(f"Women's Zwift: {womens}")
        zr = self._bounds_text(
            self.min_zwift_racing_category,
            self.max_zwift_racing_category,
            self.enforce_min_zwift_racing_category,
            self.enforce_max_zwift_racing_category,
        )
        if zr:
            items.append(f"ZR: {zr}")
        if self.require_zauth:
            items.append("Zwift connected (zauth)")
        for prefix, label in (("zftp", "zFTP"), ("zmap", "zMAP")):
            for unit, unit_label, _places in self._METRIC_UNITS:
                text = self._metric_bounds_text(prefix, unit)
                if text:
                    items.append(f"{label}: {text} {unit_label}")
        return items

    def _metric_bounds_text(self, prefix: str, unit: str) -> str:
        """Describe one metric's enforced range in one unit.

        Args:
            prefix: Field-name stem, "zftp" or "zmap".
            unit: Field-name suffix, "wkg" or "w".

        Returns:
            A short range label (e.g. "3.08-3.74", ">= 200"), or "" if nothing is enforced.

        """
        low = getattr(self, f"min_{prefix}_{unit}") if getattr(self, f"enforce_min_{prefix}_{unit}") else None
        high = getattr(self, f"max_{prefix}_{unit}") if getattr(self, f"enforce_max_{prefix}_{unit}") else None
        if low is not None and high is not None:
            return f"{low}-{high}"
        if low is not None:
            return f">= {low}"
        if high is not None:
            return f"<= {high}"
        return ""


class SquadMember(models.Model):
    """Links a user to a squad with membership status.

    Attributes:
        squad: The squad.
        user: The member.
        status: Membership status (member, pending, rejected).
        created_at: When the membership was created.
        updated_at: When the membership was last modified.

    """

    class Status(models.TextChoices):
        """Squad membership status choices."""

        MEMBER = "member", "Member"
        PENDING = "pending", "Pending"
        REJECTED = "rejected", "Rejected"

    squad = models.ForeignKey(
        Squad,
        on_delete=models.CASCADE,
        related_name="squad_members",
        help_text="The squad",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="squad_memberships",
        help_text="The squad member",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Membership status",
    )
    created_at = models.DateTimeField(default=timezone.now, help_text="When the membership was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the membership was last updated")

    class Meta:
        """Meta options for SquadMember model."""

        ordering = ["user__first_name", "user__last_name"]  # noqa: RUF012
        unique_together = [("squad", "user")]  # noqa: RUF012
        verbose_name = "Squad Member"
        verbose_name_plural = "Squad Members"

    def __str__(self) -> str:
        """Return squad and user.

        Returns:
            String in format "Squad Name - User".

        """
        return f"{self.squad.name} - {self.user}"


class AvailabilityGrid(models.Model):
    """A date/time grid configuration for collecting squad member availability.

    Created by event admins or squad captains. Members respond by marking
    which time slots they are available.

    Attributes:
        id: UUID primary key for shareable member-facing URLs.
        squad: The squad this grid belongs to.
        title: Optional label; auto-generated on save if blank.
        start_date: Grid start date.
        end_date: Grid end date.
        start_time: UTC start time as "HH:MM" string.
        end_time: UTC end time as "HH:MM" string.
        slot_duration: Minutes per slot (15, 30, or 60).
        blocked_cells: JSON list of blocked cell dicts.
        status: Grid lifecycle status (draft/published/closed).
        expires: Optional date when this grid expires.
        created_by: User who created this grid.
        created_at: When the grid was created.
        updated_at: When the grid was last modified.

    """

    class Status(models.TextChoices):
        """Grid lifecycle status choices."""

        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    squad = models.ForeignKey(
        Squad,
        on_delete=models.CASCADE,
        related_name="availability_grids",
        help_text="The squad this availability grid belongs to",
    )
    title = models.CharField(max_length=200, blank=True, help_text="Optional label for this grid")
    start_date = models.DateField(help_text="Grid start date")
    end_date = models.DateField(help_text="Grid end date")
    start_time = models.CharField(max_length=5, help_text='UTC start time as "HH:MM"')
    end_time = models.CharField(max_length=5, help_text='UTC end time as "HH:MM"')
    slot_duration = models.PositiveSmallIntegerField(
        help_text="Minutes per time slot (15, 30, or 60)",
    )
    grid_timezone = models.CharField(
        max_length=50,
        default="UTC",
        help_text="IANA timezone used when creating this grid",
    )
    blocked_cells = models.JSONField(
        default=list,
        blank=True,
        help_text='List of blocked cells, each {"date": "YYYY-MM-DD", "time": "HH:MM"}',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text="Grid lifecycle status",
    )
    max_races_question = models.BooleanField(
        default=False,
        help_text="Ask responders: What is the max number of races you would like to do?",
    )
    rest_days_question = models.BooleanField(
        default=False,
        help_text="Ask responders: How many days rest between races do you require?",
    )
    hide_empty_days = models.BooleanField(
        default=False,
        help_text="Hide days where every time slot is blocked from the response/results grids",
    )
    require_race_verified_availability = models.BooleanField(
        default=False,
        help_text=(
            "Require Race Verified status before a member can submit this grid. "
            "Forced on when the parent event requires it."
        ),
    )
    expires = models.DateField(null=True, blank=True, help_text="Date when this grid expires and is no longer visible")

    # Presentation only. A single-slot grid is an ordinary grid whose time axis has one
    # row: end_date == start_date and end_time == start_time + slot_duration. Every
    # consumer -- responses, the v-report, scheduled races -- sees the same shape it
    # always did, so this flag only decides how the builder collects it and how the
    # response form renders it.
    single_slot = models.BooleanField(
        default=False,
        help_text="One date and time rather than a grid; riders answer available yes/no",
    )

    # Optional context shown to riders alongside the grid. Gated by expanded_features
    # rather than by "is any of them non-empty": a captain who fills these in and then
    # decides against showing them can untick the box without losing the text, and can
    # tick it back on next season. Blank-means-hidden would force them to delete it.
    expanded_features = models.BooleanField(
        default=False,
        help_text="Show the description and links below to riders filling in this grid",
    )
    description = models.TextField(
        blank=True,
        help_text="Markdown shown above the grid. Captain-authored, so markdown is rendered.",
    )
    website_url = models.URLField(max_length=500, blank=True, help_text="Event website")
    course_url = models.URLField(max_length=500, blank=True, help_text="Course or route details")
    recon_url = models.URLField(max_length=500, blank=True, help_text="Event recon (ride-through, preview)")
    invite_url = models.URLField(max_length=500, blank=True, help_text="Event invite / sign-up link")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_availability_grids",
        help_text="User who created this grid",
    )
    created_at = models.DateTimeField(default=timezone.now, help_text="When the grid was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the grid was last updated")

    class Meta:
        """Meta options for AvailabilityGrid model."""

        ordering = ["-created_at"]  # noqa: RUF012
        verbose_name = "Availability Grid"
        verbose_name_plural = "Availability Grids"

    def __str__(self) -> str:
        """Return squad and title description.

        Returns:
            String in format "Squad - Title" or "Squad - Availability Grid".

        """
        return f"{self.squad} - {self.title or 'Availability Grid'}"

    def save(self, *args, **kwargs) -> None:
        """Auto-generate title if blank, then save.

        Args:
            *args: Positional arguments passed to Model.save().
            **kwargs: Keyword arguments passed to Model.save().

        """
        if not self.title:
            # A single-day grid would otherwise read "... 2026-10-27 - 2026-10-27".
            span = (
                str(self.start_date)
                if self.start_date == self.end_date
                else f"{self.start_date} - {self.end_date}"
            )
            self.title = f"{self.squad.event.title} {self.squad.name} {span}"
        super().save(*args, **kwargs)

    @property
    def require_race_verified_effective(self) -> bool:
        """Whether Race Verified status is required to submit this grid.

        Combines the grid's own setting with the parent event's floor: the event
        requirement forces the grid requirement on even if the grid was saved
        before the event toggle was enabled.

        Returns:
            True when either the grid or its event requires Race Verified status.

        """
        return bool(self.squad.event.require_race_verified_availability or self.require_race_verified_availability)

    @property
    def dates(self) -> list[str]:
        """List of date strings from start_date to end_date.

        Returns:
            List of "YYYY-MM-DD" strings for each day in the grid range.

        """
        result = []
        current = self.start_date
        while current <= self.end_date:
            result.append(current.isoformat())
            current += timedelta(days=1)
        return result

    @property
    def response_count(self) -> int:
        """The number of responses for this grid.

        Returns:
            Count of AvailabilityResponse objects linked to this grid.

        """
        return self.responses.count()

    @property
    def is_draft(self) -> bool:
        """Check if grid is in draft status.

        Returns:
            True if status is draft.

        """
        return self.status == self.Status.DRAFT

    @property
    def is_published(self) -> bool:
        """Check if grid is published.

        Returns:
            True if status is published.

        """
        return self.status == self.Status.PUBLISHED

    @property
    def is_closed(self) -> bool:
        """Check if grid is closed.

        Returns:
            True if status is closed.

        """
        return self.status == self.Status.CLOSED

    @property
    def next_period_start_date(self) -> date:
        """Default start date for a copy of this grid: shifted by the grid's length.

        For a 7-day grid this lands on the day after ``end_date`` — i.e. the start
        of the next equivalent period.

        Returns:
            Suggested start date for a copy.

        """
        return self.start_date + timedelta(days=(self.end_date - self.start_date).days + 1)

    @property
    def next_period_end_date(self) -> date:
        """Default end date for a copy of this grid: shifted by the grid's length.

        Returns:
            Suggested end date for a copy.

        """
        return self.end_date + timedelta(days=(self.end_date - self.start_date).days + 1)


class AvailabilityResponse(models.Model):
    """A single member's availability selections for an availability grid.

    Each user can submit one response per grid. Submitting again overwrites
    the previous response.

    Attributes:
        grid: The availability grid this response belongs to.
        user: The responding member.
        available_cells: JSON list of cells the user marked as available.
        created_at: When the response was created.
        updated_at: When the response was last modified.

    """

    grid = models.ForeignKey(
        AvailabilityGrid,
        on_delete=models.CASCADE,
        related_name="responses",
        help_text="The availability grid this response belongs to",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="availability_responses",
        help_text="The responding member",
    )
    available_cells = models.JSONField(
        default=list,
        blank=True,
        help_text='List of available cells, each {"date": "YYYY-MM-DD", "time": "HH:MM"}',
    )
    max_races = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Max number of races the responder wants to do",
    )
    rest_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Number of rest days required between races",
    )
    created_at = models.DateTimeField(default=timezone.now, help_text="When the response was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the response was last updated")

    class Meta:
        """Meta options for AvailabilityResponse model."""

        ordering = ["user__first_name", "user__last_name"]  # noqa: RUF012
        unique_together = [("grid", "user")]  # noqa: RUF012
        verbose_name = "Availability Response"
        verbose_name_plural = "Availability Responses"

    def __str__(self) -> str:
        """Return user and grid description.

        Returns:
            String in format "User - Grid".

        """
        return f"{self.user} - {self.grid}"


class AvailabilitySlotSelection(models.Model):
    """A named selection of riders for a specific date/time slot in an availability grid.

    Event admins create these from the results heatmap to plan races.
    Stores UTC coordinates consistent with AvailabilityResponse.available_cells.

    Attributes:
        grid: The availability grid this selection belongs to.
        name: Display name for this slot (e.g., "Race 1").
        slot_date: UTC date of the selected cell.
        slot_time: UTC time of the selected cell as "HH:MM".
        selected_users: Users selected for this slot.
        created_by: User who created this selection.
        created_at: When the selection was created.
        updated_at: When the selection was last modified.

    """

    class Status(models.TextChoices):
        """Scheduling status for a named slot."""

        NONE = "none", "None"
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"

    grid = models.ForeignKey(
        AvailabilityGrid,
        on_delete=models.CASCADE,
        related_name="slot_selections",
        help_text="The availability grid this selection belongs to",
    )
    name = models.CharField(max_length=200, help_text="Display name for this slot selection")
    slot_date = models.DateField(help_text="UTC date of the selected cell")
    slot_time = models.CharField(max_length=5, help_text='UTC time as "HH:MM"')
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.NONE,
        help_text="Scheduling status (none, pending, confirmed)",
    )
    opponent = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Optional opponent team or rider name for this scheduled race",
    )
    event_invite_url = models.URLField(
        blank=True,
        default="",
        help_text="Optional invite link to the scheduled event/race",
    )
    course_url = models.URLField(
        blank=True,
        default="",
        help_text="Optional link to the course/route page",
    )
    laps = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional number of laps for this race",
    )
    custom_finish_km = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional custom finish distance in km (e.g. 13.5)",
    )
    powerups = models.JSONField(
        default=list,
        blank=True,
        help_text="Optional list of enabled Zwift power-up slugs (ttt_planner.PowerUp.slug values)",
    )
    thread_link = models.URLField(
        blank=True,
        default="",
        help_text="Optional URL to a Discord thread for this race",
    )
    selected_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="availability_selections",
        help_text="Users selected for this slot",
    )
    substitutes = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="substitute_slot_selections",
        help_text="Optional substitute riders available to step in",
    )
    directeurs_sportifs = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="SlotDS",
        through_fields=("selection", "user"),
        related_name="directing_slot_selections",
        help_text="Team members helping with this race (DS); given the squad role while assigned",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_slot_selections",
        help_text="User who created this selection",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the selection was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the selection was last modified")

    class Meta:
        """Meta options for AvailabilitySlotSelection model."""

        ordering = ["slot_date", "slot_time"]  # noqa: RUF012
        unique_together = [("grid", "slot_date", "slot_time")]  # noqa: RUF012
        verbose_name = "Availability Slot Selection"
        verbose_name_plural = "Availability Slot Selections"

    def __str__(self) -> str:
        """Return name and slot description.

        Returns:
            String in format "Name (date time)".

        """
        return f"{self.name} ({self.slot_date} {self.slot_time})"

    @property
    def race_datetime_utc(self) -> datetime:
        """The race start as a tz-aware UTC datetime (combining slot_date + slot_time)."""
        try:
            slot_t = time.fromisoformat(self.slot_time)
        except ValueError:
            slot_t = time(0, 0)
        return datetime.combine(self.slot_date, slot_t, tzinfo=UTC)

    @property
    def powerup_objects(self) -> list:
        """The enabled ``PowerUp`` records, in their configured display order.

        Resolves the stored slugs against ``ttt_planner.PowerUp``. Unknown slugs
        (e.g. a power-up later deleted) are skipped.

        Returns:
            A list of ``PowerUp`` instances.

        """
        slugs = self.powerups or []
        if not slugs:
            return []
        from apps.ttt_planner.models import PowerUp  # local import avoids a cross-app import cycle

        return list(PowerUp.objects.filter(slug__in=slugs))

    @property
    def powerup_labels(self) -> list[str]:
        """The enabled power-up names, in their configured display order."""
        return [p.name for p in self.powerup_objects]


class SlotDS(models.Model):
    """Through model linking a Directeur Sportif (DS) to a scheduled race.

    A DS is any team member helping with a race. When added they are given the squad's
    Discord role (``team_discord_role``); a daily sweep removes it after the race. We only
    remove what we assigned, and never strip a role the user holds for another reason.

    Attributes:
        selection: The scheduled race (slot selection).
        user: The DS team member.
        role_was_assigned: True only if we actually added the squad role (they didn't already hold it).
        role_removed_at: When the role was removed/handled; null while still assigned.
        added_by: Admin who added the DS.
        added_at: When the DS was added.

    """

    selection = models.ForeignKey(
        AvailabilitySlotSelection,
        on_delete=models.CASCADE,
        related_name="ds_assignments",
        help_text="The scheduled race this DS helps with",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ds_assignments",
        help_text="The Directeur Sportif",
    )
    role_was_assigned = models.BooleanField(
        default=False,
        help_text="Whether we added the squad role (only then do we remove it later)",
    )
    role_removed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the squad role was removed/handled for this DS (null while active)",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_ds_assignments",
        help_text="Admin who added this DS",
    )
    added_at = models.DateTimeField(default=timezone.now, help_text="When the DS was added")

    class Meta:
        """Meta options for SlotDS model."""

        ordering = ["added_at"]  # noqa: RUF012
        unique_together = [("selection", "user")]  # noqa: RUF012
        verbose_name = "Slot Directeur Sportif"
        verbose_name_plural = "Slot Directeurs Sportifs"

    def __str__(self) -> str:
        """Return DS and race description.

        Returns:
            String in format "DS <user> for <race>".

        """
        return f"DS {self.user} for {self.selection}"


class AvailabilityGridTemplate(models.Model):
    """A reusable, per-squad availability-grid configuration.

    Stores the date-independent shape of a grid (times, slot size, timezone, and the
    optional questions) so captains can spin up a new draft grid for the squad without
    rebuilding the configuration each time. Times are stored as **local** "HH:MM" in
    ``timezone``; the apply flow converts them to UTC for the chosen dates so DST is
    handled correctly. Blocked cells are not stored (set fresh per grid).

    Attributes:
        squad: The squad this template belongs to.
        name: Library label shown in the template picker.
        start_time: Local start time as "HH:MM".
        end_time: Local end time as "HH:MM".
        grid_timezone: IANA timezone the times are expressed in.
        slot_duration: Minutes per slot (15, 30, or 60).
        default_length_days: Number of days a grid spans; used to derive end date on apply.
        max_races_question: Carry the "max races" question onto created grids.
        rest_days_question: Carry the "rest days" question onto created grids.
        created_by: User who created this template.
        created_at: When the template was created.
        updated_at: When the template was last modified.

    """

    squad = models.ForeignKey(
        Squad,
        on_delete=models.CASCADE,
        related_name="availability_templates",
        help_text="The squad this template belongs to",
    )
    name = models.CharField(max_length=200, help_text="Label shown in the template picker")
    start_time = models.CharField(max_length=5, help_text='Local start time as "HH:MM"')
    end_time = models.CharField(max_length=5, help_text='Local end time as "HH:MM"')
    grid_timezone = models.CharField(
        max_length=50,
        default="UTC",
        help_text="IANA timezone the times are expressed in",
    )
    slot_duration = models.PositiveSmallIntegerField(help_text="Minutes per time slot (15, 30, or 60)")
    default_length_days = models.PositiveSmallIntegerField(
        default=7,
        help_text="Number of days a created grid spans (used to derive the end date on apply)",
    )
    max_races_question = models.BooleanField(
        default=False,
        help_text="Carry the max-races question onto grids created from this template",
    )
    # A template is meant to be a whole starting point, so it carries every grid
    # setting the builder can produce -- not just the times. Anything omitted here is
    # silently dropped when the template is applied, which reads as the setting having
    # been forgotten rather than never saved.
    hide_empty_days = models.BooleanField(
        default=False,
        help_text="Hide days with no available times on grids made from this template",
    )
    single_slot = models.BooleanField(
        default=False,
        help_text="Grids from this template are a single date and time",
    )
    expanded_features = models.BooleanField(
        default=False,
        help_text="Show the description and links below on grids made from this template",
    )
    description = models.TextField(blank=True, help_text="Markdown shown above the grid")
    website_url = models.URLField(max_length=500, blank=True, help_text="Event website")
    course_url = models.URLField(max_length=500, blank=True, help_text="Course or route details")
    recon_url = models.URLField(max_length=500, blank=True, help_text="Event recon")
    invite_url = models.URLField(max_length=500, blank=True, help_text="Event invite / sign-up link")

    shared = models.BooleanField(
        default=False,
        help_text=(
            "Offer this template to every squad, not just this one. Shared templates "
            "carry the times and timezone they were built with, so the squad using one "
            "is told to check both."
        ),
    )
    rest_days_question = models.BooleanField(
        default=False,
        help_text="Carry the rest-days question onto grids created from this template",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_availability_templates",
        help_text="User who created this template",
    )
    created_at = models.DateTimeField(default=timezone.now, help_text="When the template was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the template was last updated")

    class Meta:
        """Meta options for AvailabilityGridTemplate model."""

        ordering = ["name"]  # noqa: RUF012
        verbose_name = "Availability Grid Template"
        verbose_name_plural = "Availability Grid Templates"

    def __str__(self) -> str:
        """Return squad and template name.

        Returns:
            String in format "Squad - Name".

        """
        return f"{self.squad} - {self.name}"

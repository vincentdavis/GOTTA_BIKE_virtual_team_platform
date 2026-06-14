"""Models for events app."""

import uuid
from datetime import date, timedelta

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
    squad_gender_options = models.JSONField(
        default=_default_squad_gender_options,
        blank=True,
        help_text="Squad gender preference options available at signup",
    )
    squad_gender_required = models.BooleanField(
        default=False,
        help_text="Require squad gender preference at signup (also gates whether the field appears)",
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
    min_zwift_category = models.CharField(max_length=20, blank=True, help_text="Minimum Zwift category (e.g., A, B, C)")
    max_zwift_category = models.CharField(max_length=20, blank=True, help_text="Maximum Zwift category (e.g., A, B, C)")
    min_womens_zwift_category = models.CharField(
        max_length=20, blank=True, help_text="Minimum women's Zwift category (e.g., A, B, C)"
    )
    max_womens_zwift_category = models.CharField(
        max_length=20, blank=True, help_text="Maximum women's Zwift category (e.g., A, B, C)"
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
        help_text="Block adding a rider weaker than the minimum Zwift Racing category",
    )
    enforce_max_zwift_racing_category = models.BooleanField(
        default=False,
        help_text="Block adding a rider stronger than the maximum Zwift Racing category",
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
        """Return the set of captain user PKs (uses prefetched ``captains`` when available)."""
        return {u.pk for u in self.captains.all()}

    @property
    def vice_captain_pks(self) -> set[int]:
        """Return the set of vice-captain user PKs (uses prefetched ``vice_captains`` when available)."""
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
        """Return a human description of the enforced ZR category bounds (empty if none enforced).

        ZR tiers rank Diamond (strongest) to Copper (weakest). ``min_zwift_racing_category`` is the
        weakest tier allowed and ``max_zwift_racing_category`` is the strongest tier allowed.
        """
        enforce_min = self.enforce_min_zwift_racing_category and self.min_zwift_racing_category
        enforce_max = self.enforce_max_zwift_racing_category and self.max_zwift_racing_category
        if enforce_min and enforce_max:
            return f"{self.max_zwift_racing_category} to {self.min_zwift_racing_category}"
        if enforce_max:
            return f"{self.max_zwift_racing_category} or weaker"
        if enforce_min:
            return f"{self.min_zwift_racing_category} or stronger"
        return ""

    def check_zr_eligibility(self, zr_category: str) -> tuple[bool, str]:
        """Check a rider's ZR category against this squad's enforced bounds.

        ZR tiers rank Diamond (strongest) to Copper (weakest). A rider must be no stronger than
        ``max_zwift_racing_category`` and no weaker than ``min_zwift_racing_category`` for the bounds
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

        order = ZR_CATEGORY_ORDER  # index 0 = Diamond (strongest) ... index 9 = Copper (weakest)
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
    expires = models.DateField(null=True, blank=True, help_text="Date when this grid expires and is no longer visible")
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
            self.title = f"{self.squad.event.title} {self.squad.name} {self.start_date} - {self.end_date}"
        super().save(*args, **kwargs)

    @property
    def dates(self) -> list[str]:
        """Return list of date strings from start_date to end_date.

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
        """Return the number of responses for this grid.

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

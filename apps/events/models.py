"""Models for events app."""

import uuid
from datetime import datetime, timedelta

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


def _default_timezone_options() -> list[str]:
    """Return a copy of the default timezone options list.

    Returns:
        List of default timezone option strings.

    """
    return list(DEFAULT_TIMEZONE_OPTIONS)


class Event(models.Model):
    """A team event such as a race series, time trial, or club ride.

    Attributes:
        title: Display name for the event.
        description: Longer description of the event (supports Markdown).
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

    prefix = models.CharField(max_length=50, blank=True, help_text='Channel/role prefix, e.g. "$" in $DRS')
    title = models.CharField(max_length=200, help_text="Event title")
    description = models.TextField(blank=True, help_text="Event description (supports Markdown)")
    start_date = models.DateField(help_text="Event start date")
    end_date = models.DateField(help_text="Event end date")
    visible = models.BooleanField(default=True, help_text="Whether the event is visible to team members")
    signups_open = models.BooleanField(default=False, help_text="Whether signups are currently open")
    signup_instructions = models.TextField(blank=True, help_text="Instructions shown at the top of the signup form")
    timezone_options = models.JSONField(
        default=_default_timezone_options,
        blank=True,
        help_text="Timezone options available at signup",
    )
    timezone_required = models.BooleanField(default=False, help_text="Whether timezone selection is required at signup")
    logo = models.ImageField(upload_to="event_logos/", blank=True, help_text="Optional logo image for the event")
    url = models.URLField(max_length=500, blank=True, help_text="External URL for event details or signup")
    discord_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord channel ID for event coordination (0 = none)",
    )
    head_captain_role_id = models.BigIntegerField(
        default=0,
        help_text="Discord role ID for the head captain of this event (0 = none)",
    )
    event_role = models.BigIntegerField(
        default=0,
        help_text="Discord role ID for the event (0 = none)",
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


class EventRegistration(models.Model):
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
        related_name="event_registrations",
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
        """Meta options for EventRegistration model."""

        ordering = ["-created_at"]  # noqa: RUF012
        unique_together = [("user", "race")]  # noqa: RUF012
        verbose_name = "Event Registration"
        verbose_name_plural = "Event Registrations"

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
        min_zwift_category: Minimum Zwift category letter.
        max_zwift_category: Maximum Zwift category letter.
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
    discord_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord channel ID for squad coordination (0 = none)",
    )
    audio_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord voice/stage channel ID for squad audio (0 = none)",
    )
    captain = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captain_squads",
        help_text="Squad captain",
    )
    vice_captain = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vice_captain_squads",
        help_text="Squad vice captain",
    )
    team_discord_role = models.BigIntegerField(
        default=0,
        help_text="Discord role ID for the squad (0 = none)",
    )
    min_zwift_category = models.CharField(max_length=20, blank=True, help_text="Minimum Zwift category (e.g., A, B, C)")
    max_zwift_category = models.CharField(max_length=20, blank=True, help_text="Maximum Zwift category (e.g., A, B, C)")
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
    url = models.URLField(max_length=500, blank=True, help_text="External URL for squad details")
    invite_url = models.URLField(max_length=500, blank=True, help_text="Invite URL for joining the squad")
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
    def time_slots(self) -> list[str]:
        """Return list of time slot strings from start_time to end_time by slot_duration.

        Returns:
            List of "HH:MM" strings for each slot in the time range.

        """
        start = datetime.strptime(self.start_time, "%H:%M")
        end = datetime.strptime(self.end_time, "%H:%M")
        delta = timedelta(minutes=self.slot_duration)
        result = []
        current = start
        while current < end:
            result.append(current.strftime("%H:%M"))
            current += delta
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

"""Models for events app."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class Event(models.Model):
    """A team event such as a race series, time trial, or club ride.

    Attributes:
        title: Display name for the event.
        description: Longer description of the event (supports Markdown).
        start_date: When the event starts.
        end_date: When the event ends.
        visible: Whether the event is visible to team members.
        url: External URL for event details or signup.
        created_at: When the record was created.
        updated_at: When the record was last modified.
        created_by: User who created the event.

    """

    title = models.CharField(max_length=200, help_text="Event title")
    description = models.TextField(blank=True, help_text="Event description (supports Markdown)")
    start_date = models.DateTimeField(help_text="Event start date and time")
    end_date = models.DateTimeField(help_text="Event end date and time")
    visible = models.BooleanField(default=True, help_text="Whether the event is visible to team members")
    url = models.URLField(max_length=500, blank=True, help_text="External URL for event details or signup")
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

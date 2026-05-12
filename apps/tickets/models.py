"""Models for the tickets app."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class Ticket(models.Model):
    """Member support and team management ticket.

    Tickets are raised by team members for support requests, equipment
    issues, verification disputes, and other team-management topics.
    Admins triage, assign, and resolve them.

    Attributes:
        title: Short summary of the issue.
        details: Full description (supports Markdown).
        status: Workflow state.
        category: Coarse topic for the ticket queue filter.
        priority: Triage priority.
        submitted_by: User who raised the ticket.
        assigned_to: User currently handling the ticket.
        closed_by: User who closed the ticket.
        resolution: Closing note shown on resolved tickets.
        created_at: When the ticket was created.
        updated_at: When the ticket was last modified.
        closed_at: When the ticket was closed (cleared if reopened).

    """

    class Status(models.TextChoices):
        """Ticket status choices."""

        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In Progress"
        CLOSED = "closed", "Closed"

    class Category(models.TextChoices):
        """Ticket category choices."""

        SUPPORT = "support", "Member Support"
        MEMBERSHIP = "membership", "Membership"
        VERIFICATION = "verification", "Verification"
        EQUIPMENT = "equipment", "Equipment"
        DISCORD = "discord", "Discord"
        EVENT = "event", "Event"
        SQUAD = "squad", "Squad"
        OTHER = "other", "Other"

    class Priority(models.TextChoices):
        """Ticket priority choices."""

        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    title = models.CharField(max_length=200, help_text="Short summary of the issue")
    details = models.TextField(help_text="Full description (supports Markdown)")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
        help_text="Workflow state",
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.SUPPORT,
        help_text="Coarse topic for the ticket queue filter",
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.NORMAL,
        help_text="Triage priority",
    )

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_tickets",
        help_text="User who raised the ticket (null for system-generated tickets)",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
        help_text="User currently handling the ticket",
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_tickets",
        help_text="User who closed the ticket",
    )

    guild_member = models.ForeignKey(
        "accounts.GuildMember",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="related_tickets",
        help_text="Discord guild member this ticket is about, for system-generated membership tickets",
    )

    resolution = models.TextField(blank=True, help_text="Closing note shown on resolved tickets")

    created_at = models.DateTimeField(default=timezone.now, help_text="When the ticket was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the ticket was last modified")
    closed_at = models.DateTimeField(null=True, blank=True, help_text="When the ticket was closed")

    class Meta:
        """Meta options for Ticket model."""

        ordering = ["-created_at"]  # noqa: RUF012
        verbose_name = "Ticket"
        verbose_name_plural = "Tickets"
        indexes = [  # noqa: RUF012
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self) -> str:
        """Return short ticket description.

        Returns:
            String in format "#{pk} {title}".

        """
        return f"#{self.pk} {self.title}"

    def save(self, *args, **kwargs) -> None:
        """Maintain ``closed_at`` automatically when status transitions to or from CLOSED.

        Closing without ``closed_at`` set stamps the current time; reopening a
        previously closed ticket clears the timestamp. ``closed_by`` is managed
        in the views since it needs the acting user.

        Args:
            *args: Positional arguments passed to super().save().
            **kwargs: Keyword arguments passed to super().save().

        """
        if self.status == self.Status.CLOSED and self.closed_at is None:
            self.closed_at = timezone.now()
        elif self.status != self.Status.CLOSED and self.closed_at is not None:
            self.closed_at = None
        super().save(*args, **kwargs)

"""Custom user model for accounts app."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from django.contrib.auth.models import AbstractUser
from django.db import models

if TYPE_CHECKING:
    from django.db.models.manager import RelatedManager

    from apps.team.models import RaceReadyRecord


class UserRoles:
    """Constants for user roles."""

    APP_ADMIN = "app_admin"
    LINK_ADMIN = "link_admin"
    MEMBERSHIP_ADMIN = "membership_admin"
    RACING_ADMIN = "racing_admin"
    TEAM_CAPTAIN = "team_captain"
    TEAM_VICE_CAPTAIN = "team_vice_captain"
    TEAM_MEMBER = "team_member"

    CHOICES: ClassVar[list[tuple[str, str]]] = [
        (APP_ADMIN, "App Admin"),
        (LINK_ADMIN, "Link Admin"),
        (MEMBERSHIP_ADMIN, "Membership Admin"),
        (RACING_ADMIN, "Racing Admin"),
        (TEAM_CAPTAIN, "Team Captain"),
        (TEAM_VICE_CAPTAIN, "Team Vice Captain"),
        (TEAM_MEMBER, "Team Member"),
    ]

    ALL: ClassVar[list[str]] = [choice[0] for choice in CHOICES]


class User(AbstractUser):
    """Custom user model with Discord and Zwift integration fields."""

    # Type hints for reverse relations from RaceReadyRecord
    race_ready_records: RelatedManager[RaceReadyRecord]
    reviewed_records: RelatedManager[RaceReadyRecord]

    class Gender(models.TextChoices):
        """Gender choices."""

        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"

    # Discord integration
    discord_id = models.CharField(
        max_length=20,
        blank=True,
        help_text="Discord user ID (snowflake)",
    )
    discord_username = models.CharField(
        max_length=100,
        blank=True,
        help_text="Discord username",
    )
    discord_nickname = models.CharField(
        max_length=100,
        blank=True,
        help_text="Discord server nickname",
    )
    discord_avatar = models.CharField(
        max_length=100,
        blank=True,
        help_text="Discord avatar hash",
    )
    discord_roles = models.JSONField(
        default=dict,
        blank=True,
        help_text="Discord roles as {role_id: role_name}",
    )

    # Zwift integration
    zwid = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Zwift user ID",
    )
    zwid_verified = models.BooleanField(
        default=False,
        help_text="Zwift user ID has been verified",
    )

    # Personal info
    birth_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Year of birth (e.g., 1990)",
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        help_text="City of residence",
    )
    country = models.CharField(
        max_length=100,
        blank=True,
        help_text="Country of residence",
    )
    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        blank=True,
        help_text="Gender",
    )
    timezone = models.CharField(
        max_length=50,
        blank=True,
        help_text="User's timezone (e.g., America/New_York)",
    )
    youtube_channel = models.URLField(
        max_length=200,
        blank=True,
        help_text="YouTube channel URL",
    )

    # Roles
    roles = models.JSONField(
        default=list,
        blank=True,
        help_text="List of user roles",
    )

    class Meta:
        """Meta options for User model."""

        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self) -> str:
        """Return string representation of user.

        Returns:
            The username.

        """
        return f"{self.username}, Discord: {self.discord_username}"

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role.

        Args:
            role: The role to check for.

        Returns:
            True if user has the role, False otherwise.

        """
        return role in (self.roles or [])

    def add_role(self, role: str) -> bool:
        """Add a role to the user.

        Args:
            role: The role to add (must be a valid role from UserRoles.ALL).

        Returns:
            True if role was added, False if already present or invalid.

        """
        if role not in UserRoles.ALL:
            return False
        if self.roles is None:
            self.roles = []
        if role not in self.roles:
            self.roles.append(role)
            return True
        return False

    def remove_role(self, role: str) -> bool:
        """Remove a role from the user.

        Args:
            role: The role to remove.

        Returns:
            True if role was removed, False if not present.

        """
        if self.roles and role in self.roles:
            self.roles.remove(role)
            return True
        return False

    @property
    def is_app_admin(self) -> bool:
        """Check if user is an app admin."""
        return self.has_role(UserRoles.APP_ADMIN)

    @property
    def is_link_admin(self) -> bool:
        """Check if user is a link admin."""
        return self.has_role(UserRoles.LINK_ADMIN)

    @property
    def is_membership_admin(self) -> bool:
        """Check if user is a membership admin."""
        return self.has_role(UserRoles.MEMBERSHIP_ADMIN)

    @property
    def is_racing_admin(self) -> bool:
        """Check if user is a racing admin."""
        return self.has_role(UserRoles.RACING_ADMIN)

    @property
    def is_team_captain(self) -> bool:
        """Check if user is a team captain."""
        return self.has_role(UserRoles.TEAM_CAPTAIN)

    @property
    def is_team_vice_captain(self) -> bool:
        """Check if user is a team vice captain."""
        return self.has_role(UserRoles.TEAM_VICE_CAPTAIN)

    @property
    def is_team_member(self) -> bool:
        """Check if user is a team member."""
        return self.has_role(UserRoles.TEAM_MEMBER)

    @property
    def is_any_admin(self) -> bool:
        """Check if user has any admin role."""
        return self.is_app_admin or self.is_link_admin or self.is_membership_admin or self.is_racing_admin

    @property
    def is_any_captain(self) -> bool:
        """Check if user is captain or vice captain."""
        return self.is_team_captain or self.is_team_vice_captain

    @property
    def discord_avatar_url(self) -> str | None:
        """Get the full Discord avatar URL.

        Returns:
            The Discord CDN URL for the avatar, or None if no avatar.

        """
        if self.discord_id and self.discord_avatar:
            return f"https://cdn.discordapp.com/avatars/{self.discord_id}/{self.discord_avatar}.png"
        return None

    def has_discord_role(self, role_id: int | str) -> bool:
        """Check if user has a Discord role.

        Args:
            role_id: The Discord role ID to check.

        Returns:
            True if user has the role, False otherwise.

        """
        return str(role_id) in (self.discord_roles or {})

    def get_discord_role_ids(self) -> list[int]:
        """Get list of Discord role IDs.

        Returns:
            List of role IDs as integers.

        """
        return [int(rid) for rid in (self.discord_roles or {})]

    def get_discord_role_names(self) -> list[str]:
        """Get list of Discord role names.

        Returns:
            List of role names.

        """
        return list((self.discord_roles or {}).values())


class GuildMember(models.Model):
    """Discord guild member data synced from bot.

    Tracks all members of the Discord guild, including those who don't have
    a Django User account. Used to compare guild membership with app users.
    """

    discord_id = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text="Discord user ID (snowflake)",
    )
    username = models.CharField(
        max_length=100,
        help_text="Discord username",
    )
    display_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Discord global display name",
    )
    nickname = models.CharField(
        max_length=100,
        blank=True,
        help_text="Server-specific nickname",
    )
    avatar_hash = models.CharField(
        max_length=100,
        blank=True,
        help_text="Discord avatar hash for CDN URL",
    )
    roles = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Discord role IDs",
    )
    joined_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the member joined the guild",
    )
    is_bot = models.BooleanField(
        default=False,
        help_text="Whether this member is a bot",
    )

    # Tracking fields
    date_created = models.DateTimeField(
        auto_now_add=True,
        help_text="When this record was created",
    )
    date_modified = models.DateTimeField(
        auto_now=True,
        help_text="When this record was last updated",
    )
    date_left = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the member left the guild (null if still present)",
    )

    # Link to User if exists
    user = models.OneToOneField(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="guild_member",
        help_text="Linked Django User account",
    )

    class Meta:
        """Meta options for GuildMember model."""

        verbose_name = "Guild Member"
        verbose_name_plural = "Guild Members"
        ordering = ["-date_modified"]  # noqa: RUF012

    def __str__(self) -> str:
        """Return string representation.

        Returns:
            Display name or username.

        """
        name = self.nickname or self.display_name or self.username
        return f"{name} ({self.discord_id})"

    @property
    def avatar_url(self) -> str | None:
        """Get the full Discord avatar URL.

        Returns:
            The Discord CDN URL for the avatar, or None if no avatar.

        """
        if self.avatar_hash:
            return f"https://cdn.discordapp.com/avatars/{self.discord_id}/{self.avatar_hash}.png"
        return None

    @property
    def is_active(self) -> bool:
        """Check if member is still in the guild.

        Returns:
            True if date_left is None, False otherwise.

        """
        return self.date_left is None

    @property
    def has_user_account(self) -> bool:
        """Check if member has a linked User account.

        Returns:
            True if user is linked, False otherwise.

        """
        return self.user is not None

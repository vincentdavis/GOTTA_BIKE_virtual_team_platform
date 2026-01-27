"""Custom user model for accounts app."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import logfire
from django.contrib.auth.models import AbstractUser
from django.db import models
from django_countries.fields import CountryField

if TYPE_CHECKING:
    from django.db.models.manager import RelatedManager

    from apps.team.models import RaceReadyRecord


class UserRoles:
    """Constants for user roles (legacy - use Permissions for new code)."""

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


class Permissions:
    """Constants for permission names and Constance mappings.

    Permissions are checked via Discord roles configured in Constance.
    Each permission maps to a Constance setting containing a JSON array
    of Discord role IDs that grant that permission.
    """

    APP_ADMIN = "app_admin"
    TEAM_CAPTAIN = "team_captain"
    VICE_CAPTAIN = "vice_captain"
    LINK_ADMIN = "link_admin"
    MEMBERSHIP_ADMIN = "membership_admin"
    RACING_ADMIN = "racing_admin"
    TEAM_MEMBER = "team_member"
    RACE_READY = "race_ready"
    APPROVE_VERIFICATION = "approve_verification"
    DATA_CONNECTION = "data_connection"
    PAGES_ADMIN = "pages_admin"

    # Map permission names to Constance config keys
    CONSTANCE_MAP: ClassVar[dict[str, str]] = {
        APP_ADMIN: "PERM_APP_ADMIN_ROLES",
        TEAM_CAPTAIN: "PERM_TEAM_CAPTAIN_ROLES",
        VICE_CAPTAIN: "PERM_VICE_CAPTAIN_ROLES",
        LINK_ADMIN: "PERM_LINK_ADMIN_ROLES",
        MEMBERSHIP_ADMIN: "PERM_MEMBERSHIP_ADMIN_ROLES",
        RACING_ADMIN: "PERM_RACING_ADMIN_ROLES",
        TEAM_MEMBER: "PERM_TEAM_MEMBER_ROLES",
        RACE_READY: "PERM_RACE_READY_ROLES",
        APPROVE_VERIFICATION: "PERM_APPROVE_VERIFICATION_ROLES",
        DATA_CONNECTION: "PERM_DATA_CONNECTION_ROLES",
        PAGES_ADMIN: "PERM_PAGES_ADMIN_ROLES",
    }

    CHOICES: ClassVar[list[tuple[str, str]]] = [
        (APP_ADMIN, "App Admin"),
        (TEAM_CAPTAIN, "Team Captain"),
        (VICE_CAPTAIN, "Vice Captain"),
        (LINK_ADMIN, "Link Admin"),
        (MEMBERSHIP_ADMIN, "Membership Admin"),
        (RACING_ADMIN, "Racing Admin"),
        (TEAM_MEMBER, "Team Member"),
        (RACE_READY, "Race Ready"),
        (APPROVE_VERIFICATION, "Approve Verification"),
        (DATA_CONNECTION, "Data Connection"),
        (PAGES_ADMIN, "Pages Admin"),
    ]

    ALL: ClassVar[list[str]] = list(CONSTANCE_MAP.keys())


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

    class UnitPreference(models.TextChoices):
        """Unit preference choices for weight and height."""

        METRIC = "metric", "Metric (kg, cm)"
        IMPERIAL = "imperial", "Imperial (lbs, in)"

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
    country = CountryField(
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
    unit_preference = models.CharField(
        max_length=10,
        choices=UnitPreference.choices,
        default=UnitPreference.METRIC,
        help_text="Preferred unit system for weight and height",
    )

    # Social Accounts
    youtube_channel = models.URLField(
        max_length=200,
        blank=True,
        help_text="YouTube channel URL",
    )
    twitch_channel = models.URLField(
        max_length=200,
        blank=True,
        help_text="Twitch channel URL",
    )
    instagram_url = models.URLField(
        max_length=200,
        blank=True,
        help_text="Instagram profile URL",
    )
    facebook_url = models.URLField(
        max_length=200,
        blank=True,
        help_text="Facebook profile URL",
    )
    twitter_url = models.URLField(
        max_length=200,
        blank=True,
        help_text="Twitter/X profile URL",
    )
    tiktok_url = models.URLField(
        max_length=200,
        blank=True,
        help_text="TikTok profile URL",
    )
    bluesky_url = models.URLField(
        max_length=200,
        blank=True,
        help_text="BlueSky profile URL",
    )
    mastodon_url = models.URLField(
        max_length=200,
        blank=True,
        help_text="Mastodon profile URL",
    )
    strava_url = models.URLField(
        max_length=200,
        blank=True,
        help_text="Strava profile URL (e.g., https://www.strava.com/athletes/1234)",
    )
    garmin_url = models.URLField(
        max_length=200,
        blank=True,
        help_text="Garmin Connect profile URL (e.g., https://connect.garmin.com/modern/profile/username)",
    )
    tpv_profile_url = models.URLField(
        max_length=200,
        blank=True,
        help_text="TrainingPeaks Virtual profile URL (e.g., https://tpvirtualhub.com/profile/GGGTTTRRRDDD123)",
    )

    # Training Equipment
    trainer = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Indoor trainer model (options from TRAINER_OPTIONS)",
    )
    powermeter = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Powermeter on trainer bike, if any (options from POWERMETER_OPTIONS)",
    )
    dual_recording = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="Do you dual record data?",
    )
    heartrate_monitor = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Heart rate monitor brand (options from HEARTRATE_MONITOR_OPTIONS)",
    )

    # Emergency Contact
    emergency_contact_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Emergency contact full name",
    )
    emergency_contact_relation = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Relationship to emergency contact (e.g., spouse, parent, friend)",
    )
    emergency_contact_email = models.EmailField(
        blank=True,
        default="",
        help_text="Emergency contact email address",
    )
    emergency_contact_phone = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Emergency contact phone number",
    )

    # Roles
    roles = models.JSONField(
        default=list,
        blank=True,
        help_text="List of user roles (legacy - use permission_overrides for new grants)",
    )
    permission_overrides = models.JSONField(
        default=dict,
        blank=True,
        help_text="Manual permission overrides: {permission_name: True/False}. True grants, False revokes.",
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
            logfire.warning(
                "Invalid role addition attempted",
                user_id=self.id,
                discord_id=self.discord_id,
                role=role,
                valid_roles=UserRoles.ALL,
            )
            return False
        if self.roles is None:
            self.roles = []
        if role not in self.roles:
            self.roles.append(role)
            logfire.info(
                "Role added to user",
                user_id=self.id,
                discord_id=self.discord_id,
                role=role,
            )
            return True
        logfire.debug(
            "Role already present, not added",
            user_id=self.id,
            discord_id=self.discord_id,
            role=role,
        )
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
            logfire.info(
                "Role removed from user",
                user_id=self.id,
                discord_id=self.discord_id,
                role=role,
            )
            return True
        logfire.debug(
            "Role not present, not removed",
            user_id=self.id,
            discord_id=self.discord_id,
            role=role,
        )
        return False

    def _get_permission_role_ids(self, constance_key: str) -> list[int]:
        """Get Discord role IDs for a permission from Constance config.

        Args:
            constance_key: The Constance config key (e.g., "PERM_TEAM_CAPTAIN_ROLES").

        Returns:
            List of Discord role IDs as integers.

        """
        import json

        from constance import config

        role_ids_json = getattr(config, constance_key, "[]")
        try:
            role_ids = json.loads(role_ids_json)
            return [int(rid) for rid in role_ids]
        except (json.JSONDecodeError, ValueError, TypeError):
            return []

    def has_permission(self, permission_name: str) -> bool:
        """Check if user has a specific permission.

        Permission check order:
        1. Superusers always have all permissions
        2. Check permission_overrides for explicit grant/revoke
        3. Check if any Discord role matches permission's configured roles
        4. Fall back to legacy app roles check

        Args:
            permission_name: The permission to check (from Permissions class).

        Returns:
            True if user has the permission, False otherwise.

        """
        # 1. Superuser bypass
        if self.is_superuser:
            logfire.debug(
                "Permission granted via superuser",
                user_id=self.id,
                discord_id=self.discord_id,
                permission=permission_name,
                grant_reason="superuser",
            )
            return True

        # 2. Check explicit overrides
        if self.permission_overrides:
            override = self.permission_overrides.get(permission_name)
            if override is not None:
                if override:
                    logfire.debug(
                        "Permission granted via override",
                        user_id=self.id,
                        discord_id=self.discord_id,
                        permission=permission_name,
                        grant_reason="permission_override",
                    )
                else:
                    logfire.debug(
                        "Permission denied via override",
                        user_id=self.id,
                        discord_id=self.discord_id,
                        permission=permission_name,
                        denial_reason="permission_override_revoked",
                    )
                return bool(override)

        # 3. Check Discord roles against Constance config
        constance_key = Permissions.CONSTANCE_MAP.get(permission_name)
        if constance_key:
            allowed_role_ids = self._get_permission_role_ids(constance_key)
            if allowed_role_ids:
                user_role_ids = self.get_discord_role_ids()
                if any(rid in user_role_ids for rid in allowed_role_ids):
                    logfire.debug(
                        "Permission granted via Discord role",
                        user_id=self.id,
                        discord_id=self.discord_id,
                        permission=permission_name,
                        grant_reason="discord_role",
                    )
                    return True

        # 4. Backward compatibility: check legacy app roles
        has_legacy_role = self.has_role(permission_name)
        if has_legacy_role:
            logfire.debug(
                "Permission granted via legacy role",
                user_id=self.id,
                discord_id=self.discord_id,
                permission=permission_name,
                grant_reason="legacy_role",
            )
        else:
            logfire.debug(
                "Permission denied - no matching role",
                user_id=self.id,
                discord_id=self.discord_id,
                permission=permission_name,
                denial_reason="no_matching_role",
            )
        return has_legacy_role

    @property
    def is_app_admin(self) -> bool:
        """Check if user has app admin permission."""
        return self.has_permission(Permissions.APP_ADMIN)

    @property
    def is_link_admin(self) -> bool:
        """Check if user has link admin permission."""
        return self.has_permission(Permissions.LINK_ADMIN)

    @property
    def is_membership_admin(self) -> bool:
        """Check if user has membership admin permission."""
        return self.has_permission(Permissions.MEMBERSHIP_ADMIN)

    @property
    def is_racing_admin(self) -> bool:
        """Check if user has racing admin permission."""
        return self.has_permission(Permissions.RACING_ADMIN)

    @property
    def is_team_captain(self) -> bool:
        """Check if user has team captain permission."""
        return self.has_permission(Permissions.TEAM_CAPTAIN)

    @property
    def is_team_vice_captain(self) -> bool:
        """Check if user has vice captain permission."""
        return self.has_permission(Permissions.VICE_CAPTAIN)

    @property
    def is_team_member(self) -> bool:
        """Check if user has team member permission."""
        return self.has_permission(Permissions.TEAM_MEMBER)

    @property
    def can_approve_verification(self) -> bool:
        """Check if user has permission to approve verification records."""
        return self.has_permission(Permissions.APPROVE_VERIFICATION)

    @property
    def is_pages_admin(self) -> bool:
        """Check if user has pages admin permission."""
        return self.has_permission(Permissions.PAGES_ADMIN)

    @property
    def is_race_ready(self) -> bool:
        """Check if user has all required verifications for their ZwiftPower category.

        Required types are determined by CATEGORY_REQUIREMENTS based on user's
        ZwiftPower division (category). Defaults to weight_light + height if
        no category is found.

        Returns:
            True if user has all required verifications, False otherwise.

        """
        from apps.team.models import RaceReadyRecord
        from apps.team.services import get_user_verification_types

        # Get required verification types for this user's category
        required_types = get_user_verification_types(self)

        # Get verified records for this user
        verified_records = self.race_ready_records.filter(
            status=RaceReadyRecord.Status.VERIFIED
        )

        # Build set of valid (non-expired) verification types
        valid_types = set()
        expired_types = []
        for record in verified_records:
            if not record.is_expired:
                valid_types.add(record.verify_type)
            else:
                expired_types.append(record.verify_type)

        # Check that ALL required types are present
        is_ready = all(req_type in valid_types for req_type in required_types)
        missing_types = [t for t in required_types if t not in valid_types]

        if is_ready:
            logfire.debug(
                "User is race ready",
                user_id=self.id,
                discord_id=self.discord_id,
                required_types=list(required_types),
                valid_types=list(valid_types),
            )
        else:
            logfire.debug(
                "User is not race ready",
                user_id=self.id,
                discord_id=self.discord_id,
                required_types=list(required_types),
                valid_types=list(valid_types),
                missing_types=missing_types,
                expired_types=expired_types,
            )

        return is_ready

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

    @property
    def is_profile_complete(self) -> bool:
        """Check if user has completed all required profile fields.

        Required fields:
        - first_name
        - last_name
        - birth_year
        - gender
        - timezone
        - country
        - zwid_verified (Zwift account must be verified)

        Returns:
            True if all required fields are filled and Zwift is verified, False otherwise.

        """
        # Collect missing fields for logging
        missing_fields = []

        required_text_fields = [
            ("first_name", self.first_name),
            ("last_name", self.last_name),
            ("gender", self.gender),
            ("timezone", self.timezone),
        ]

        # Check all text fields are non-empty
        for field_name, field_value in required_text_fields:
            if not (field_value and field_value.strip()):
                missing_fields.append(field_name)

        # Check country is set (CountryField returns a Country object, not a string)
        if not self.country:
            missing_fields.append("country")

        # Check birth_year is set (it's a nullable integer field)
        if not self.birth_year:
            missing_fields.append("birth_year")

        # Check Zwift account is verified
        if not self.zwid_verified:
            missing_fields.append("zwid_verified")

        is_complete = len(missing_fields) == 0

        if not is_complete:
            logfire.debug(
                "Profile is incomplete",
                user_id=self.id,
                discord_id=self.discord_id,
                missing_fields=missing_fields,
            )

        return is_complete

    @property
    def profile_completion_status(self) -> dict[str, bool]:
        """Get detailed profile completion status for each required field.

        Returns:
            Dictionary mapping field names to completion status.

        """
        return {
            "first_name": bool(self.first_name and self.first_name.strip()),
            "last_name": bool(self.last_name and self.last_name.strip()),
            "birth_year": bool(self.birth_year),
            "gender": bool(self.gender and self.gender.strip()),
            "timezone": bool(self.timezone and self.timezone.strip()),
            "country": bool(self.country),  # CountryField returns a Country object, not a string
            "zwid_verified": self.zwid_verified,
        }


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

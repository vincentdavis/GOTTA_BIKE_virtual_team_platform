"""Custom allauth adapters for Discord integration."""

import contextlib

import httpx
import logfire
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from constance import config
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.discord_service import sync_user_discord_roles


class DiscordSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Adapter to populate Discord fields from OAuth data.

    This adapter syncs Discord profile data to the custom User model's
    discord_id, discord_username, and discord_nickname fields on both
    initial signup and subsequent logins.

    It also verifies that users are members of the configured Discord guild
    before allowing signup or login.
    """

    def _check_guild_membership(self, request, sociallogin):
        """Check if user is a member of the required Discord guild.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.

        Raises:
            ImmediateHttpResponse: If user is not a member of the guild.

        """
        guild_id = config.GUILD_ID
        if not guild_id:
            # No guild configured, skip check
            return

        # Get the access token from the social login
        access_token = sociallogin.token.token

        try:
            # Fetch user's guilds from Discord API
            response = httpx.get(
                "https://discord.com/api/v10/users/@me/guilds",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            guilds = response.json()

            # Check if user is in the required guild
            user_guild_ids = [int(g["id"]) for g in guilds]
            if guild_id not in user_guild_ids:
                guild_name = config.GUILD_NAME or "the team"
                discord_url = config.DISCORD_URL or "#"
                logfire.warning(
                    "User not in required guild",
                    discord_id=sociallogin.account.extra_data.get("id"),
                    discord_username=sociallogin.account.extra_data.get("username"),
                    required_guild_id=guild_id,
                )
                messages.error(
                    request,
                    f"You must be a member of {guild_name} Discord server to sign up. "
                    f"Please join the server first and try again.",
                )
                raise ImmediateHttpResponse(redirect(discord_url))

        except httpx.HTTPError as e:
            logfire.error(f"Failed to fetch Discord guilds: {e}")
            messages.error(request, "Failed to verify Discord membership. Please try again.")
            raise ImmediateHttpResponse(redirect("account_login")) from e

    def populate_user(self, request, sociallogin, data):
        """Populate user with Discord-specific data on signup.

        We intentionally do NOT call super().populate_user() because it sets
        first_name/last_name from Discord data. We want those fields empty
        so users must fill them in on the profile page.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.
            data: The data dictionary from the provider.

        Returns:
            The populated user object.

        """
        user = sociallogin.user
        extra_data = sociallogin.account.extra_data

        # Set email from Discord (if provided)
        user.email = data.get('email', '')

        # Set username from Discord username
        user.username = extra_data.get('username', '')

        # Set Discord-specific fields
        user.discord_id = extra_data.get('id', '')
        user.discord_username = extra_data.get('username', '')
        user.discord_nickname = extra_data.get('global_name') or extra_data.get('username', '')
        user.discord_avatar = extra_data.get('avatar', '') or ''

        # first_name, last_name, birth_year, gender, timezone, country
        # are intentionally left empty - user must fill these in on profile page

        return user

    def save_user(self, request, sociallogin, form=None):
        """Save a new user and sync their Discord roles.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.
            form: Optional signup form.

        Returns:
            The saved user object.

        """
        # Check if user already exists (reconnecting social account)
        # In this case, preserve their existing profile data
        from apps.accounts.models import User

        existing_user = None
        if sociallogin.user.pk:
            # User already has a primary key, so they exist
            existing_user = sociallogin.user
        elif sociallogin.account.extra_data.get('id'):
            # Try to find existing user by discord_id
            discord_id = sociallogin.account.extra_data.get('id')
            with contextlib.suppress(User.DoesNotExist):
                existing_user = User.objects.get(discord_id=discord_id)

        # Store existing profile data before save
        preserved_first_name = existing_user.first_name if existing_user else None
        preserved_last_name = existing_user.last_name if existing_user else None

        user = super().save_user(request, sociallogin, form)

        # Restore preserved profile data if it existed
        if preserved_first_name or preserved_last_name:
            updated_fields = []
            if preserved_first_name and not user.first_name:
                user.first_name = preserved_first_name
                updated_fields.append('first_name')
            if preserved_last_name and not user.last_name:
                user.last_name = preserved_last_name
                updated_fields.append('last_name')
            if updated_fields:
                user.save(update_fields=updated_fields)
                logfire.info(
                    "Preserved existing profile data during social account reconnect",
                    user_id=user.id,
                    preserved_fields=updated_fields,
                )

        # Sync Discord guild roles for newly registered user
        sync_user_discord_roles(user)

        logfire.info(
            "User registered/connected via Discord OAuth",
            user_id=user.id,
            discord_id=user.discord_id,
            discord_username=user.discord_username,
            is_reconnect=existing_user is not None,
        )

        return user

    def pre_social_login(self, request, sociallogin):
        """Verify guild membership and update Discord fields on every login.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.

        """
        super().pre_social_login(request, sociallogin)

        # Check guild membership before allowing login
        self._check_guild_membership(request, sociallogin)

        if sociallogin.is_existing:
            user = sociallogin.user
            extra_data = sociallogin.account.extra_data

            # Update Discord fields in case they changed
            user.discord_id = extra_data.get('id', '')
            user.discord_username = extra_data.get('username', '')
            user.discord_nickname = extra_data.get('global_name') or extra_data.get('username', '')
            user.discord_avatar = extra_data.get('avatar', '') or ''
            user.save(update_fields=['discord_id', 'discord_username', 'discord_nickname', 'discord_avatar'])

            # Sync Discord guild roles for existing user on login
            sync_user_discord_roles(user)

    def get_login_redirect_url(self, request):
        """Return redirect URL after login.

        Redirects users with incomplete profiles to profile edit page.

        Args:
            request: The HTTP request.

        Returns:
            URL to redirect to after login.

        """
        user = request.user
        # Check if profile is incomplete
        if user.is_authenticated and not user.is_profile_complete:
            return reverse('accounts:profile_edit')
        return super().get_login_redirect_url(request)

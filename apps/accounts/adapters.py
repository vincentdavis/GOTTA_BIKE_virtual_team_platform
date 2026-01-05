"""Custom allauth adapters for Discord integration."""

import httpx
import logfire
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from constance import config
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


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

        Args:
            request: The HTTP request.
            sociallogin: The social login object.
            data: The data dictionary from the provider.

        Returns:
            The populated user object.

        """
        user = super().populate_user(request, sociallogin, data)

        # Extract Discord data from extra_data
        extra_data = sociallogin.account.extra_data

        # Set Discord-specific fields
        user.discord_id = extra_data.get('id', '')
        user.discord_username = extra_data.get('username', '')
        # Discord global_name is the display name, fallback to username
        user.discord_nickname = extra_data.get('global_name') or extra_data.get('username', '')
        user.discord_avatar = extra_data.get('avatar', '') or ''

        # Set username from Discord username if not set
        if not user.username:
            user.username = extra_data.get('username', '')

        # Clear first_name/last_name - we use discord_nickname for display
        user.first_name = ''
        user.last_name = ''

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

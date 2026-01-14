"""Custom allauth adapters for Discord integration."""

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

        # Log that populate_user was called - this helps debug unexpected calls
        logfire.warning(
            "populate_user called - this should only happen for NEW users",
            discord_id=extra_data.get('id'),
            discord_username=extra_data.get('username'),
            user_pk=user.pk,
            user_has_pk=bool(user.pk),
            sociallogin_is_existing=sociallogin.is_existing,
            request_path=request.path if request else None,
        )

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

        This method is called ONLY for NEW users (not existing logins).
        We handle user creation directly instead of calling super().save_user()
        to prevent any accidental data overwrites.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.
            form: Optional signup form.

        Returns:
            The saved user object.

        """
        extra_data = sociallogin.account.extra_data
        discord_id = extra_data.get('id')

        # Log that save_user was called
        logfire.info(
            "save_user called for NEW user",
            discord_id=discord_id,
            discord_username=extra_data.get('username'),
            request_path=request.path if request else None,
        )

        # Get the user object from sociallogin (created by populate_user)
        user = sociallogin.user

        # Set unusable password (users authenticate via Discord)
        user.set_unusable_password()

        # Save the user to the database
        user.save()

        # Now save the social account and link it to the user
        sociallogin.account.user = user
        sociallogin.account.save()

        # Save the token if present
        if sociallogin.token:
            sociallogin.token.account = sociallogin.account
            sociallogin.token.save()

        # Sync Discord guild roles for newly registered user
        sync_user_discord_roles(user)

        logfire.info(
            "New user created via Discord OAuth",
            user_id=user.id,
            discord_id=user.discord_id,
            discord_username=user.discord_username,
        )

        return user

    def pre_social_login(self, request, sociallogin):
        """Verify guild membership and update Discord fields on every login.

        IMPORTANT: This method should NOT modify user profile data (first_name,
        last_name, etc.). It only updates Discord-specific fields.

        This method also handles reconnecting existing users whose SocialAccount
        was deleted or not found - we look up by discord_id on our User model.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.

        """
        from apps.accounts.models import User

        extra_data = sociallogin.account.extra_data
        discord_id = extra_data.get('id')

        # Log pre_social_login call
        logfire.info(
            "pre_social_login called",
            discord_id=discord_id,
            discord_username=extra_data.get('username'),
            sociallogin_is_existing=sociallogin.is_existing,
            sociallogin_user_pk=sociallogin.user.pk if sociallogin.user else None,
            request_path=request.path if request else None,
        )

        # CRITICAL: If allauth doesn't recognize this as an existing user,
        # check if we have a User with this discord_id. This handles cases where
        # the SocialAccount was deleted but the User still exists.
        if not sociallogin.is_existing and discord_id:
            try:
                existing_user = User.objects.get(discord_id=discord_id)
                logfire.warning(
                    "Found existing user by discord_id - reconnecting SocialAccount",
                    user_id=existing_user.id,
                    discord_id=discord_id,
                    discord_username=extra_data.get('username'),
                    first_name=existing_user.first_name,
                    last_name=existing_user.last_name,
                )
                # Connect the social account to the existing user
                sociallogin.connect(request, existing_user)
                # Mark as existing so we don't create a new user
                sociallogin.is_existing = True
            except User.DoesNotExist:
                logfire.info(
                    "No existing user found by discord_id - will create new user",
                    discord_id=discord_id,
                )

        # Call parent implementation
        super().pre_social_login(request, sociallogin)

        # Check guild membership before allowing login
        self._check_guild_membership(request, sociallogin)

        # For EXISTING users, only update Discord fields (never profile fields)
        if sociallogin.is_existing:
            user = sociallogin.user

            logfire.info(
                "pre_social_login: Updating existing user Discord fields only",
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                birth_year=user.birth_year,
            )

            # Update ONLY Discord-related fields, never profile fields
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

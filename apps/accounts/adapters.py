"""Custom allauth adapters for Discord integration."""

import contextlib
import traceback

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
            stack_trace=traceback.format_stack()[-5:],
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

        extra_data = sociallogin.account.extra_data

        # Log that save_user was called
        logfire.warning(
            "save_user called - this should only happen for NEW users or reconnects",
            discord_id=extra_data.get('id'),
            discord_username=extra_data.get('username'),
            sociallogin_user_pk=sociallogin.user.pk,
            sociallogin_is_existing=sociallogin.is_existing,
            request_path=request.path if request else None,
            stack_trace=traceback.format_stack()[-5:],
        )

        existing_user = None
        if sociallogin.user.pk:
            # User already has a primary key, so they exist
            existing_user = sociallogin.user
            logfire.info(
                "save_user: Found existing user by pk",
                user_id=existing_user.id,
                first_name=existing_user.first_name,
                last_name=existing_user.last_name,
            )
        elif sociallogin.account.extra_data.get('id'):
            # Try to find existing user by discord_id
            discord_id = sociallogin.account.extra_data.get('id')
            with contextlib.suppress(User.DoesNotExist):
                existing_user = User.objects.get(discord_id=discord_id)
                logfire.info(
                    "save_user: Found existing user by discord_id",
                    user_id=existing_user.id,
                    first_name=existing_user.first_name,
                    last_name=existing_user.last_name,
                )

        # Store existing profile data before save
        preserved_first_name = existing_user.first_name if existing_user else None
        preserved_last_name = existing_user.last_name if existing_user else None
        preserved_birth_year = existing_user.birth_year if existing_user else None
        preserved_gender = existing_user.gender if existing_user else None
        preserved_timezone = existing_user.timezone if existing_user else None
        preserved_country = str(existing_user.country) if existing_user and existing_user.country else None

        logfire.info(
            "save_user: Preserved data before super().save_user()",
            preserved_first_name=preserved_first_name,
            preserved_last_name=preserved_last_name,
            preserved_birth_year=preserved_birth_year,
            preserved_gender=preserved_gender,
            preserved_timezone=preserved_timezone,
            preserved_country=preserved_country,
        )

        user = super().save_user(request, sociallogin, form)

        logfire.info(
            "save_user: After super().save_user()",
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            birth_year=user.birth_year,
            gender=user.gender,
            timezone=user.timezone,
            country=str(user.country) if user.country else None,
        )

        # Restore preserved profile data if it existed
        updated_fields = []
        if preserved_first_name and not user.first_name:
            user.first_name = preserved_first_name
            updated_fields.append('first_name')
        if preserved_last_name and not user.last_name:
            user.last_name = preserved_last_name
            updated_fields.append('last_name')
        if preserved_birth_year and not user.birth_year:
            user.birth_year = preserved_birth_year
            updated_fields.append('birth_year')
        if preserved_gender and not user.gender:
            user.gender = preserved_gender
            updated_fields.append('gender')
        if preserved_timezone and not user.timezone:
            user.timezone = preserved_timezone
            updated_fields.append('timezone')
        if preserved_country and not user.country:
            user.country = preserved_country
            updated_fields.append('country')

        if updated_fields:
            user.save(update_fields=updated_fields)
            logfire.info(
                "save_user: Restored preserved profile data",
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

        This method also handles reconnecting existing users who may have lost
        their SocialAccount link (e.g., if they deleted their account and re-signed up,
        or if there was a database issue).

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

        # CRITICAL FIX: If sociallogin is NOT existing but we have a user with this discord_id,
        # connect the social account to that existing user instead of creating a new one.
        # This prevents data loss when SocialAccount links are broken.
        if not sociallogin.is_existing and discord_id:
            try:
                existing_user = User.objects.get(discord_id=discord_id)
                logfire.warning(
                    "pre_social_login: Found existing user by discord_id, connecting social account",
                    user_id=existing_user.id,
                    discord_id=discord_id,
                    first_name=existing_user.first_name,
                    last_name=existing_user.last_name,
                    birth_year=existing_user.birth_year,
                )
                # Connect this social login to the existing user
                sociallogin.connect(request, existing_user)
                # Now sociallogin.is_existing should be True and sociallogin.user is existing_user
                logfire.info(
                    "pre_social_login: Social account connected to existing user",
                    user_id=existing_user.id,
                    sociallogin_is_existing=sociallogin.is_existing,
                )
            except User.DoesNotExist:
                # No existing user with this discord_id, proceed with normal signup
                logfire.info(
                    "pre_social_login: No existing user found, will create new user",
                    discord_id=discord_id,
                )

        super().pre_social_login(request, sociallogin)

        # Check guild membership before allowing login
        self._check_guild_membership(request, sociallogin)

        if sociallogin.is_existing:
            user = sociallogin.user
            extra_data = sociallogin.account.extra_data

            logfire.info(
                "pre_social_login: Updating existing user Discord fields only",
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                birth_year=user.birth_year,
            )

            # Update Discord fields in case they changed
            # IMPORTANT: Only update Discord-related fields, never profile fields
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

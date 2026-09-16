"""Custom allauth adapters for Discord integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import logfire
from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from constance import config
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from apps.accounts.discord_service import sync_user_discord_roles
from apps.accounts.membership import clear_departure

if TYPE_CHECKING:
    from django.http import HttpResponseRedirect

DISCORD_PROVIDER = "discord"


class NoLocalSignupAccountAdapter(DefaultAccountAdapter):
    """Refuse allauth's own signup: an account comes from a Discord login or not at all.

    Every membership check -- the block list, guild membership, Discord's verified email --
    lives in ``DiscordSocialAccountAdapter.pre_social_login``. allauth's local
    ``/accounts/signup/`` skips all of them: an email-only POST created a password-less
    account with no ``discord_id`` and logged it straight in. With
    ``SOCIALACCOUNT_EMAIL_AUTHENTICATION`` and ``_AUTO_CONNECT`` on, such an account would
    then capture the first Discord login carrying the same email address.

    This closes only the creation of new local accounts. Existing accounts are untouched, and
    ``/admin/`` still authenticates username and password through ``ModelBackend``, which is
    how the superuser signs in.
    """

    def is_open_for_signup(self, request) -> bool:
        """Whether allauth may create a local account.

        Args:
            request: The HTTP request.

        Returns:
            False, always.

        """
        return False

    def pre_login(
        self,
        request,
        user,
        *,
        email_verification,
        signal_kwargs,
        email,
        signup,
        redirect_url,
    ):
        """Refuse any allauth login that did not come through a Discord social login.

        allauth calls this at the start of every login it performs -- password, emailed
        code, password-reset, passkey and social alike (``flows.login.perform_login``). Only
        the social flows pass the ``SocialLogin`` in ``signal_kwargs["sociallogin"]``, and
        only a social login has been through ``pre_social_login``'s block-list and guild
        checks. The routes for the other flows are already closed in
        ``gotta_bike_platform/urls.py``; this is the second line, for any way in those
        routes miss. It does not run again when a login resumes after the TOTP step
        (``resume_login``), and ``/admin/`` logs in through Django's own view, not here.

        Args:
            request: The HTTP request.
            user: The account being logged into.
            email_verification: allauth's email verification mode for this login.
            signal_kwargs: Extra signal arguments; carries ``sociallogin`` for social logins.
            email: The email address used to log in, if any.
            signup: Whether this login completes a signup.
            redirect_url: Where to go after the login.

        Returns:
            A redirect to the login page when refused, otherwise allauth's own verdict.

        """
        sociallogin = (signal_kwargs or {}).get("sociallogin")
        if not _is_discord_login(sociallogin):
            logfire.warning(
                "Refused an allauth login that did not come through Discord",
                user_id=getattr(user, "pk", None),
                signup=signup,
            )
            messages.error(request, "Sign in with Discord to use this site.")
            return redirect("account_login")
        return super().pre_login(
            request,
            user,
            email_verification=email_verification,
            signal_kwargs=signal_kwargs,
            email=email,
            signup=signup,
            redirect_url=redirect_url,
        )


def _is_discord_login(sociallogin) -> bool:
    """Whether a login's ``sociallogin`` is a real Discord social login.

    Args:
        sociallogin: The value allauth put in ``signal_kwargs["sociallogin"]``, if any.

    Returns:
        True for a ``SocialLogin`` whose account comes from the Discord provider.

    """
    from allauth.socialaccount.models import SocialLogin

    if not isinstance(sociallogin, SocialLogin):
        return False
    account = getattr(sociallogin, "account", None)
    return getattr(account, "provider", None) == DISCORD_PROVIDER


def _back_to_login(request, message: str) -> HttpResponseRedirect:
    """Queue an error for the login page and return the redirect to it.

    A refused Discord login raises ``ImmediateHttpResponse`` carrying this response.

    Args:
        request: The HTTP request.
        message: The error shown on the login page.

    Returns:
        The redirect to the login page.

    """
    messages.error(request, message)
    return redirect("account_login")


class DiscordSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Adapter to populate Discord fields from OAuth data.

    This adapter syncs Discord profile data to the custom User model's
    discord_id, discord_username, and discord_nickname fields on both
    initial signup and subsequent logins.

    It also verifies that users are members of the configured Discord guild
    before allowing signup or login.
    """

    def is_open_for_signup(self, request, sociallogin) -> bool:
        """Whether a Discord login may create an account.

        allauth's social adapter otherwise delegates this to the account adapter, which
        refuses every signup (``NoLocalSignupAccountAdapter``) -- that would shut new riders
        out of Discord signup too. Discord signups stay open; who may have one is decided by
        ``pre_social_login`` (block list, guild membership, verified email).

        Args:
            request: The HTTP request.
            sociallogin: The social login being processed.

        Returns:
            True, always.

        """
        return True

    def _check_guild_membership(self, request, sociallogin):
        """Check live, with the rider's own token, that they are in the team's Discord server.

        Fails closed: anything short of Discord listing ``GUILD_ID`` among the rider's
        servers refuses the login -- including an unset ``GUILD_ID``, which used to skip the
        check and let any Discord account in.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.

        Raises:
            ImmediateHttpResponse: If membership is not confirmed.

        """
        discord_id = sociallogin.account.extra_data.get("id")
        guild_id = config.GUILD_ID
        if not guild_id:
            logfire.error("GUILD_ID is not configured; refusing Discord login", discord_id=discord_id)
            raise ImmediateHttpResponse(
                _back_to_login(
                    request,
                    "Sign-in is unavailable because the team's Discord server is not configured. "
                    "Please contact a team admin.",
                )
            )

        # A login without a token is refused like any other failure, not answered with a 500.
        access_token = getattr(sociallogin.token, "token", None)
        if not access_token:
            logfire.error("Discord login carried no access token", discord_id=discord_id)
            raise ImmediateHttpResponse(
                _back_to_login(request, "Failed to verify Discord server membership. Please try again.")
            )

        try:
            response = httpx.get(
                "https://discord.com/api/v10/users/@me/guilds",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
        except httpx.HTTPError as e:
            # No str(e): httpx quotes the request in its message, and the type says enough.
            logfire.error(
                "Failed to fetch Discord guilds",
                error_type=type(e).__name__,
                discord_id=discord_id,
            )
            raise ImmediateHttpResponse(
                _back_to_login(request, "Failed to verify Discord server membership. Please try again.")
            ) from e

        if response.status_code == 429:
            # Discord's 429 body is normally JSON, but an edge or proxy can answer with
            # anything, and reading it must not turn a refusal into a 500.
            try:
                retry_after = response.json().get("retry_after")
            except ValueError, AttributeError:
                retry_after = response.headers.get("Retry-After")
            logfire.warning(
                "Discord API rate limited during guild membership check",
                discord_id=discord_id,
                retry_after=retry_after,
            )
            raise ImmediateHttpResponse(
                _back_to_login(
                    request,
                    "Discord is temporarily rate limiting requests. Please wait a few minutes and try again.",
                )
            )

        try:
            response.raise_for_status()
            user_guild_ids = {int(g["id"]) for g in response.json()}
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            # No str(e): a 401's "Unauthorized" would have it scrubbed in production anyway,
            # and the status code and type carry what it says.
            logfire.error(
                "Failed to read Discord guilds",
                error_type=type(e).__name__,
                status_code=response.status_code,
                discord_id=discord_id,
            )
            raise ImmediateHttpResponse(
                _back_to_login(request, "Failed to verify Discord server membership. Please try again.")
            ) from e

        if int(guild_id) not in user_guild_ids:
            guild_name = config.GUILD_NAME or "the team"
            discord_url = config.DISCORD_URL or ""
            logfire.warning(
                "User not in required guild",
                discord_id=discord_id,
                required_guild_id=guild_id,
                user_guild_count=len(user_guild_ids),
            )
            invite_msg = ""
            if discord_url and discord_url.startswith(("http://", "https://")):
                invite_msg = format_html(
                    ' <a href="{}" target="_blank" rel="noopener" class="link">Join here</a>.', discord_url
                )
            # Built as safe HTML (both values escaped) so the toast renders the invite as a link;
            # the message store keeps the safe flag across the redirect.
            raise ImmediateHttpResponse(
                _back_to_login(
                    request,
                    format_html("You must be a member of the {} Discord server to log in.{}", guild_name, invite_msg),
                )
            )

    def _check_not_blocked(self, request, sociallogin):
        """Refuse a blocked Discord account, and an existing account that belongs to one.

        The incoming Discord id is one half. The other is the account allauth has already
        resolved (``sociallogin.user``), which with ``SOCIALACCOUNT_EMAIL_AUTHENTICATION`` can
        be matched by email alone: a blocked person signing in with a second Discord account
        that shares their verified email would otherwise land in their old account -- and
        the existing-user update in ``pre_social_login`` would then overwrite its blocked
        ``discord_id`` with the new, unblocked one.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.

        Raises:
            ImmediateHttpResponse: If either id is blocked.

        """
        from apps.accounts.models import BlockedDiscordId

        discord_id = sociallogin.account.extra_data.get("id")
        # An unsaved user is populate_user's throwaway, carrying the incoming id; only a
        # saved one is an account allauth resolved.
        existing = sociallogin.user if getattr(sociallogin.user, "pk", None) else None
        existing_discord_id = str(getattr(existing, "discord_id", "") or "")

        blocked_incoming = BlockedDiscordId.is_blocked(discord_id)
        blocked_account = (
            not blocked_incoming
            and existing_discord_id != str(discord_id)
            and BlockedDiscordId.is_blocked(existing_discord_id)
        )
        if blocked_incoming or blocked_account:
            logfire.warning(
                "Blocked Discord account attempted to sign in",
                discord_id=discord_id,
                user_id=getattr(existing, "pk", None),
                account_discord_id=existing_discord_id if blocked_account else None,
            )
            raise ImmediateHttpResponse(
                _back_to_login(
                    request,
                    "This Discord account cannot sign in. Contact a team admin if you think this is a mistake.",
                )
            )

    def _check_email_verified(self, request, sociallogin):
        """Refuse a Discord account whose email Discord has not verified.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.

        Raises:
            ImmediateHttpResponse: If the email is unverified.

        """
        extra_data = sociallogin.account.extra_data
        email_verified = extra_data.get("verified", False)
        if not email_verified:
            logfire.warning(
                "Discord email not verified - blocking login",
                discord_id=extra_data.get("id"),
                email_verified=email_verified,
            )
            raise ImmediateHttpResponse(
                _back_to_login(
                    request,
                    "Your Discord account's email is not verified. "
                    "Please verify your email in Discord Settings > My Account, then try again.",
                )
            )

    def _reconnect_by_discord_id(self, request, sociallogin):
        """Attach this Discord login to the account already holding its ``discord_id``.

        For an account whose ``SocialAccount`` row was lost: allauth found neither the row
        nor an email match, but a ``User`` still carries this Discord id, and signing up
        again would create a second, empty account. Only called once every check has
        passed, because ``connect()`` writes the ``SocialAccount``. ``connect()`` also sets
        ``sociallogin.user`` to the saved account, which is all it takes for
        ``is_existing`` -- a read-only property in allauth -- to be True.

        ``User.discord_id`` is not unique. If more than one account holds the id the login
        is refused rather than attached to a guess.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.

        Raises:
            ImmediateHttpResponse: If more than one account holds the Discord id.

        """
        from apps.accounts.models import User

        extra_data = sociallogin.account.extra_data
        discord_id = str(extra_data.get("id"))
        user_ids = list(User.objects.filter(discord_id=discord_id).order_by("pk").values_list("pk", flat=True))
        if not user_ids:
            logfire.info("No existing user found by discord_id - will create new user", discord_id=discord_id)
            return
        if len(user_ids) > 1:
            logfire.error(
                "More than one account holds this discord_id; refusing to pick one",
                discord_id=discord_id,
                user_ids=user_ids,
            )
            raise ImmediateHttpResponse(
                _back_to_login(
                    request,
                    "More than one account here is linked to this Discord account. "
                    "Please contact a team admin to sort it out.",
                )
            )

        existing_user = User.objects.get(pk=user_ids[0])
        logfire.warning(
            "Found existing user by discord_id - reconnecting SocialAccount",
            user_id=existing_user.id,
            discord_id=discord_id,
        )
        sociallogin.connect(request, existing_user)

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
            discord_id=extra_data.get("id"),
            user_pk=user.pk,
            user_has_pk=bool(user.pk),
            sociallogin_is_existing=sociallogin.is_existing,
            request_path=request.path if request else None,
        )

        # Set email from Discord (if provided)
        user.email = data.get("email", "")

        # Set username from Discord username
        user.username = extra_data.get("username", "")

        # Set Discord-specific fields
        user.discord_id = extra_data.get("id", "")
        user.discord_username = extra_data.get("username", "")
        user.discord_nickname = extra_data.get("global_name") or extra_data.get("username", "")
        user.discord_avatar = extra_data.get("avatar", "") or ""

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
        discord_id = extra_data.get("id")

        # Log that save_user was called
        logfire.info(
            "save_user called for NEW user",
            discord_id=discord_id,
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
        )

        return user

    def pre_social_login(self, request, sociallogin):
        """Verify the Discord login may proceed, then update Discord fields on every login.

        IMPORTANT: This method should NOT modify user profile data (first_name,
        last_name, etc.). It only updates Discord-specific fields.

        Every check -- block list, live guild membership, verified email -- runs before
        this method writes anything, so a refused login leaves no ``SocialAccount`` or
        ``User`` change behind. Only then are existing users whose SocialAccount was lost
        reconnected by ``discord_id``. A failed check raises allauth's
        ``ImmediateHttpResponse`` (from the ``_check_*`` helpers), which sends the rider back
        to the login page.

        Args:
            request: The HTTP request.
            sociallogin: The social login object.

        """
        from allauth.socialaccount.providers.base import AuthProcess

        extra_data = sociallogin.account.extra_data
        discord_id = extra_data.get("id")

        # Refuse a blocked account before anything else, including the Discord API call.
        self._check_not_blocked(request, sociallogin)

        # Log pre_social_login call
        logfire.info(
            "pre_social_login called",
            discord_id=discord_id,
            sociallogin_is_existing=sociallogin.is_existing,
            sociallogin_user_pk=sociallogin.user.pk if sociallogin.user else None,
            request_path=request.path if request else None,
        )

        self._check_guild_membership(request, sociallogin)
        self._check_email_verified(request, sociallogin)

        # CRITICAL: If allauth doesn't recognize this as an existing user, check if we have
        # a User with this discord_id. This handles cases where the SocialAccount was deleted
        # but the User still exists. Not for a "connect" from the connections page, where
        # allauth attaches the account to the signed-in user itself.
        if not sociallogin.is_existing and discord_id and sociallogin.state.get("process") != AuthProcess.CONNECT:
            self._reconnect_by_discord_id(request, sociallogin)

        # Discord has just confirmed they are in the server, so a departure stamped by an
        # earlier guild sync is stale. Left in place, DepartedMemberLogoutMiddleware would
        # sign a rejoined rider out on their next request, until the next sync.
        if clear_departure(discord_id):
            logfire.info("Cleared a stale guild departure on Discord login", discord_id=discord_id)

        # Call parent implementation
        super().pre_social_login(request, sociallogin)

        # For EXISTING users, only update Discord fields (never profile fields)
        if sociallogin.is_existing:
            user = sociallogin.user

            # Ids only: this runs on every login, and names or a birth year must not leave here.
            logfire.info(
                "pre_social_login: Updating existing user Discord fields only",
                user_id=user.id,
                discord_id=discord_id,
            )

            # Update ONLY Discord-related fields, never profile fields
            user.discord_id = extra_data.get("id", "")
            user.discord_username = extra_data.get("username", "")
            user.discord_nickname = extra_data.get("global_name") or extra_data.get("username", "")
            user.discord_avatar = extra_data.get("avatar", "") or ""
            user.save(update_fields=["discord_id", "discord_username", "discord_nickname", "discord_avatar"])

            # Sync Discord guild roles for existing user on login
            sync_user_discord_roles(user)

    def on_authentication_error(self, request, provider, error=None, exception=None, extra_context=None):
        """Handle OAuth authentication errors with user-friendly messages.

        Called by allauth when the OAuth callback contains an error, e.g. when
        Discord denies authorization because the user's email is unverified.

        Args:
            request: The HTTP request.
            provider: The social account provider.
            error: The error code (e.g. AuthError.DENIED, AuthError.UNKNOWN).
            exception: The exception that occurred, if any.
            extra_context: Additional context dict.

        """
        exception_str = str(exception) if exception else ""

        # The exception text and extra context are not logged: an OAuth error can quote the
        # callback URL or its state, and an "Unauthorized" in them gets the value scrubbed anyway.
        logfire.error(
            "Discord OAuth authentication error",
            error=str(error),
            error_type=type(exception).__name__ if exception else None,
            provider=str(provider),
        )

        if "rate" in exception_str.lower() and "limit" in exception_str.lower():
            messages.error(
                request,
                "Discord is temporarily rate limiting login requests. Please wait a few minutes and try again.",
            )
        elif error == "denied":
            messages.error(
                request,
                "Discord denied the login request. This usually means your Discord account's "
                "email is not verified. Please check Discord Settings > My Account and verify "
                "your email address, then try again.",
            )
        else:
            messages.error(
                request,
                f"Something went wrong during Discord login. Please try again. "
                f"If the problem persists, contact a team admin. (Error: {error or 'unknown'})",
            )

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
            return reverse("accounts:profile_edit")
        return super().get_login_redirect_url(request)

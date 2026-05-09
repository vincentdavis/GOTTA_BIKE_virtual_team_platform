"""Helpers for issuing, hashing, and looking up user API keys."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING

import logfire
from constance import config
from django.utils import timezone

from apps.accounts.models import Permissions
from apps.user_api.models import UserApiKey

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.accounts.models import User

KEY_PREFIX = "coal"
DEFAULT_LIFETIME_DAYS = 30


def _hash_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw API key.

    Args:
        raw_key: The raw key string.

    Returns:
        Hex-encoded SHA-256 digest.

    """
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str, str]:
    """Generate a new API key.

    Returns:
        Tuple of ``(raw_key, key_hash, prefix, last4)``.

    """
    secret = secrets.token_urlsafe(32)
    raw_key = f"{KEY_PREFIX}_{secret}"
    return raw_key, _hash_key(raw_key), raw_key[:8], raw_key[-4:]


def issue_api_key(user: User, name: str, lifetime_days: int = DEFAULT_LIFETIME_DAYS) -> tuple[UserApiKey, str]:
    """Create and persist a new ``UserApiKey`` for ``user``.

    Args:
        user: The user the key belongs to.
        name: User-chosen label.
        lifetime_days: Days until expiration (default 30).

    Returns:
        Tuple of (the persisted UserApiKey, the raw key string).
        The raw key is returned only here; it is not stored on the model.

    """
    raw_key, key_hash, prefix, last4 = generate_api_key()
    api_key = UserApiKey.objects.create(
        user=user,
        name=name.strip(),
        key_hash=key_hash,
        prefix=prefix,
        last4=last4,
        expires_at=timezone.now() + timedelta(days=lifetime_days),
    )
    return api_key, raw_key


def user_can_use_api(user: User) -> bool:
    """Return True if ``user`` may create or use API keys.

    Required gates (all must hold):

    - ``user.is_active`` is True.
    - ``user.has_permission(Permissions.TEAM_MEMBER)`` is True.
    - User holds **every** Discord role ID listed in the
      ``PERM_ROLES_REQUIRED_USE_API`` Constance setting. Empty list means no
      extra role restriction. Bad/non-JSON config falls back to deny.

    Args:
        user: The user to check.

    Returns:
        True if the user satisfies every gate, otherwise False.

    """
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if not user.has_permission(Permissions.TEAM_MEMBER):
        return False
    try:
        required_role_ids = json.loads(config.PERM_ROLES_REQUIRED_USE_API)
    except (json.JSONDecodeError, TypeError) as e:
        logfire.error("PERM_ROLES_REQUIRED_USE_API not valid JSON", error=str(e))
        return False
    if not required_role_ids:
        return True
    return all(user.has_discord_role(int(role_id)) for role_id in required_role_ids)


def lookup_active_key(raw_key: str) -> UserApiKey | None:
    """Resolve a raw key to its active ``UserApiKey`` row.

    Active means: matches the hash, not revoked, not expired, and the owning
    user is still active (``user.is_active=True``). Deactivating a user
    therefore disables every key they hold immediately.

    Args:
        raw_key: The raw key string presented by the client.

    Returns:
        The matching active key, or ``None`` if missing/expired/revoked or
        the owning user is deactivated.

    """
    if not raw_key:
        return None
    return (
        UserApiKey.objects
        .select_related("user")
        .filter(
            key_hash=_hash_key(raw_key),
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
            user__is_active=True,
        )
        .first()
    )


def get_client_ip(request: HttpRequest) -> str:
    """Best-effort client IP for rate limiting.

    Args:
        request: The HTTP request.

    Returns:
        The client IP string, or ``"unknown"`` if unavailable.

    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def user_api_rate_key(group: str, request: HttpRequest) -> str:
    """Bucket rate limits per API key (or per IP when unauthenticated).

    Args:
        group: The django-ratelimit group name (unused).
        request: The HTTP request, after Ninja auth has run.

    Returns:
        A stable string identifying the rate-limit bucket.

    """
    auth = getattr(request, "auth", None)
    if isinstance(auth, dict) and auth.get("api_key") is not None:
        return f"apikey:{auth['api_key'].pk}"
    return f"ip:{get_client_ip(request)}"


def auth_ip_rate_key(group: str, request: HttpRequest) -> str:
    """Bucket auth-attempt rate limits per client IP.

    Used by ``UserApiKeyAuth.authenticate`` to throttle brute-force or scanning
    traffic before the DB lookup runs. Honours ``X-Forwarded-For`` so the limit
    works correctly behind Railway/proxies.

    Args:
        group: The django-ratelimit group name (unused).
        request: The HTTP request.

    Returns:
        A stable string identifying the per-IP rate-limit bucket.

    """
    return f"auth_ip:{get_client_ip(request)}"

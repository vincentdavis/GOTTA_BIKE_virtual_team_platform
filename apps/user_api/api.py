"""User-facing Ninja API authenticated by per-user 30-day API keys."""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from constance import config
from django.utils import timezone
from django_ratelimit.core import is_ratelimited
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited
from ninja import NinjaAPI
from ninja.security import HttpBearer

from apps.user_api.models import UserApiKey
from apps.user_api.services import auth_ip_rate_key, lookup_active_key, user_api_rate_key, user_can_use_api
from apps.zwiftracing.models import ZRRider

# Per-IP cap on auth attempts (success or fail). Sized so legitimate clients —
# even from a shared NAT — never trip it; it just caps malicious throughput so
# bogus tokens can't hammer the DB at full speed.
AUTH_IP_RATE = "120/m"

if TYPE_CHECKING:
    from django.http import HttpRequest


class UserApiKeyAuth(HttpBearer):
    """Authenticate requests against the ``UserApiKey`` table.

    Expects ``Authorization: Bearer <raw key>``. Returns a context dict on
    success: ``{"user": User, "api_key": UserApiKey}``.
    """

    def authenticate(self, request: HttpRequest, token: str | None) -> dict | None:
        """Resolve the bearer token to a user + API key context.

        Args:
            request: The HTTP request.
            token: The bearer token string.

        Returns:
            Auth context dict on success, ``None`` to reject (Ninja returns 401).

        """
        # Per-IP rate limit on auth attempts (counted before the DB lookup) so
        # bogus tokens can't brute-force or hammer the DB. Increments on every
        # call; tripping it short-circuits with a 401.
        if is_ratelimited(
            request,
            group="user_api_auth",
            fn=None,
            key=auth_ip_rate_key,
            rate=AUTH_IP_RATE,
            method="ALL",
            increment=True,
        ):
            logfire.warning("user api auth rate limited by ip", path=request.path)
            return None

        key = lookup_active_key(token or "")
        if key is None:
            logfire.warning(
                "user api key auth failed",
                path=request.path,
                prefix=(token or "")[:8] or None,
            )
            return None

        # Re-check the same gate the management page enforces, so losing a
        # required role (or losing team_member) instantly disables every key
        # the user holds without needing to revoke them one by one.
        if not user_can_use_api(key.user):
            logfire.warning(
                "user api key rejected: owner no longer meets use requirements",
                path=request.path,
                key_id=key.pk,
                user_id=key.user_id,
            )
            return None

        # Best-effort last_used_at bump. Skip writes if the value was updated in
        # the last minute to avoid contention on hot keys.
        now = timezone.now()
        if not key.last_used_at or (now - key.last_used_at).total_seconds() > 60:
            UserApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
            key.last_used_at = now

        return {"user": key.user, "api_key": key}


api = NinjaAPI(auth=UserApiKeyAuth(), urls_namespace="user_api_v1", title="User API")


@api.exception_handler(Ratelimited)
def ratelimited_handler(request: HttpRequest, exc: Ratelimited):
    """Render a JSON 429 when django-ratelimit blocks a request.

    Args:
        request: The HTTP request.
        exc: The Ratelimited exception (unused).

    Returns:
        A 429 JSON response.

    """
    auth = getattr(request, "auth", None)
    api_key_pk = auth.get("api_key").pk if isinstance(auth, dict) and auth.get("api_key") else None
    logfire.warning("user api rate limit exceeded", path=request.path, api_key_id=api_key_pk)
    return api.create_response(request, {"error": "rate limit exceeded"}, status=429)


def _serialise_zr(rider: ZRRider) -> dict:
    """Return the canonical ZR profile dict shape.

    Matches the ``"zwiftracing"`` block in ``apps/dbot_api/api.py:get_my_profile``
    so consumers see one consistent format across APIs.

    Args:
        rider: A ``ZRRider`` row.

    Returns:
        A JSON-serialisable dict.

    """
    return {
        "zwid": rider.zwid,
        "name": rider.name,
        "country": rider.country,
        "gender": rider.gender,
        "height": rider.height,
        "weight": float(rider.weight) if rider.weight else None,
        "zp_category": rider.zp_category,
        "zp_ftp": rider.zp_ftp,
        # Critical Power
        "power_cp": float(rider.power_cp) if rider.power_cp else None,
        # Race ratings
        "race_current_rating": float(rider.race_current_rating) if rider.race_current_rating else None,
        "race_current_category": rider.race_current_category,
        "race_max30_rating": float(rider.race_max30_rating) if rider.race_max30_rating else None,
        "race_max30_category": rider.race_max30_category,
        "race_max90_rating": float(rider.race_max90_rating) if rider.race_max90_rating else None,
        "race_max90_category": rider.race_max90_category,
        # Race stats
        "race_finishes": rider.race_finishes,
        "race_wins": rider.race_wins,
        "race_podiums": rider.race_podiums,
        "race_dnfs": rider.race_dnfs,
        # Phenotype
        "phenotype_value": rider.phenotype_value,
        "phenotype_sprinter": float(rider.phenotype_sprinter) if rider.phenotype_sprinter else None,
        "phenotype_puncheur": float(rider.phenotype_puncheur) if rider.phenotype_puncheur else None,
        "phenotype_pursuiter": float(rider.phenotype_pursuiter) if rider.phenotype_pursuiter else None,
        "phenotype_climber": float(rider.phenotype_climber) if rider.phenotype_climber else None,
        "phenotype_tt": float(rider.phenotype_tt) if rider.phenotype_tt else None,
        # Power curve (w/kg)
        "power_wkg5": float(rider.power_wkg5) if rider.power_wkg5 else None,
        "power_wkg15": float(rider.power_wkg15) if rider.power_wkg15 else None,
        "power_wkg60": float(rider.power_wkg60) if rider.power_wkg60 else None,
        "power_wkg300": float(rider.power_wkg300) if rider.power_wkg300 else None,
        "power_wkg1200": float(rider.power_wkg1200) if rider.power_wkg1200 else None,
    }


@api.get("/zr_profile/{zwid}")
@ratelimit(
    key=user_api_rate_key,
    rate=lambda group, request: config.USER_API_RATE_LIMIT,
    block=True,
)
def get_zr_profile(request: HttpRequest, zwid: int):
    """Return the Zwift Racing profile for ``zwid`` if known to this team.

    Args:
        request: The authenticated HTTP request.
        zwid: The Zwift rider id to look up.

    Returns:
        Profile JSON, or 404 if no ``ZRRider`` row exists for the zwid. The
        permission check (``team_member`` plus the ``PERM_ROLES_REQUIRED_USE_API``
        gate) is enforced at auth time by ``UserApiKeyAuth``.

    """
    user = request.auth["user"]

    try:
        rider = ZRRider.objects.get(zwid=zwid)
    except ZRRider.DoesNotExist:
        logfire.info(
            "user api zr_profile miss",
            zwid=zwid,
            api_key_id=request.auth["api_key"].pk,
        )
        return api.create_response(request, {"error": "zwid not found in zwiftracing"}, status=404)

    logfire.info(
        "user api zr_profile hit",
        zwid=zwid,
        api_key_id=request.auth["api_key"].pk,
        user_id=user.pk,
    )
    return _serialise_zr(rider)

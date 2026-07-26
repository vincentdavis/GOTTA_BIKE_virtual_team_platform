"""Reconcile platform Zwift verification against the zauth service.

Phase 2 of the zauth verification migration. The zauth OAuth connection (owned by
the zwift service) is the source of truth for ``zauth``-method verification; this
module stamps that onto the platform ``User`` (``zwid_verified`` /
``zwid_verification_method='zauth'`` / ``zwid_verified_at``) and revokes it when
the account is no longer connected.

Two invariants (see project memory ``zauth-verification-migration``):

1. **Revocation is scoped to ``method='zauth'`` users only.** Legacy/admin
   verifications are never touched here — they are grandfathered and only change
   via the eventual cutover flag or an admin.
2. **A failed/unavailable service call never revokes anyone.** ``None`` from the
   client means "unknown", not "nobody connected", so we skip rather than wipe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.zwift import client

if TYPE_CHECKING:
    from apps.accounts.models import User

# Matches User.VerificationMethod.ZAUTH (kept as a literal to avoid importing the
# model at module load; asserted against the enum in tests).
ZAUTH = "zauth"

# ``User.zwid`` is a PositiveIntegerField, i.e. a 32-bit ``integer`` column on
# PostgreSQL: a larger value raises DataError on save rather than storing.
_MAX_ZWID = 2147483647


def _coerce_zwid(value: object) -> int | None:
    """Coerce a service-reported zwid into a value storable in ``User.zwid``.

    Args:
        value: The raw ``zwid`` from the service (str/int/None).

    Returns:
        The zwid as an int, or None when it is missing, non-numeric, zero, or
        too large for the column.

    """
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    zwid = int(text)
    if zwid <= 0 or zwid > _MAX_ZWID:
        return None
    return zwid


def _grant(user: User, zwid: object) -> str:
    """Stamp zauth verification onto a user, taking the service's zwid as official.

    Refuses to verify at all when the service reports no usable zwid: a ``zauth``
    verification asserts an *official* Zwift ID, so it must never be stamped over
    a self-reported one (and an out-of-range value would abort the caller's run).

    Args:
        user: The user to verify.
        zwid: The zwid reported by the service (may be str/int/None).

    Returns:
        ``"granted"`` if any field changed (and was saved), ``"unchanged"`` if
        the user was already correct, or ``"invalid_zwid"`` if the service gave
        no usable zwid (nothing is written, and an existing verification stands).

    """
    zwid_int = _coerce_zwid(zwid)
    if zwid_int is None:
        logfire.warning(
            "zauth grant skipped: service reported no usable zwid",
            user_id=user.pk,
            reported_zwid=str(zwid)[:64],
        )
        return "invalid_zwid"

    fields: list[str] = []
    if user.zwid != zwid_int:
        user.zwid = zwid_int
        fields.append("zwid")

    newly_zauth = user.zwid_verification_method != ZAUTH
    if not user.zwid_verified:
        user.zwid_verified = True
        fields.append("zwid_verified")
    if newly_zauth:
        user.zwid_verification_method = ZAUTH
        fields.append("zwid_verification_method")
    # Stamp the time only when the verification first becomes zauth (or was never
    # stamped), so a steady-state hourly reconcile doesn't rewrite the row.
    if newly_zauth or user.zwid_verified_at is None:
        user.zwid_verified_at = timezone.now()
        fields.append("zwid_verified_at")

    if fields:
        user.save(update_fields=fields)
        if "zwid" in fields:
            # Required verification types are keyed on zwid (ZPTeamRiders -> ZP
            # category), so adopting the official zwid can change race-ready status.
            # is_race_ready is a cache with no signal behind it, so refresh it here
            # rather than leaving it stale until the next full sweep.
            user.refresh_race_ready()
    return "granted" if fields else "unchanged"


def _revoke(user: User) -> bool:
    """Revoke a user's zauth verification, keeping the last-known zwid.

    Args:
        user: The user to unverify.

    Returns:
        True if any field changed (and was saved), else False.

    """
    fields: list[str] = []
    if user.zwid_verified:
        user.zwid_verified = False
        fields.append("zwid_verified")
    if user.zwid_verification_method:
        user.zwid_verification_method = ""
        fields.append("zwid_verification_method")
    # zwid is intentionally left intact; a reconnect re-confirms it.
    if fields:
        user.save(update_fields=fields)
    return bool(fields)


def apply_status(user: User, status: dict | None) -> str:
    """Reconcile one user against an already-fetched connection status.

    Args:
        user: The user.
        status: The service's status dict, or None when the service is
            unconfigured/unreachable (invariant 2: never revoke on None).

    Returns:
        One of ``"granted"``, ``"revoked"``, ``"unchanged"``, ``"skipped"``
        (service unavailable) or ``"invalid_zwid"`` (connected, but the service
        reported no usable zwid).

    """
    if status is None:
        return "skipped"
    if status.get("connected"):
        return _grant(user, status.get("zwid"))
    # Genuinely not connected (service reachable). Only revoke zauth-method users
    # (invariant 1) — legacy/admin verifications are left alone.
    if user.zwid_verification_method == ZAUTH:
        return "revoked" if _revoke(user) else "unchanged"
    return "unchanged"


def sync_user_verification(user: User) -> str:
    """Fetch one user's live connection status and reconcile it (on-connect path).

    Args:
        user: The user.

    Returns:
        The :func:`apply_status` result string.

    """
    return apply_status(user, client.get_connection_status(str(user.pk)))


def reconcile_all() -> dict:
    """Reconcile every user's zauth verification against the service (hourly task).

    Grants for all connected users; revokes ``method='zauth'`` users no longer in
    the connections list. Aborts without any change if the service is unavailable.

    Returns:
        A summary dict with a ``status`` of ``"completed"`` or ``"skipped"``.

    """
    with logfire.span("reconcile_zauth_verifications"):
        connections = client.list_connections()
        if connections is None:  # invariant 2: unavailable != nobody connected
            logfire.warning("zauth reconcile skipped: service unavailable")
            return {"status": "skipped", "reason": "service_unavailable"}

        connected: dict[int, dict] = {}
        for row in connections:
            uid = row.get("user_id")
            if uid is not None and str(uid).isdigit():
                connected[int(uid)] = row

        user_model = get_user_model()

        granted = 0
        invalid = 0
        for pk, row in connected.items():
            user = user_model.objects.filter(pk=pk).first()
            if user is None:
                continue
            outcome = _grant(user, row.get("zwid"))
            if outcome == "granted":
                granted += 1
            elif outcome == "invalid_zwid":
                invalid += 1

        # Invariant 1: only zauth-method users may be revoked here.
        revoked = 0
        stale = user_model.objects.filter(zwid_verification_method=ZAUTH).exclude(pk__in=connected.keys())
        for user in stale.iterator(chunk_size=200):
            if _revoke(user):
                revoked += 1

        result = {
            "status": "completed",
            "connected": len(connected),
            "granted": granted,
            "revoked": revoked,
            "invalid_zwid": invalid,
        }
        logfire.info("zauth reconcile completed", **result)
        return result

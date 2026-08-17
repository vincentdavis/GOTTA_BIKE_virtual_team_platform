"""Zwift Auth panel for the verification review page.

Gives a reviewer the official Zwift-sourced numbers next to the rider's self-reported
submission, so weight and height claims can be checked against Zwift itself rather than
taken on trust.

What the zauth service can and cannot supply today
--------------------------------------------------
The service (``apps.zwift.client``) exposes a rider's connection status and a **single
latest** racing-profile snapshot. From that we can show:

- whether the account is connected at all, and since when;
- the current weight, with the timestamp of the snapshot it came from.

Three of the things a reviewer would want are **not available yet**, and this module
reports them as explicitly unavailable rather than blank or zero, because a blank reads
as "the rider is 0 kg" or "nothing to see here" and a reviewer would act on it:

- **90-day min/max weight** — the service keeps one row per account and upserts it, so
  there is no history to take a min or max over.
- **Height** — the upstream profile carries it, but the service neither denormalizes it
  onto its response nor is it safe for the platform to reach into the raw ``data`` blob
  with upstream field names: keeping partner-API schema knowledge inside the private
  service is the entire point of the split.
- **90-day min/max height** — needs both of the above.

Closing those means service-side work first (denormalize height; store a snapshot series),
then this module reads the new fields. ``UNAVAILABLE_REASON`` is the single place the UI
explains the gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django.utils.dateparse import parse_datetime

from apps.zwift import client as zwift_client

if TYPE_CHECKING:
    from apps.accounts.models import User

#: Shown against every metric the zauth service cannot supply yet. One string so the
#: page never invents a different excuse per row.
UNAVAILABLE_REASON = "Not published by the Zwift service yet"


def _dt(value: object):
    """Parse a service ISO-8601 timestamp into a datetime for template formatting.

    Args:
        value: The raw value from the service payload.

    Returns:
        A datetime when parseable, else the value unchanged so nothing is silently
        dropped (the template can still print a raw string).

    """
    if isinstance(value, str):
        return parse_datetime(value) or value
    return value


def _weight_kg(profile: dict) -> float | None:
    """Convert the profile's gram weight to kilograms.

    Args:
        profile: The racing-profile dict from the zauth service.

    Returns:
        Weight in kg to one decimal, or None when absent or non-numeric.

    """
    grams = profile.get("weight_in_grams")
    if not isinstance(grams, (int, float)) or grams <= 0:
        return None
    return round(grams / 1000, 1)


def build_zauth_panel(user: User) -> dict:
    """Assemble the Zwift Auth panel context for one rider.

    Never raises and never lets a slow or down service break the review page: every
    failure mode collapses to ``connected=False`` with the rest left unavailable.

    Args:
        user: The rider whose verification record is being reviewed.

    Returns:
        A context dict:

        - ``configured``: the platform has a zauth service to talk to at all.
        - ``connected``: the rider has an active Zwift OAuth connection.
        - ``connected_at`` / ``zwid``: from the connection status.
        - ``verified_via_zauth`` / ``verified_at``: the platform's own stamp, which is
          what actually gates race-ready, and can disagree with ``connected`` between
          reconcile runs.
        - ``weight_kg`` / ``weight_as_of``: current weight and the snapshot timestamp.
        - ``height_cm``, ``weight_90d_min``, ``weight_90d_max``, ``height_90d_min``,
          ``height_90d_max``: always None today — see the module docstring.
        - ``unavailable_reason``: why those are None.

    """
    method = getattr(user.__class__, "VerificationMethod", None)
    panel: dict = {
        "configured": zwift_client.is_configured(),
        "connected": False,
        "connected_at": None,
        "zwid": None,
        "verified_via_zauth": bool(
            method and user.zwid_verification_method == method.ZAUTH and user.zwid_verified
        ),
        "verified_at": user.zwid_verified_at,
        "weight_kg": None,
        "weight_as_of": None,
        # Pending service-side work; see the module docstring.
        "height_cm": None,
        "weight_90d_min": None,
        "weight_90d_max": None,
        "height_90d_min": None,
        "height_90d_max": None,
        "unavailable_reason": UNAVAILABLE_REASON,
    }
    if not panel["configured"]:
        return panel

    status = zwift_client.get_connection_status(str(user.pk))
    if not status or not status.get("connected"):
        return panel
    panel["connected"] = True
    panel["connected_at"] = _dt(status.get("connected_at"))
    panel["zwid"] = status.get("zwid")

    profile = zwift_client.get_racing_profile(str(user.pk))
    if not profile:
        # Connected but no snapshot yet (or the fetch failed). Leave the metrics
        # unavailable rather than implying a weight of zero.
        logfire.info("Zauth panel: connected with no racing profile", user_id=user.pk)
        return panel
    panel["weight_kg"] = _weight_kg(profile)
    panel["weight_as_of"] = _dt(profile.get("fetched_at"))
    return panel

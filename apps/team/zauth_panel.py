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

Height and the 90-day ranges are **stored by the service but not exposed on its API**:
``ZwiftRacingProfileSnapshot`` already accumulates ``weight_in_grams``,
``height_in_millimeters`` and ``captured_at``, deduped on change. Closing those rows is
therefore an API-exposure change in the service (add height to its profile response; add
min/max over the snapshot window), not new data capture. Until then this module reports
them as explicitly unavailable rather than blank or zero, because a blank reads as "the
rider is 0 kg" and a reviewer would act on it. ``UNAVAILABLE_REASON`` is the single place
the UI explains the gap.

Note the snapshot series only reaches back to when snapshotting was deployed, so a
90-day range will be short until it fills.
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


def _kg(grams: object) -> float | None:
    """Convert a gram value to kilograms.

    Args:
        grams: A raw gram value from the service payload.

    Returns:
        Weight in kg to one decimal, or None when absent, non-numeric or non-positive.
        ``bool`` is rejected explicitly since it is a subclass of ``int``.

    """
    if isinstance(grams, bool) or not isinstance(grams, (int, float)) or grams <= 0:
        return None
    return round(grams / 1000, 1)


def _profile_weight_grams(profile: dict) -> object:
    """Pick the rider's LIVE profile weight, never the competition-metrics one.

    Zwift returns two weights and they routinely disagree:

    - the profile's top-level ``weight``, which changes the moment the rider edits it;
    - ``competitionMetrics.weightInGrams``, documented as "weight at snapshot time" —
      frozen into Zwift's last racing-metrics computation alongside category, racing
      score and zFTP, so it can be days stale.

    A reviewer checking a submitted weight wants the live one, so this reads that and
    deliberately does **not** fall back to the metrics weight: showing a stale value
    under a fresh timestamp would be worse than showing nothing.

    Args:
        profile: The racing-profile dict from the zauth service.

    Returns:
        The raw gram value, or None when unavailable.

    """
    # Preferred: a denormalized field, if/when the service publishes one.
    grams = profile.get("profile_weight_in_grams")
    if grams is not None:
        return grams
    # Until then, read it out of the passed-through payload. `data` is the service's
    # own republication of the profile, and `weight` is its top-level live weight.
    data = profile.get("data")
    return data.get("weight") if isinstance(data, dict) else None


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
        - ``weight_kg`` / ``weight_as_of``: the rider's LIVE profile weight and when the
          service last read it. Never the competition-metrics weight — see
          :func:`_profile_weight_grams`.
        - ``metrics_weight_kg``: the competition-metrics weight, for reference.
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
        "metrics_weight_kg": None,
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
    panel["weight_kg"] = _kg(_profile_weight_grams(profile))
    # `fetched_at` is when the service last read Zwift, which is the correct "as of" for
    # a live value. It would be the wrong label for the metrics weight below.
    panel["weight_as_of"] = _dt(profile.get("fetched_at"))
    # Kept for reference (not currently rendered): the weight Zwift actually raced this
    # rider at, per its last metrics snapshot.
    panel["metrics_weight_kg"] = _kg(profile.get("weight_in_grams"))
    return panel

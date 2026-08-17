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

Height and the 90-day ranges come from the service's ``profile-stats`` endpoint, which
summarizes its deduped ``ZwiftRacingProfileSnapshot`` history. Two things to keep in mind
when reading those numbers:

- **The windowed weight is the competition-metrics weight**, not the live profile weight
  shown at the top of the card — it is the series of values Zwift actually raced and
  categorised the rider at, which is the useful anti-sandbagging signal but is a
  different quantity from the one the rider edits.
- **The window is only as deep as the history.** Snapshots are deduped on change and only
  reach back to when snapshotting was deployed (height later still), so a "90-day" range
  can rest on very few points. ``count`` is surfaced so the page can say how many.

Anything the service cannot supply is reported as explicitly unavailable rather than
blank or zero, because a blank reads as "the rider is 0 kg" and a reviewer would act on
it. ``UNAVAILABLE_REASON`` is the single place the UI explains a gap.
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

#: A 90-day weight swing at or above this is highlighted for the reviewer. Not a verdict —
#: riders legitimately fluctuate — just the threshold where it is worth a second look.
SWING_ATTENTION_KG = 2.0


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


def _drift(live_kg: float | None, metrics_kg: float | None) -> str | None:
    """Describe how far the live weight has moved since Zwift's metrics snapshot.

    A gap here is the interesting signal for a reviewer: it means the rider changed
    their weight after Zwift last computed their category and racing score.

    Args:
        live_kg: The rider's current profile weight in kg.
        metrics_kg: The weight inside Zwift's last competition-metrics snapshot.

    Returns:
        A signed string like ``"+0.4"`` / ``"-0.1"``, or None when either weight is
        missing or the two agree (nothing worth drawing attention to).

    """
    if live_kg is None or metrics_kg is None:
        return None
    delta = round(live_kg - metrics_kg, 1)
    if delta == 0:
        return None
    return f"{delta:+.1f}"


def _cm(millimeters: object) -> float | None:
    """Convert a millimetre height to centimetres.

    Args:
        millimeters: A raw millimetre value from the service payload.

    Returns:
        Height in cm to one decimal, or None when absent, non-numeric or non-positive.

    """
    if isinstance(millimeters, bool) or not isinstance(millimeters, (int, float)) or millimeters <= 0:
        return None
    return round(millimeters / 10, 1)


def _window(stats: dict | None, field: str, *, days: int = 90) -> dict | None:
    """Pull one metric's summary out of a ``profile-stats`` response.

    Args:
        stats: The service's profile-stats payload, or None.
        field: The snapshot field name to read.
        days: The rolling window in days.

    Returns:
        The ``{"min", "max", "first", "last", "count"}`` dict, or None when the payload
        is missing, malformed, or carries no data for that metric in the window.

    """
    if not isinstance(stats, dict):
        return None
    window = (stats.get("windows") or {}).get(f"{days}d")
    if not isinstance(window, dict):
        return None
    entry = window.get(field)
    return entry if isinstance(entry, dict) else None


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
        - ``metrics_weight_kg``: the weight frozen into Zwift's last metrics computation,
          i.e. the weight it actually raced and categorised this rider at.
        - ``weight_drift``: signed kg the live weight has moved since that snapshot, as a
          display string, or None when the two agree or either is missing.
        - ``height_cm``: the rider's current Zwift height.
        - ``weight_90d_min`` / ``weight_90d_max`` / ``height_90d_min`` /
          ``height_90d_max``: the 90-day range from the snapshot history, or None when
          there is no history for that metric yet.
        - ``weight_90d_count`` / ``height_90d_count``: how many snapshots each range rests
          on, so the page can show that a range is thin rather than implying confidence.
        - ``weight_90d_swing`` / ``weight_90d_swing_high``: max minus min, and whether it
          crosses :data:`SWING_ATTENTION_KG`.
        - ``unavailable_reason``: shown against anything still None.

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
        "weight_drift": None,
        # Pending service-side work; see the module docstring.
        "height_cm": None,
        "weight_90d_min": None,
        "weight_90d_max": None,
        "weight_90d_count": 0,
        "weight_90d_swing": None,
        "weight_90d_swing_high": False,
        "height_90d_min": None,
        "height_90d_max": None,
        "height_90d_count": 0,
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
    panel["metrics_weight_kg"] = _kg(profile.get("weight_in_grams"))
    panel["weight_drift"] = _drift(panel["weight_kg"], panel["metrics_weight_kg"])

    stats = zwift_client.get_profile_stats(str(user.pk))
    current = (stats or {}).get("current")
    if isinstance(current, dict):
        panel["height_cm"] = _cm(current.get("height_in_millimeters"))
    weight_window = _window(stats, "weight_in_grams")
    if weight_window:
        panel["weight_90d_min"] = _kg(weight_window.get("min"))
        panel["weight_90d_max"] = _kg(weight_window.get("max"))
        panel["weight_90d_count"] = weight_window.get("count") or 0
        if panel["weight_90d_min"] is not None and panel["weight_90d_max"] is not None:
            swing = round(panel["weight_90d_max"] - panel["weight_90d_min"], 1)
            panel["weight_90d_swing"] = swing
            # A couple of kilos inside a 90-day window is the thing a reviewer should
            # look at twice; it can move a rider across a racing category boundary.
            panel["weight_90d_swing_high"] = swing >= SWING_ATTENTION_KG
    height_window = _window(stats, "height_in_millimeters")
    if height_window:
        panel["height_90d_min"] = _cm(height_window.get("min"))
        panel["height_90d_max"] = _cm(height_window.get("max"))
        panel["height_90d_count"] = height_window.get("count") or 0
    return panel

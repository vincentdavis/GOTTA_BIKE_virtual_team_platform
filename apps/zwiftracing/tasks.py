"""Background tasks for Zwift Racing.

Uses Django 6.0 background tasks feature with django-tasks database backend.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

import logfire
from constance import config
from django.tasks import task  # ty:ignore[unresolved-import]
from django.utils import timezone

from apps.zwiftracing.models import ZRRider
from apps.zwiftracing.zr_client import get_club


def _parse_decimal(value: float | int | str | None) -> Decimal | None:
    """Parse a value to Decimal, returning None if invalid.

    Returns:
        Decimal value or None if parsing fails.

    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_int(value: int | str | None) -> int | None:
    """Parse a value to int, returning None if invalid.

    Returns:
        int value or None if parsing fails.

    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _map_rider_to_model(rider: dict) -> dict:
    """Map API rider data to ZRRider model fields.

    Args:
        rider: Rider data from the API.

    Returns:
        Dictionary of model field values.

    """
    power = rider.get("power", {}) or {}
    race = rider.get("race", {}) or {}
    race_current = race.get("current", {}) or {}
    race_last = race.get("last", {}) or {}
    race_max30 = race.get("max30", {}) or {}
    race_max90 = race.get("max90", {}) or {}
    handicaps = rider.get("handicaps", {}) or {}
    handicaps_profile = handicaps.get("profile", {}) or {}
    phenotype = rider.get("phenotype", {}) or {}
    phenotype_scores = phenotype.get("scores", {}) or {}
    club = rider.get("club", {}) or {}
    seed = rider.get("seed", {}) or {}
    seed_factors = seed.get("factors", {}) or {}
    velo = rider.get("velo", {}) or {}
    velo_factors = velo.get("factors", {}) or {}

    return {
        # Basic info
        "name": rider.get("name") or "",
        "gender": rider.get("gender") or "",
        "country": rider.get("country") or "",
        "age": rider.get("age") or "",
        "height": _parse_int(rider.get("height")),
        "weight": _parse_decimal(rider.get("weight")),
        # ZwiftPower category
        "zp_category": rider.get("zpCategory") or "",
        "zp_ftp": _parse_int(rider.get("zpFTP")),
        # Power data - w/kg
        "power_wkg5": _parse_decimal(power.get("wkg5")),
        "power_wkg15": _parse_decimal(power.get("wkg15")),
        "power_wkg30": _parse_decimal(power.get("wkg30")),
        "power_wkg60": _parse_decimal(power.get("wkg60")),
        "power_wkg120": _parse_decimal(power.get("wkg120")),
        "power_wkg300": _parse_decimal(power.get("wkg300")),
        "power_wkg1200": _parse_decimal(power.get("wkg1200")),
        # Power data - watts
        "power_w5": _parse_int(power.get("w5")),
        "power_w15": _parse_int(power.get("w15")),
        "power_w30": _parse_int(power.get("w30")),
        "power_w60": _parse_int(power.get("w60")),
        "power_w120": _parse_int(power.get("w120")),
        "power_w300": _parse_int(power.get("w300")),
        "power_w1200": _parse_int(power.get("w1200")),
        # Power metrics
        "power_cp": _parse_decimal(power.get("CP")),
        "power_awc": _parse_decimal(power.get("AWC")),
        "power_compound_score": _parse_decimal(power.get("compoundScore")),
        # Race rating - current
        "race_current_rating": _parse_decimal(race_current.get("rating")),
        "race_current_date": _parse_int(race_current.get("date")),
        "race_current_category": (race_current.get("mixed", {}) or {}).get("category") or "",
        "race_current_category_num": _parse_int((race_current.get("mixed", {}) or {}).get("number")),
        # Race rating - last
        "race_last_rating": _parse_decimal(race_last.get("rating")),
        "race_last_date": _parse_int(race_last.get("date")),
        "race_last_category": (race_last.get("mixed", {}) or {}).get("category") or "",
        "race_last_category_num": _parse_int((race_last.get("mixed", {}) or {}).get("number")),
        # Race rating - max30
        "race_max30_rating": _parse_decimal(race_max30.get("rating")),
        "race_max30_date": _parse_int(race_max30.get("date")),
        "race_max30_expires": _parse_int(race_max30.get("expires")),
        "race_max30_category": (race_max30.get("mixed", {}) or {}).get("category") or "",
        "race_max30_category_num": _parse_int((race_max30.get("mixed", {}) or {}).get("number")),
        # Race rating - max90
        "race_max90_rating": _parse_decimal(race_max90.get("rating")),
        "race_max90_date": _parse_int(race_max90.get("date")),
        "race_max90_expires": _parse_int(race_max90.get("expires")),
        "race_max90_category": (race_max90.get("mixed", {}) or {}).get("category") or "",
        "race_max90_category_num": _parse_int((race_max90.get("mixed", {}) or {}).get("number")),
        # Race stats
        "race_finishes": race.get("finishes", 0) or 0,
        "race_dnfs": race.get("dnfs", 0) or 0,
        "race_wins": race.get("wins", 0) or 0,
        "race_podiums": race.get("podiums", 0) or 0,
        # Handicaps
        "handicap_flat": _parse_decimal(handicaps_profile.get("flat")),
        "handicap_rolling": _parse_decimal(handicaps_profile.get("rolling")),
        "handicap_hilly": _parse_decimal(handicaps_profile.get("hilly")),
        "handicap_mountainous": _parse_decimal(handicaps_profile.get("mountainous")),
        # Phenotype
        "phenotype_value": phenotype.get("value") or "",
        "phenotype_bias": _parse_decimal(phenotype.get("bias")),
        "phenotype_sprinter": _parse_decimal(phenotype_scores.get("sprinter")),
        "phenotype_puncheur": _parse_decimal(phenotype_scores.get("puncheur")),
        "phenotype_pursuiter": _parse_decimal(phenotype_scores.get("pursuiter")),
        "phenotype_climber": _parse_decimal(phenotype_scores.get("climber")),
        "phenotype_tt": _parse_decimal(phenotype_scores.get("tt")),
        # Club info
        "club_id": _parse_int(club.get("id")),
        "club_name": club.get("name") or "",
        # Seed ratings
        "seed_race": _parse_decimal(seed.get("race")),
        "seed_time_trial": _parse_decimal(seed.get("timeTrial")),
        "seed_endurance": _parse_decimal(seed_factors.get("endurance")),
        "seed_pursuit": _parse_decimal(seed_factors.get("pursuit")),
        "seed_sprint": _parse_decimal(seed_factors.get("sprint")),
        "seed_punch": _parse_decimal(seed_factors.get("punch")),
        "seed_climb": _parse_decimal(seed_factors.get("climb")),
        "seed_tt_factor": _parse_decimal(seed_factors.get("timeTrial")),
        # Velo ratings
        "velo_race": _parse_decimal(velo.get("race")),
        "velo_time_trial": _parse_decimal(velo.get("timeTrial")),
        "velo_endurance": _parse_decimal(velo_factors.get("endurance")),
        "velo_pursuit": _parse_decimal(velo_factors.get("pursuit")),
        "velo_sprint": _parse_decimal(velo_factors.get("sprint")),
        "velo_punch": _parse_decimal(velo_factors.get("punch")),
        "velo_climb": _parse_decimal(velo_factors.get("climb")),
        "velo_tt_factor": _parse_decimal(velo_factors.get("timeTrial")),
    }


@task
def sync_zr_riders(from_id: int | None = None) -> dict:
    """Sync ZRRider data from Zwift Racing API.

    Fetches club riders from the Zwift Racing API and updates the database.
    Handles pagination (if >= 999 riders) and rate limiting (429 status).

    Args:
        from_id: Optional rider ID to paginate from.

    Returns:
        dict with sync status and counts.

    """
    with logfire.span("sync_zr_riders", from_id=from_id):
        club_id = config.ZWIFTPOWER_TEAM_ID

        # Call the API
        status_code, data = get_club(club_id, from_id)

        # Handle rate limiting (429)
        if status_code == 429:
            retry_after = int(data.get("retryAfter", 600))
            run_at = timezone.now() + timedelta(seconds=retry_after)
            logfire.warning(f"Rate limited, retrying in {retry_after} seconds at {run_at}")
            sync_zr_riders.using(run_after=run_at).enqueue(from_id)
            return {
                "status": "rate_limited",
                "retry_after": retry_after,
                "from_id": from_id,
            }

        riders = data.get("riders", [])
        if not riders:
            logfire.warning("No riders data returned from Zwift Racing API")
            return {"status": "complete", "processed": 0, "created": 0, "updated": 0}

        created_count = 0
        updated_count = 0

        for rider in riders:
            zwid = rider.get("riderId")
            if not zwid:
                continue

            defaults = _map_rider_to_model(rider)

            obj, created = ZRRider.objects.update_or_create(
                zwid=zwid,
                defaults=defaults,
            )

            if created:
                created_count += 1
                logfire.info(f"Created ZR rider: {obj.name} ({zwid})")
            else:
                updated_count += 1

        # Handle pagination - if we got 999 riders, there may be more
        if len(riders) >= 999:
            last_rider_id = riders[-1]["riderId"]
            run_at = timezone.now() + timedelta(seconds=630)
            logfire.info(f"Paginating: got {len(riders)} riders, continuing from {last_rider_id} at {run_at}")
            sync_zr_riders.using(run_after=run_at).enqueue(last_rider_id)
            return {
                "status": "paginating",
                "processed": len(riders),
                "created": created_count,
                "updated": updated_count,
                "next_from_id": last_rider_id,
            }

        logfire.info(f"ZR Riders sync complete: {created_count} created, {updated_count} updated")
        return {
            "status": "complete",
            "processed": len(riders),
            "created": created_count,
            "updated": updated_count,
        }

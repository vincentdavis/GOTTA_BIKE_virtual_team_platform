"""Background tasks for ZwiftPower.

Uses Django 6.0 background tasks feature with django-tasks database backend.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import logfire
from django.tasks import task  # ty:ignore[unresolved-import]
from django.utils import timezone

from apps.zwiftpower.models import ZPEvent, ZPRiderResults, ZPTeamRiders
from apps.zwiftpower.zp_client import ZPClient


def _parse_decimal(value: str | int | float | None) -> Decimal | None:
    """Parse a value to Decimal, returning None if invalid.

    Returns:
        Decimal value or None if parsing fails.

    """
    if value is None or value == "" or value == 0:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_int(value: str | int | None) -> int | None:
    """Parse a value to int, returning None if invalid.

    Returns:
        int value or None if parsing fails.

    """
    if value is None or value == "" or value == 0:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _extract_first_value(value: list | str | int | None) -> str | int | None:
    """Extract first value from array or return value as-is.

    Returns:
        First element if value is a list, otherwise the value itself.

    """
    if isinstance(value, list) and len(value) > 0:
        return value[0]
    return value  # ty:ignore[invalid-return-type]


@task
def update_team_riders() -> dict:
    """Fetch team riders from ZwiftPower and update the database.

    - Creates new riders if they don't exist
    - Updates existing riders with the latest data
    - Sets date_left for riders no longer on the team

    Returns:
        dict with counts of created, updated, and left riders.

    """
    with logfire.span("update_team_riders"):
        created_count = 0
        updated_count = 0
        left_count = 0

        with ZPClient() as client:
            riders_data = client.fetch_team_riders()

        if not riders_data:
            logfire.warning("No riders data returned from ZwiftPower")
            return {"created": 0, "updated": 0, "left": 0, "error": "No data returned"}

        # Track which zwids are in the current roster
        current_zwids = set()

        for rider in riders_data:
            zwid = rider.get("zwid")
            if not zwid:
                continue

            current_zwids.add(zwid)

            # Extract and parse values
            ftp_raw = _extract_first_value(rider.get("ftp"))
            weight_raw = _extract_first_value(rider.get("w"))

            defaults = {
                "aid": str(rider.get("aid", "") or ""),
                "name": rider.get("name", "").strip(),
                "flag": rider.get("flag", ""),
                "age": rider.get("age", ""),
                "div": rider.get("div", 0) or 0,
                "divw": rider.get("divw", 0) or 0,
                "r": str(rider.get("r", "") or ""),
                "rank": _parse_decimal(rider.get("rank")),
                "ftp": _parse_int(ftp_raw),
                "weight": _parse_decimal(weight_raw),
                "skill": rider.get("skill", 0) or 0,
                "skill_race": rider.get("skill_race", 0) or 0,
                "skill_seg": rider.get("skill_seg", 0) or 0,
                "skill_power": rider.get("skill_power", 0) or 0,
                "distance": rider.get("distance", 0) or 0,
                "climbed": rider.get("climbed", 0) or 0,
                "energy": rider.get("energy", 0) or 0,
                "time": rider.get("time", 0) or 0,
                "h_1200_watts": _parse_int(rider.get("h_1200_watts")),
                "h_1200_wkg": _parse_decimal(rider.get("h_1200_wkg")),
                "h_15_watts": _parse_int(rider.get("h_15_watts")),
                "h_15_wkg": _parse_decimal(rider.get("h_15_wkg")),
                "status": rider.get("status", ""),
                "reg": bool(rider.get("reg", 0)),
                "email": rider.get("email", ""),
                "zada": rider.get("zada", 0) or 0,
                "date_left": None,  # Clear date_left if rider is back on team
            }

            obj, created = ZPTeamRiders.objects.update_or_create(
                zwid=zwid,
                defaults=defaults,
            )

            if created:
                created_count += 1
                logfire.info(f"Created rider: {obj.name} ({zwid})")
            else:
                updated_count += 1

        # Mark riders who are no longer on the team
        left_riders = ZPTeamRiders.objects.filter(
            date_left__isnull=True,
        ).exclude(
            zwid__in=current_zwids,
        )

        for rider in left_riders:
            rider.date_left = timezone.now()
            rider.save(update_fields=["date_left"])
            left_count += 1
            logfire.info(f"Rider left team: {rider.name} ({rider.zwid})")

        logfire.info(
            f"Team riders update complete: {created_count} created, {updated_count} updated, {left_count} left"
        )

        return {
            "created": created_count,
            "updated": updated_count,
            "left": left_count,
        }


@task
def update_team_results() -> dict:
    """Fetch team results from ZwiftPower and update the database.

    - Creates new events if they don't exist
    - Creates or updates rider results for each event

    Returns:
        dict with counts of events and results created/updated.

    """
    with logfire.span("update_team_results"):
        events_created = 0
        events_updated = 0
        results_created = 0
        results_updated = 0

        with ZPClient() as client:
            data = client.fetch_team_results()

        events_data = data.get("events", {})
        results_data = data.get("data", [])

        if not events_data and not results_data:
            logfire.warning("No team results data returned from ZwiftPower")
            return {"events_created": 0, "events_updated": 0, "results_created": 0, "results_updated": 0}

        # First, create/update all events
        event_cache: dict[int, ZPEvent] = {}
        for zid_str, event_info in events_data.items():
            zid = int(zid_str)
            event_date = datetime.fromtimestamp(event_info.get("date", 0), tz=UTC)

            event, created = ZPEvent.objects.update_or_create(
                zid=zid,
                defaults={
                    "title": event_info.get("title", ""),
                    "event_date": event_date,
                },
            )
            event_cache[zid] = event

            if created:
                events_created += 1
                logfire.info(f"Created event: {event.title} ({zid})")
            else:
                events_updated += 1

        # Now process rider results
        for result in results_data:
            zid = int(result.get("zid", 0))
            zwid = result.get("zwid")

            if not zid or not zwid:
                continue

            # Get or fetch the event
            if zid not in event_cache:
                event = ZPEvent.objects.filter(zid=zid).first()
                if not event:
                    logfire.warning(f"Event {zid} not found for result, skipping")
                    continue
                event_cache[zid] = event
            else:
                event = event_cache[zid]

            # Extract values from arrays (ZP returns [value, comparison_value])
            time_val = _extract_first_value(result.get("time"))
            weight_val = _extract_first_value(result.get("weight"))
            height_val = _extract_first_value(result.get("height"))
            avg_power_val = _extract_first_value(result.get("avg_power"))
            avg_wkg_val = _extract_first_value(result.get("avg_wkg"))
            np_val = _extract_first_value(result.get("np"))
            wftp_val = _extract_first_value(result.get("wftp"))
            wkg_ftp_val = _extract_first_value(result.get("wkg_ftp"))
            avg_hr_val = _extract_first_value(result.get("avg_hr"))
            max_hr_val = _extract_first_value(result.get("max_hr"))

            # Power curve values
            w5_val = _extract_first_value(result.get("w5"))
            w15_val = _extract_first_value(result.get("w15"))
            w30_val = _extract_first_value(result.get("w30"))
            w60_val = _extract_first_value(result.get("w60"))
            w120_val = _extract_first_value(result.get("w120"))
            w300_val = _extract_first_value(result.get("w300"))
            w1200_val = _extract_first_value(result.get("w1200"))

            wkg5_val = _extract_first_value(result.get("wkg5"))
            wkg15_val = _extract_first_value(result.get("wkg15"))
            wkg30_val = _extract_first_value(result.get("wkg30"))
            wkg60_val = _extract_first_value(result.get("wkg60"))
            wkg120_val = _extract_first_value(result.get("wkg120"))
            wkg300_val = _extract_first_value(result.get("wkg300"))
            wkg1200_val = _extract_first_value(result.get("wkg1200"))

            defaults = {
                "event": event,
                "res_id": result.get("res_id", "") or "",
                "name": (result.get("name", "") or "").strip(),
                "flag": result.get("flag", "") or "",
                "age": result.get("age", "") or "",
                "male": bool(result.get("male", 1)),
                "tid": str(result.get("tid", "") or ""),
                "tname": result.get("tname", "") or "",
                "pos": _parse_int(result.get("pos")),
                "position_in_cat": _parse_int(result.get("position_in_cat")),
                "category": result.get("category", "") or "",
                "label": result.get("label", "") or "",
                "time_seconds": _parse_decimal(time_val),
                "time_gun": _parse_decimal(result.get("time_gun")),
                "gap": _parse_decimal(result.get("gap")),
                "ftp": _parse_int(result.get("ftp")),
                "weight": _parse_decimal(weight_val),
                "height": _parse_int(height_val),
                "avg_power": _parse_int(avg_power_val),
                "avg_wkg": _parse_decimal(avg_wkg_val),
                "np": _parse_int(np_val),
                "wftp": _parse_int(wftp_val),
                "wkg_ftp": _parse_decimal(wkg_ftp_val),
                "w5": _parse_int(w5_val),
                "w15": _parse_int(w15_val),
                "w30": _parse_int(w30_val),
                "w60": _parse_int(w60_val),
                "w120": _parse_int(w120_val),
                "w300": _parse_int(w300_val),
                "w1200": _parse_int(w1200_val),
                "wkg5": _parse_decimal(wkg5_val),
                "wkg15": _parse_decimal(wkg15_val),
                "wkg30": _parse_decimal(wkg30_val),
                "wkg60": _parse_decimal(wkg60_val),
                "wkg120": _parse_decimal(wkg120_val),
                "wkg300": _parse_decimal(wkg300_val),
                "wkg1200": _parse_decimal(wkg1200_val),
                "avg_hr": _parse_int(avg_hr_val),
                "max_hr": _parse_int(max_hr_val),
                "hrm": bool(result.get("hrm", 0)),
                "div": result.get("div", 0) or 0,
                "divw": result.get("divw", 0) or 0,
                "skill": _parse_decimal(result.get("skill")),
                "skill_gain": _parse_decimal(result.get("skill_gain")),
                "zada": result.get("zada", 0) or 0,
                "reg": bool(result.get("reg", 0)),
                "penalty": result.get("penalty", "") or "",
                "upg": bool(result.get("upg", 0)),
                "f_t": (result.get("f_t", "") or "").strip(),
            }

            _, created = ZPRiderResults.objects.update_or_create(
                zid=zid,
                zwid=zwid,
                defaults=defaults,
            )

            if created:
                results_created += 1
            else:
                results_updated += 1

        logfire.info(
            f"Team results update complete: {events_created} events created, {events_updated} events updated, "
            f"{results_created} results created, {results_updated} results updated"
        )

        return {
            "events_created": events_created,
            "events_updated": events_updated,
            "results_created": results_created,
            "results_updated": results_updated,
        }

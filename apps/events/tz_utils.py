"""Timezone conversion utilities for availability grids.

All grid data is stored as UTC. These functions convert between a local
timezone and UTC for display and persistence.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo


def convert_local_to_utc(
    start_date: date,
    end_date: date,
    start_time: str,
    end_time: str,
    source_tz: str,
) -> tuple[date, date, str, str]:
    """Convert local start/end date+time to UTC equivalents.

    Used by the builder on save. The admin enters dates and times in their
    chosen timezone; this function returns the UTC dates and times that
    should be stored on the grid.

    Args:
        start_date: Local start date.
        end_date: Local end date.
        start_time: Local start time as "HH:MM".
        end_time: Local end time as "HH:MM".
        source_tz: IANA timezone string (e.g. "America/New_York").

    Returns:
        Tuple of (utc_start_date, utc_end_date, utc_start_time, utc_end_time).

    """
    if source_tz == "UTC":
        return start_date, end_date, start_time, end_time

    tz = ZoneInfo(source_tz)
    utc = ZoneInfo("UTC")

    local_start_time = time.fromisoformat(start_time)
    local_end_time = time.fromisoformat(end_time)

    local_start_dt = datetime.combine(start_date, local_start_time, tzinfo=tz)
    local_end_dt = datetime.combine(end_date, local_end_time, tzinfo=tz)

    utc_start_dt = local_start_dt.astimezone(utc)
    utc_end_dt = local_end_dt.astimezone(utc)

    return (
        utc_start_dt.date(),
        utc_end_dt.date(),
        utc_start_dt.strftime("%H:%M"),
        utc_end_dt.strftime("%H:%M"),
    )


def convert_blocked_cells_to_utc(
    blocked_cells: list[dict],
    source_tz: str,
    slot_duration: int,
) -> list[dict]:
    """Convert blocked cells from local timezone to UTC.

    Each blocked cell is a dict with "date" and "time" keys representing
    the start of a slot in the source timezone. We convert each to UTC.

    Args:
        blocked_cells: List of {"date": "YYYY-MM-DD", "time": "HH:MM"} dicts.
        source_tz: IANA timezone string.
        slot_duration: Slot duration in minutes (unused but kept for API consistency).

    Returns:
        List of {"date": "YYYY-MM-DD", "time": "HH:MM"} dicts in UTC.

    """
    if source_tz == "UTC" or not blocked_cells:
        return blocked_cells

    tz = ZoneInfo(source_tz)
    utc = ZoneInfo("UTC")
    result = []
    for cell in blocked_cells:
        local_dt = datetime.combine(
            date.fromisoformat(cell["date"]),
            time.fromisoformat(cell["time"]),
            tzinfo=tz,
        )
        utc_dt = local_dt.astimezone(utc)
        result.append({
            "date": utc_dt.strftime("%Y-%m-%d"),
            "time": utc_dt.strftime("%H:%M"),
        })
    return result


def convert_grid_to_local(
    utc_dates: list[str],
    utc_time_slots: list[str],
    blocked_cells: list[dict],
    target_tz: str,
) -> dict:
    """Convert a UTC grid definition to local-timezone display coordinates.

    Used by the respond and results views. Every UTC (date, time) cell is
    converted to the target timezone. The result includes mappings so JS
    can render local labels while still posting UTC coordinates.

    Args:
        utc_dates: List of UTC date strings ("YYYY-MM-DD").
        utc_time_slots: List of UTC time strings ("HH:MM").
        blocked_cells: List of {"date": ..., "time": ...} dicts in UTC.
        target_tz: IANA timezone string for display.

    Returns:
        Dict with keys:
            display_dates: sorted list of local date strings
            display_time_slots: sorted list of local time strings
            cell_map: {"local_date|local_time": {"date": utc_date, "time": utc_time}}
            reverse_map: {"utc_date|utc_time": "local_date|local_time"}
            display_blocked: set of "local_date|local_time" keys
            valid_cells: set of "local_date|local_time" keys that have a UTC counterpart

    """
    if target_tz == "UTC":
        blocked_set = {f"{c['date']}|{c['time']}" for c in blocked_cells}
        cell_map = {}
        reverse_map = {}
        for d in utc_dates:
            for t in utc_time_slots:
                key = f"{d}|{t}"
                cell_map[key] = {"date": d, "time": t}
                reverse_map[key] = key
        return {
            "display_dates": list(utc_dates),
            "display_time_slots": list(utc_time_slots),
            "cell_map": cell_map,
            "reverse_map": reverse_map,
            "display_blocked": blocked_set,
            "valid_cells": set(cell_map.keys()),
        }

    tz = ZoneInfo(target_tz)
    utc = ZoneInfo("UTC")

    local_dates_set: set[str] = set()
    local_times_set: set[str] = set()
    cell_map: dict[str, dict[str, str]] = {}
    reverse_map: dict[str, str] = {}

    for d_str in utc_dates:
        for t_str in utc_time_slots:
            utc_dt = datetime.combine(
                date.fromisoformat(d_str),
                time.fromisoformat(t_str),
                tzinfo=utc,
            )
            local_dt = utc_dt.astimezone(tz)
            local_d = local_dt.strftime("%Y-%m-%d")
            local_t = local_dt.strftime("%H:%M")
            local_dates_set.add(local_d)
            local_times_set.add(local_t)

            local_key = f"{local_d}|{local_t}"
            utc_key = f"{d_str}|{t_str}"
            cell_map[local_key] = {"date": d_str, "time": t_str}
            reverse_map[utc_key] = local_key

    blocked_utc_set = {f"{c['date']}|{c['time']}" for c in blocked_cells}
    display_blocked: set[str] = set()
    for utc_key, local_key in reverse_map.items():
        if utc_key in blocked_utc_set:
            display_blocked.add(local_key)

    display_dates = sorted(local_dates_set)
    display_time_slots = sorted(local_times_set)

    return {
        "display_dates": display_dates,
        "display_time_slots": display_time_slots,
        "cell_map": cell_map,
        "reverse_map": reverse_map,
        "display_blocked": display_blocked,
        "valid_cells": set(cell_map.keys()),
    }


# Common IANA timezone choices for the builder dropdown.
# Values use canonical IANA names (same as the profile form) so the
# user's profile timezone matches an option in the dropdown.
TIMEZONE_CHOICES: list[tuple[str, str]] = [
    ("UTC", "UTC"),
    ("America/New_York", "America / New York (Eastern)"),
    ("America/Chicago", "America / Chicago (Central)"),
    ("America/Denver", "America / Denver (Mountain)"),
    ("America/Los_Angeles", "America / Los Angeles (Pacific)"),
    ("America/Anchorage", "America / Anchorage (Alaska)"),
    ("Pacific/Honolulu", "Pacific / Honolulu (Hawaii)"),
    ("America/Halifax", "America / Halifax (Atlantic)"),
    ("America/Toronto", "America / Toronto"),
    ("America/Winnipeg", "America / Winnipeg"),
    ("America/Edmonton", "America / Edmonton"),
    ("America/Vancouver", "America / Vancouver"),
    ("Europe/London", "Europe / London"),
    ("Europe/Paris", "Europe / Paris"),
    ("Europe/Berlin", "Europe / Berlin"),
    ("Europe/Amsterdam", "Europe / Amsterdam"),
    ("Europe/Rome", "Europe / Rome"),
    ("Europe/Madrid", "Europe / Madrid"),
    ("Europe/Stockholm", "Europe / Stockholm"),
    ("Europe/Helsinki", "Europe / Helsinki"),
    ("Europe/Athens", "Europe / Athens"),
    ("Europe/Moscow", "Europe / Moscow"),
    ("Australia/Sydney", "Australia / Sydney"),
    ("Australia/Melbourne", "Australia / Melbourne"),
    ("Australia/Brisbane", "Australia / Brisbane"),
    ("Australia/Perth", "Australia / Perth"),
    ("Australia/Adelaide", "Australia / Adelaide"),
    ("Pacific/Auckland", "Pacific / Auckland (New Zealand)"),
    ("Asia/Tokyo", "Asia / Tokyo"),
    ("Asia/Singapore", "Asia / Singapore"),
    ("Asia/Kolkata", "Asia / Kolkata (India)"),
    ("Asia/Dubai", "Asia / Dubai (Gulf)"),
    ("America/Sao_Paulo", "America / Sao Paulo"),
    ("America/Argentina/Buenos_Aires", "America / Buenos Aires"),
    ("Africa/Johannesburg", "Africa / Johannesburg"),
]

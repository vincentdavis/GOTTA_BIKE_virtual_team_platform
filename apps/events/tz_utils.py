"""Timezone conversion utilities for availability grids.

All grid data is stored as UTC. These functions convert between a local
timezone and UTC for display and persistence.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def _span_minutes(start_time: str, end_time: str) -> int:
    """Minutes from ``start_time`` to ``end_time`` on the clock, wrapping past midnight.

    Equal times are a full day, as an end at or before the start means the next day.

    Returns:
        The span in minutes, more than 0 and at most a day.

    """
    start = time.fromisoformat(start_time)
    end = time.fromisoformat(end_time)
    minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return minutes if minutes > 0 else minutes + 24 * 60


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

    A grid stores one UTC start and end time for every day, so everything is
    worked out from the first local day: the UTC start is that day's local
    start, the UTC end is the UTC start plus the local span, and the UTC end
    date keeps the local day count. A range that crosses a daylight-saving
    change therefore keeps the same number of slots on every day (its later
    days show an hour off locally) instead of taking the start from one
    offset and the end from the other.

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

    utc_start_dt = datetime.combine(start_date, time.fromisoformat(start_time), tzinfo=tz).astimezone(utc)
    utc_end_dt = utc_start_dt + timedelta(minutes=_span_minutes(start_time, end_time))
    # The end date is the start date plus the local day count, not the UTC date of the
    # last day's end: an end that crosses midnight UTC (23:30 PDT is 06:30 UTC the next
    # day) would add a day, and grid.dates x grid.time_slots would produce an extra day
    # of slots. Counting days also keeps a start near UTC midnight from gaining or losing
    # a day when the range crosses a daylight-saving change.
    utc_end_date = utc_start_dt.date() + (end_date - start_date)

    return (
        utc_start_dt.date(),
        utc_end_date,
        utc_start_dt.strftime("%H:%M"),
        utc_end_dt.strftime("%H:%M"),
    )


def convert_utc_to_local_config(
    utc_start_date: date,
    utc_end_date: date,
    utc_start_time: str,
    utc_end_time: str,
    source_tz: str,
) -> tuple[date, date, str, str]:
    """Reverse of :func:`convert_local_to_utc` for re-editing a stored grid.

    Recovers the local start/end dates and times a grid was originally built with, so the
    builder can be pre-filled in the grid's own timezone. Like the forward conversion it
    works from the first day: the local start is the UTC start in the grid's timezone, and
    the local end is that plus the stored span, which also handles spans that cross
    midnight in either timezone and ranges that cross a daylight-saving change.

    Args:
        utc_start_date: Stored UTC start date.
        utc_end_date: Stored (anchored) UTC end date.
        utc_start_time: Stored UTC start time as "HH:MM".
        utc_end_time: Stored UTC end time as "HH:MM".
        source_tz: IANA timezone the grid was created in.

    Returns:
        Tuple of (local_start_date, local_end_date, local_start_time, local_end_time).

    """
    if source_tz == "UTC":
        return utc_start_date, utc_end_date, utc_start_time, utc_end_time

    tz = ZoneInfo(source_tz)
    utc = ZoneInfo("UTC")

    local_start_dt = datetime.combine(utc_start_date, time.fromisoformat(utc_start_time), tzinfo=utc).astimezone(tz)
    local_start_date = local_start_dt.date()
    local_start_time = local_start_dt.strftime("%H:%M")

    # The anchored UTC date span preserves the local day count.
    length_days = (utc_end_date - utc_start_date).days + 1
    local_end_date = local_start_date + timedelta(days=length_days - 1)

    local_end_time = (
        datetime.combine(date.min, time.fromisoformat(local_start_time))
        + timedelta(minutes=_span_minutes(utc_start_time, utc_end_time))
    ).strftime("%H:%M")

    return local_start_date, local_end_date, local_start_time, local_end_time


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
    start_time: str,
    end_time: str,
    slot_duration: int,
    blocked_cells: list[dict],
    target_tz: str,
) -> dict:
    """Convert a UTC grid definition to local-timezone display coordinates.

    Used by the respond and results views. Every UTC (date, time) cell is
    converted to the target timezone. The result includes mappings so JS
    can render local labels while still posting UTC coordinates.

    Args:
        utc_dates: List of UTC date strings ("YYYY-MM-DD").
        start_time: First race time on each day ("HH:MM").
        end_time: End of race availability on each day ("HH:MM").
        slot_duration: Minutes per availability slot.
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
    tz = ZoneInfo(target_tz)
    utc = ZoneInfo("UTC")

    local_dates_set: set[str] = set()
    local_times_set: set[str] = set()
    cell_map: dict[str, dict[str, str]] = {}
    reverse_map: dict[str, str] = {}

    for d_str in utc_dates:
        start = datetime.combine(
            date.fromisoformat(d_str),
            time.fromisoformat(start_time),
            tzinfo=utc,
        )
        end = datetime.combine(
            date.fromisoformat(d_str),
            time.fromisoformat(end_time),
            tzinfo=utc,
        )
        if end <= start:
            end += timedelta(days=1)
        current = start
        delta = timedelta(minutes=slot_duration)
        while current < end:
            t_str = current.strftime("%H:%M")
            current_d_str = current.strftime("%Y-%m-%d")
            local_dt = current.astimezone(tz)
            local_d = local_dt.strftime("%Y-%m-%d")
            local_t = local_dt.strftime("%H:%M")
            local_dates_set.add(local_d)
            local_times_set.add(local_t)

            local_key = f"{local_d}|{local_t}"
            utc_key = f"{current_d_str}|{t_str}"
            cell_map[local_key] = {"date": current_d_str, "time": t_str}
            reverse_map[utc_key] = local_key

            current += delta

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


def drop_fully_blocked_days(grid_data: dict) -> dict:
    """Filter a grid's display dates to those with at least one open cell.

    Operates on the output of :func:`convert_grid_to_local`: a display date is kept only
    when at least one of its valid cells is not blocked. Days where every slot is blocked
    are removed from ``display_dates``. The same dict is returned (``display_dates`` replaced).

    Args:
        grid_data: The dict returned by :func:`convert_grid_to_local`.

    Returns:
        The grid_data dict with fully-blocked days removed from ``display_dates``.

    """
    times = grid_data["display_time_slots"]
    blocked = grid_data["display_blocked"]
    valid = grid_data["valid_cells"]
    kept = []
    for d in grid_data["display_dates"]:
        day_cells = [f"{d}|{t}" for t in times if f"{d}|{t}" in valid]
        if any(cell not in blocked for cell in day_cells):
            kept.append(d)
    grid_data["display_dates"] = kept
    return grid_data


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

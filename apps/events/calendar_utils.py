"""iCalendar (.ics) and Google Calendar helpers for scheduled races.

Scheduled races store only a start (``slot_date`` + ``slot_time`` in UTC), so
calendar events use a fixed duration. The .ics is served from a public,
unguessable signed-token endpoint (see ``race_calendar_ics_view``) so the link
works when clicked straight from a Discord thread without logging in.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.core import signing
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.events.models import AvailabilitySlotSelection

CALENDAR_SALT = "events.race-calendar"
RACE_DURATION = timedelta(minutes=60)
_ICS_TS_FORMAT = "%Y%m%dT%H%M%SZ"


def race_calendar_token(selection: AvailabilitySlotSelection) -> str:
    """Return an unguessable signed token encoding the race's primary key.

    Args:
        selection: The scheduled race to encode.

    Returns:
        A signed, URL-safe token string.

    """
    return signing.dumps(selection.pk, salt=CALENDAR_SALT)


def unsign_race_token(token: str) -> int | None:
    """Decode a signed race token back to a primary key.

    Args:
        token: The signed token from the URL.

    Returns:
        The race PK, or None if the signature is invalid/tampered.

    """
    try:
        return signing.loads(token, salt=CALENDAR_SALT)
    except signing.BadSignature:
        return None


def _race_start_end(selection: AvailabilitySlotSelection) -> tuple[datetime, datetime]:
    """Return the UTC start and (start + fixed duration) end for a race.

    Args:
        selection: The scheduled race.

    Returns:
        A (start, end) tuple of timezone-aware UTC datetimes.

    """
    start = datetime.combine(
        selection.slot_date,
        datetime.strptime(selection.slot_time, "%H:%M").time(),
        tzinfo=ZoneInfo("UTC"),
    )
    return start, start + RACE_DURATION


def _race_summary(selection: AvailabilitySlotSelection) -> str:
    """Return the calendar event title (race name, plus opponent if set).

    Args:
        selection: The scheduled race.

    Returns:
        The event title string.

    """
    if selection.opponent:
        return f"{selection.name} vs {selection.opponent}"
    return selection.name


def _race_description(selection: AvailabilitySlotSelection) -> str:
    """Return the calendar event description (status + links).

    Args:
        selection: The scheduled race.

    Returns:
        A newline-joined description string.

    """
    parts = [f"Status: {selection.get_status_display()}"]
    if selection.opponent:
        parts.append(f"Opponent: {selection.opponent}")
    if selection.event_invite_url:
        parts.append(f"Event invite: {selection.event_invite_url}")
    if selection.course_url:
        parts.append(f"Course: {selection.course_url}")
    if selection.thread_link:
        parts.append(f"Discord thread: {selection.thread_link}")
    return "\n".join(parts)


def _ics_escape(text: str) -> str:
    """Escape a value for an iCalendar TEXT field (RFC 5545 §3.3.11).

    Args:
        text: The raw text to escape.

    Returns:
        The escaped text.

    """
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    """Fold a content line to 75 octets with leading-space continuations (RFC 5545 §3.1).

    Args:
        line: A single iCalendar content line.

    Returns:
        The line, folded with CRLF + space if it exceeds 75 octets.

    """
    if len(line) <= 75:
        return line
    chunks = [line[:75]]
    rest = line[75:]
    while rest:
        chunks.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(chunks)


def build_race_ics(selection: AvailabilitySlotSelection) -> str:
    """Build the iCalendar (.ics) document text for a single scheduled race.

    Args:
        selection: The scheduled race.

    Returns:
        The full .ics document as a CRLF-delimited string.

    """
    start, end = _race_start_end(selection)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//The Coalition//Scheduled Race//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:race-{selection.pk}@the-coalition",
        f"DTSTAMP:{timezone.now().strftime(_ICS_TS_FORMAT)}",
        f"DTSTART:{start.strftime(_ICS_TS_FORMAT)}",
        f"DTEND:{end.strftime(_ICS_TS_FORMAT)}",
        f"SUMMARY:{_ics_escape(_race_summary(selection))}",
        f"DESCRIPTION:{_ics_escape(_race_description(selection))}",
        "LOCATION:Zwift",
    ]
    url = selection.event_invite_url or selection.thread_link or selection.course_url
    if url:
        lines.append(f"URL:{_ics_escape(url)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


def race_google_calendar_url(selection: AvailabilitySlotSelection) -> str:
    """Build a Google Calendar 'add event' template URL for a scheduled race.

    Args:
        selection: The scheduled race.

    Returns:
        An absolute calendar.google.com URL with the race pre-filled.

    """
    start, end = _race_start_end(selection)
    params = {
        "action": "TEMPLATE",
        "text": _race_summary(selection),
        "dates": f"{start.strftime(_ICS_TS_FORMAT)}/{end.strftime(_ICS_TS_FORMAT)}",
        "details": _race_description(selection),
        "location": "Zwift",
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def race_calendar_urls(selection: AvailabilitySlotSelection, request: HttpRequest) -> dict[str, str]:
    """Return absolute .ics and Google Calendar URLs for a scheduled race.

    Args:
        selection: The scheduled race.
        request: The current request, used to build an absolute .ics URL.

    Returns:
        Dict with ``ics_url`` and ``gcal_url`` keys.

    """
    ics_path = reverse("events:race_calendar_ics", kwargs={"token": race_calendar_token(selection)})
    return {
        "ics_url": request.build_absolute_uri(ics_path),
        "gcal_url": race_google_calendar_url(selection),
    }

"""Utility functions for accounts app."""

import re
from typing import NamedTuple

import httpx
import logfire
from bs4 import BeautifulSoup

# ``User.zwid`` is a PositiveIntegerField, i.e. a 32-bit ``integer`` column on PostgreSQL:
# a larger value raises DataError on save rather than storing. (``apps.zwift.verification``
# applies the same bound to the zwid the zauth service reports.)
MAX_ZWID = 2147483647
_MAX_ZWID_DIGITS = len(str(MAX_ZWID))

# ``[0-9]`` rather than ``\d``, which also matches "\u0663" and friends: int() reads those
# as ordinary digits, so a URL carrying them would resolve to some other rider's ZWID.
_ZWIFTPOWER_PROFILE = re.compile(r"zwiftpower\.com/profile\.php\?z=([0-9]+)")
_ASCII_DIGITS = re.compile(r"[0-9]+")


class ZwidInput(NamedTuple):
    """What a rider's ZWID entry parsed to.

    Attributes:
        zwid: The ZWID to store, or None when the input is unusable.
        form: A coarse label for the shape of the entry -- ``zwiftpower_url``, ``digits``,
            ``url``, ``empty`` or ``other``. Safe to log for any input.
        entered: The number the rider actually typed, whether or not it is usable, so a
            rejected entry is still traceable to the ZWID they meant. None when they typed
            no number, or one too long to convert (see :func:`parse_zwid_input`).

    """

    zwid: int | None
    form: str
    entered: int | None


def _read_digits(text: str) -> tuple[int | None, int | None]:
    """Convert a run of ASCII digits into (storable zwid, the number as entered).

    Args:
        text: A non-empty run of ASCII digits.

    Returns:
        The value if the column can hold it else None, and the value as entered -- which is
        also None past :data:`_MAX_ZWID_DIGITS`, because int() refuses a string of more than
        4300 digits outright and nothing that long is a mistyped ZWID anyway.

    """
    if len(text) > _MAX_ZWID_DIGITS:
        return None, None
    entered = int(text)
    return (entered if 0 < entered <= MAX_ZWID else None), entered


def parse_zwid_input(raw: str) -> ZwidInput:
    """Read a rider-entered ZwiftPower profile URL or bare Zwift ID.

    Shared by the rider's own verification page and the public membership-registration
    form so the two cannot drift apart on what they accept.

    The ZWID a rider entered is an id and belongs in the logs even when it is rejected --
    that is how a "it would not take my ID" report gets answered. The rest of what they
    typed does not: the box takes anything, so ``form`` is what names an entry that
    carried no number.

    Rejects what the column cannot hold: ``str.isdigit()`` is true of "\u00b2" (``int()``
    raises) and of "\u0663" (``int()`` yields 3, a real rider's ZWID), and a value above
    :data:`MAX_ZWID` raises DataError on save.

    Args:
        raw: Whatever the rider typed.

    Returns:
        A :class:`ZwidInput`.

    """
    text = raw.strip()
    if not text:
        return ZwidInput(None, "empty", None)

    match = _ZWIFTPOWER_PROFILE.search(text)
    if match:
        zwid, entered = _read_digits(match.group(1))
        return ZwidInput(zwid, "zwiftpower_url", entered)

    if _ASCII_DIGITS.fullmatch(text):
        zwid, entered = _read_digits(text)
        return ZwidInput(zwid, "digits", entered)

    return ZwidInput(None, "url" if "://" in text or text.startswith("www.") else "other", None)


def youtube_url_form(youtube_url: str) -> str:
    """Name the shape of a YouTube channel URL without the part that identifies it.

    The URL itself is personal data -- it names a rider's channel -- so it never reaches the
    logs. Its shape still answers the triage question: one rider pasted something odd, or
    every URL of a kind stopped resolving.

    Args:
        youtube_url: The YouTube channel URL.

    Returns:
        A coarse label: ``handle``, ``channel``, ``c``, ``user``, ``non_youtube`` or ``other``.

    """
    host_and_path = re.sub(r"^https?://", "", youtube_url.strip())
    if not re.match(r"([\w-]+\.)*youtube\.com/.", host_and_path):
        return "non_youtube"
    path = host_and_path.split("/", 1)[1]
    if path.startswith("@"):
        return "handle"
    first_segment = path.split("/", 1)[0]
    return first_segment if first_segment in {"channel", "c", "user"} else "other"


def extract_youtube_channel_id(youtube_url: str) -> str | None:
    """Extract YouTube channel ID from a YouTube channel URL.

    Handles various YouTube URL formats:
    - https://www.youtube.com/channel/UC1234... (ID in URL)
    - https://www.youtube.com/@username (handle format)
    - https://www.youtube.com/c/CustomName (custom URL)
    - https://www.youtube.com/user/username (legacy format)

    Args:
        youtube_url: The YouTube channel URL.

    Returns:
        The channel ID (starting with UC) or None if extraction fails.

    """
    if not youtube_url:
        return None

    youtube_url = youtube_url.strip()

    # Check if channel ID is directly in the URL
    channel_match = re.search(r"/channel/(UC[\w-]+)", youtube_url)
    if channel_match:
        return channel_match.group(1)

    # For other URL formats, fetch the page and extract from HTML
    try:
        # instrument_httpx() records every request URL as a span attribute, and this one is
        # the rider's channel. Suppress the span; the outcome is logged below without it.
        with logfire.suppress_instrumentation():
            response = httpx.get(
                youtube_url,
                follow_redirects=True,
                timeout=10.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
        response.raise_for_status()

        html_content = response.text

        # Parse with BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")

        # Try canonical link first: <link rel="canonical" href="https://www.youtube.com/channel/UC...">
        canonical_link = soup.find("link", rel="canonical")
        if canonical_link and canonical_link.get("href"):
            href = canonical_link["href"]
            channel_match = re.search(r"/channel/(UC[\w-]+)", href)
            if channel_match:
                logfire.debug(
                    "Extracted YouTube channel ID from canonical link",
                    channel_id=channel_match.group(1),
                )
                return channel_match.group(1)

        # Try meta tag: <meta itemprop="channelId" content="UC...">
        meta_tag = soup.find("meta", itemprop="channelId")
        if meta_tag and meta_tag.get("content"):
            channel_id = meta_tag["content"]
            if channel_id.startswith("UC"):
                logfire.debug(
                    "Extracted YouTube channel ID from meta tag",
                    channel_id=channel_id,
                )
                return channel_id

        # Fallback: search for channel ID pattern in raw HTML (from JSON data)
        channel_match = re.search(r'"channelId"\s*:\s*"(UC[\w-]+)"', html_content)
        if channel_match:
            logfire.debug(
                "Extracted YouTube channel ID from JSON in HTML",
                channel_id=channel_match.group(1),
            )
            return channel_match.group(1)

        # Try externalId or browseId patterns
        external_match = re.search(r'"(?:externalId|browseId)"\s*:\s*"(UC[\w-]+)"', html_content)
        if external_match:
            logfire.debug(
                "Extracted YouTube channel ID from externalId/browseId",
                channel_id=external_match.group(1),
            )
            return external_match.group(1)

        logfire.warning(
            "Could not extract YouTube channel ID",
            url_form=youtube_url_form(youtube_url),
        )
        return None

    except httpx.HTTPStatusError as e:
        # Not error=str(e): httpx quotes the failing request URL in its message.
        logfire.error(
            "YouTube refused a channel page",
            status_code=e.response.status_code,
            url_form=youtube_url_form(youtube_url),
        )
        return None
    except httpx.HTTPError as e:
        logfire.error(
            "HTTP error fetching YouTube page",
            error_type=type(e).__name__,
            url_form=youtube_url_form(youtube_url),
        )
        return None
    except Exception as e:
        logfire.error(
            "Error extracting YouTube channel ID",
            error=str(e),
        )
        return None


def get_youtube_rss_url(channel_id: str) -> str | None:
    """Get the YouTube RSS feed URL for a channel ID.

    Args:
        channel_id: The YouTube channel ID (starting with UC).

    Returns:
        The RSS feed URL or None if channel_id is invalid.

    """
    if not channel_id or not channel_id.startswith("UC"):
        return None
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def fetch_youtube_videos(channel_id: str, limit: int = 5) -> list[dict]:
    """Fetch recent videos from a YouTube channel's RSS feed.

    Args:
        channel_id: The YouTube channel ID (starting with UC).
        limit: Maximum number of videos to return.

    Returns:
        List of video dictionaries with keys: video_id, title, url, thumbnail, published.

    """
    import contextlib
    import xml.etree.ElementTree as ET  # noqa: S405 - YouTube RSS is a trusted source
    from datetime import datetime

    if not channel_id or not channel_id.startswith("UC"):
        return []

    rss_url = get_youtube_rss_url(channel_id)
    if not rss_url:
        return []

    try:
        response = httpx.get(
            rss_url,
            timeout=10.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        response.raise_for_status()

        # Parse the XML feed (YouTube RSS is a trusted source)
        root = ET.fromstring(response.text)  # noqa: S314

        # Define namespaces used in YouTube RSS
        namespaces = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/",
        }

        videos = []
        for entry in root.findall("atom:entry", namespaces)[:limit]:
            video_id = entry.find("yt:videoId", namespaces)
            title = entry.find("atom:title", namespaces)
            published = entry.find("atom:published", namespaces)
            media_group = entry.find("media:group", namespaces)

            if video_id is not None and title is not None:
                video_data = {
                    "video_id": video_id.text,
                    "title": title.text,
                    "url": f"https://www.youtube.com/watch?v={video_id.text}",
                    "thumbnail": f"https://i.ytimg.com/vi/{video_id.text}/mqdefault.jpg",
                    "published": None,
                }

                # Parse published date
                if published is not None and published.text:
                    with contextlib.suppress(ValueError):
                        # YouTube uses ISO 8601 format: 2024-01-15T12:00:00+00:00
                        video_data["published"] = datetime.fromisoformat(published.text)

                # Get description from media:group if available
                if media_group is not None:
                    description = media_group.find("media:description", namespaces)
                    if description is not None and description.text:
                        video_data["description"] = description.text[:200]  # Truncate

                videos.append(video_data)

        logfire.debug(
            "Fetched YouTube videos",
            channel_id=channel_id,
            video_count=len(videos),
        )
        return videos

    except httpx.HTTPError as e:
        logfire.error(
            "HTTP error fetching YouTube RSS feed",
            channel_id=channel_id,
            error=str(e),
        )
        return []
    except ET.ParseError as e:
        logfire.error(
            "Error parsing YouTube RSS feed",
            channel_id=channel_id,
            error=str(e),
        )
        return []
    except Exception as e:
        logfire.error(
            "Error fetching YouTube videos",
            channel_id=channel_id,
            error=str(e),
        )
        return []

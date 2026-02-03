"""Utility functions for accounts app."""

import re

import httpx
import logfire
from bs4 import BeautifulSoup


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
                    youtube_url=youtube_url,
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
                    youtube_url=youtube_url,
                    channel_id=channel_id,
                )
                return channel_id

        # Fallback: search for channel ID pattern in raw HTML (from JSON data)
        channel_match = re.search(r'"channelId"\s*:\s*"(UC[\w-]+)"', html_content)
        if channel_match:
            logfire.debug(
                "Extracted YouTube channel ID from JSON in HTML",
                youtube_url=youtube_url,
                channel_id=channel_match.group(1),
            )
            return channel_match.group(1)

        # Try externalId or browseId patterns
        external_match = re.search(r'"(?:externalId|browseId)"\s*:\s*"(UC[\w-]+)"', html_content)
        if external_match:
            logfire.debug(
                "Extracted YouTube channel ID from externalId/browseId",
                youtube_url=youtube_url,
                channel_id=external_match.group(1),
            )
            return external_match.group(1)

        logfire.warning(
            "Could not extract YouTube channel ID",
            youtube_url=youtube_url,
        )
        return None

    except httpx.HTTPError as e:
        logfire.error(
            "HTTP error fetching YouTube page",
            youtube_url=youtube_url,
            error=str(e),
        )
        return None
    except Exception as e:
        logfire.error(
            "Error extracting YouTube channel ID",
            youtube_url=youtube_url,
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
                        video_data["published"] = datetime.fromisoformat(
                            published.text.replace("Z", "+00:00")
                        )

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

"""A rider's YouTube channel URL must not reach Logfire.

The URL is rider-entered free text that names their public channel, so it falls under the
"ids only" rule in CLAUDE.md. Two routes used to carry it: the kwargs of the resolution logs,
and -- less visibly -- ``instrument_httpx()``, which records every request URL as a span
attribute, and httpx's own error messages, which quote the failing URL.

The resolved ``channel_id`` is deliberately still logged: it is an external-platform id for
the channel, the same kind of thing as ``zwid``, and it is what makes a wrong-channel report
diagnosable.
"""

import contextlib
from unittest.mock import Mock, patch

import httpx
import pytest

from apps.accounts.tasks import sync_youtube_channel_ids
from apps.accounts.utils import extract_youtube_channel_id, youtube_url_form

HANDLE = "a-riders-real-handle"
HANDLE_URL = f"https://www.youtube.com/@{HANDLE}"
CHANNEL_ID = "UCchannelchannelchan"
RESOLVED_PAGE = f'<html><head><link rel="canonical" href="https://www.youtube.com/channel/{CHANNEL_ID}"></head></html>'


# The logfire calls that actually emit. A mock records more than that -- entering
# suppress_instrumentation() and the exception handed to its __exit__ -- and none of that
# leaves the process.
EMITTING = frozenset({"debug", "info", "notice", "warning", "error", "exception", "fatal", "log", "span"})


def _logged_values(fake_logfire) -> list[str]:
    """Collect every value the patched logfire module was asked to emit.

    Returns:
        The stringified positional and keyword arguments of all emitting calls.

    """
    values = []
    for name, args, kwargs in fake_logfire.mock_calls:
        if name not in EMITTING:
            continue
        values.extend(str(arg) for arg in args)
        values.extend(str(value) for value in kwargs.values())
    return values


def _page(html: str) -> Mock:
    """Build a stand-in httpx response carrying ``html``.

    Returns:
        A mock with the two attributes the extractor touches.

    """
    return Mock(text=html, raise_for_status=Mock())


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/@someone", "handle"),
        ("https://www.youtube.com/channel/UCabc", "channel"),
        ("https://youtube.com/c/CustomName", "c"),
        ("https://www.youtube.com/user/legacy", "user"),
        ("https://www.youtube.com/watch?v=abc", "other"),
        ("https://vimeo.com/someone", "non_youtube"),
        ("not a url at all", "non_youtube"),
    ],
)
def test_url_form_labels_the_shape_without_the_identity(url, expected):
    form = youtube_url_form(url)

    assert form == expected
    assert "someone" not in form
    assert "CustomName" not in form


def test_a_resolved_channel_is_logged_without_the_url():
    with (
        patch("apps.accounts.utils.httpx.get", return_value=_page(RESOLVED_PAGE)),
        patch("apps.accounts.utils.logfire") as fake_logfire,
    ):
        channel_id = extract_youtube_channel_id(HANDLE_URL)

    assert channel_id == CHANNEL_ID
    values = _logged_values(fake_logfire)
    assert not any(HANDLE in value for value in values)
    assert any(CHANNEL_ID in value for value in values)


def test_an_unresolvable_page_logs_the_url_shape_not_the_url():
    with (
        patch("apps.accounts.utils.httpx.get", return_value=_page("<html></html>")),
        patch("apps.accounts.utils.logfire") as fake_logfire,
    ):
        assert extract_youtube_channel_id(HANDLE_URL) is None

    values = _logged_values(fake_logfire)
    assert not any(HANDLE in value for value in values)
    assert "handle" in values


def test_an_http_error_does_not_leak_the_url_through_the_exception_message():
    """The failing URL is quoted in httpx's own message, so ``error=str(e)`` would have leaked it."""
    request = httpx.Request("GET", HANDLE_URL)
    refused = httpx.HTTPStatusError(
        f"Client error '404 Not Found' for url '{HANDLE_URL}'",
        request=request,
        response=httpx.Response(404, request=request),
    )

    with (
        patch("apps.accounts.utils.httpx.get", side_effect=refused),
        patch("apps.accounts.utils.logfire") as fake_logfire,
    ):
        assert extract_youtube_channel_id(HANDLE_URL) is None

    values = _logged_values(fake_logfire)
    assert not any(HANDLE in value for value in values)
    assert "404" in values


def test_the_channel_page_request_is_not_traced():
    """instrument_httpx() would record the rider's channel URL as a span attribute."""
    suppressing = []
    observed = {}

    @contextlib.contextmanager
    def fake_suppress():
        suppressing.append(True)
        try:
            yield
        finally:
            suppressing.pop()

    def fake_get(*_args, **_kwargs):
        observed["suppressed"] = bool(suppressing)
        return _page(RESOLVED_PAGE)

    with (
        patch("apps.accounts.utils.httpx.get", side_effect=fake_get),
        patch("apps.accounts.utils.logfire") as fake_logfire,
    ):
        fake_logfire.suppress_instrumentation = fake_suppress
        extract_youtube_channel_id(HANDLE_URL)

    assert observed["suppressed"] is True


@pytest.mark.django_db
def test_the_sync_task_logs_no_url_when_resolution_fails(team_member):
    team_member.youtube_channel = HANDLE_URL
    team_member.save()

    with (
        patch("apps.accounts.utils.extract_youtube_channel_id", return_value=None),
        patch("apps.accounts.tasks.time.sleep"),
        patch("apps.accounts.tasks.logfire") as fake_logfire,
    ):
        sync_youtube_channel_ids.func()

    values = _logged_values(fake_logfire)
    assert not any(HANDLE in value for value in values)
    assert "handle" in values
    assert str(team_member.id) in values


@pytest.mark.django_db
def test_the_sync_task_logs_no_url_when_resolution_succeeds(team_member):
    team_member.youtube_channel = HANDLE_URL
    team_member.save()

    with (
        patch("apps.accounts.utils.extract_youtube_channel_id", return_value=CHANNEL_ID),
        patch("apps.accounts.tasks.time.sleep"),
        patch("apps.accounts.tasks.logfire") as fake_logfire,
    ):
        sync_youtube_channel_ids.func()

    values = _logged_values(fake_logfire)
    assert not any(HANDLE in value for value in values)
    assert CHANNEL_ID in values

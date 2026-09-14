"""What a rider types into the ZWID boxes must not reach Logfire, and must not 500.

The two manual-verification forms -- the rider's own verification page and the *public*
membership-registration form -- log their invalid-input path. They used to log the raw
text, which is rider-entered free text and so falls under the "ids only" rule in
CLAUDE.md; the shape of the input goes instead.

They also parsed with ``str.isdigit()``, which is true of characters ``int()`` either
refuses or reads as a different number, and neither checked the column's range.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.accounts.utils import MAX_ZWID, parse_zwid_input

# Stands in for what a rider might paste: an id, and free text naming them.
PROFILE_URL = "https://zwiftpower.com/profile.php?z=4242"
PERSONAL_TEXT = "rider.realname@example.test my zwift is 'Real Name (COALITION)'"

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


# --- the parser --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (PROFILE_URL, (4242, "zwiftpower_url")),
        ("  4242  ", (4242, "digits")),
        ("http://www.zwiftpower.com/profile.php?z=4242", (4242, "zwiftpower_url")),
        ("", (None, "empty")),
        ("   ", (None, "empty")),
        ("0", (None, "digits")),
        ("https://zwiftpower.com/profile.php", (None, "url")),
        ("www.zwift.com/me", (None, "url")),
        (PERSONAL_TEXT, (None, "other")),
        # isdigit() is true of both: int() raises on the first, and reads the second as 3.
        ("²", (None, "other")),
        ("٣", (None, "other")),
        ("٣" * 4, (None, "other")),
        # PositiveIntegerField is a 32-bit column; a bigger value is a DataError on save.
        (str(MAX_ZWID), (MAX_ZWID, "digits")),
        (str(MAX_ZWID + 1), (None, "digits")),
        ("9" * 40, (None, "digits")),
        (f"https://zwiftpower.com/profile.php?z={MAX_ZWID + 1}", (None, "zwiftpower_url")),
    ],
)
def test_parse_zwid_input(raw, expected):
    assert parse_zwid_input(raw) == expected


# --- the rider's own verification page ---------------------------------------


@pytest.mark.django_db
def test_rider_input_is_not_logged(client, user):
    client.force_login(user)

    with patch("apps.accounts.views.logfire") as fake_logfire:
        client.post(reverse("accounts:manual_zwift_verify"), {"zwiftpower_url": PERSONAL_TEXT})

    logged = _logged_values(fake_logfire)
    assert "other" in logged  # the shape still went, so a failure is triageable
    assert not any(PERSONAL_TEXT in value for value in logged)


@pytest.mark.django_db
@pytest.mark.parametrize("junk", ["²", "٣", "9" * 40, str(MAX_ZWID + 1)])
def test_a_hand_crafted_zwid_is_rejected_not_crashed_on(client, user, junk):
    """isdigit() passed "²" (int() raises) and "٣" (int() yields 3, a real ZWID)."""
    client.force_login(user)

    resp = client.post(reverse("accounts:manual_zwift_verify"), {"zwiftpower_url": junk})

    assert resp.status_code == 200
    assert b"valid ZwiftPower profile URL" in resp.content
    user.refresh_from_db()
    assert user.zwid is None


# --- the public registration form --------------------------------------------


@pytest.fixture
def application(db):
    from apps.team.models import MembershipApplication

    return MembershipApplication.objects.create(discord_id="123456789", discord_username="applicant")


@pytest.mark.django_db
def test_applicant_input_is_not_logged(client, application):
    """The registration form needs no login: anyone with the UUID can fill these logs."""
    url = reverse("team:application_manual_zwift_verify", args=[application.pk])

    with patch("apps.team.views.logfire") as fake_logfire:
        client.post(url, {"zwiftpower_url": PERSONAL_TEXT})

    logged = _logged_values(fake_logfire)
    assert "other" in logged
    assert not any(PERSONAL_TEXT in value for value in logged)


@pytest.mark.django_db
@pytest.mark.parametrize("junk", ["²", "٣", "9" * 40, str(MAX_ZWID + 1)])
def test_a_hand_crafted_application_zwid_is_rejected_not_crashed_on(client, application, junk):
    url = reverse("team:application_manual_zwift_verify", args=[application.pk])

    resp = client.post(url, {"zwiftpower_url": junk})

    assert resp.status_code == 200
    application.refresh_from_db()
    assert application.zwift_id == ""

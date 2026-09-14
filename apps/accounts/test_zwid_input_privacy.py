"""The ZWID a rider enters reaches Logfire; the rest of what they type does not.

The two manual-verification forms -- the rider's own verification page and the *public*
membership-registration form -- log their invalid-input path. The number a rider entered
belongs there even when it is rejected: it is an id, and it is what answers an "it would
not take my ID" report. What used to be logged was the raw text, which is whatever they
typed, so an entry carrying no number is now reported by its shape instead.

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
        (PROFILE_URL, (4242, "zwiftpower_url", 4242)),
        ("  4242  ", (4242, "digits", 4242)),
        ("http://www.zwiftpower.com/profile.php?z=4242", (4242, "zwiftpower_url", 4242)),
        ("", (None, "empty", None)),
        ("   ", (None, "empty", None)),
        ("0", (None, "digits", 0)),
        ("https://zwiftpower.com/profile.php", (None, "url", None)),
        ("www.zwift.com/me", (None, "url", None)),
        (PERSONAL_TEXT, (None, "other", None)),
        # isdigit() is true of both: int() raises on the first, and reads the second as 3.
        ("\u00b2", (None, "other", None)),
        ("\u0663", (None, "other", None)),
        ("\u0663" * 4, (None, "other", None)),
        # PositiveIntegerField is a 32-bit column; a bigger value is a DataError on save.
        # Rejected, but still the ZWID the rider meant, so it is still reported.
        (str(MAX_ZWID), (MAX_ZWID, "digits", MAX_ZWID)),
        (str(MAX_ZWID + 1), (None, "digits", MAX_ZWID + 1)),
        (f"https://zwiftpower.com/profile.php?z={MAX_ZWID + 1}", (None, "zwiftpower_url", MAX_ZWID + 1)),
        # Past 10 digits nothing is a mistyped ZWID, and int() refuses over 4300 outright.
        ("9" * 40, (None, "digits", None)),
        ("9" * 5000, (None, "digits", None)),
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


@pytest.mark.django_db
def test_a_rejected_zwid_is_still_reported(client, user):
    """The rider entered a ZWID; it not being storable is exactly what needs explaining."""
    client.force_login(user)

    with patch("apps.accounts.views.logfire") as fake_logfire:
        client.post(reverse("accounts:manual_zwift_verify"), {"zwiftpower_url": str(MAX_ZWID + 1)})

    kwargs = fake_logfire.warning.call_args[1]
    assert kwargs["entered_zwid"] == MAX_ZWID + 1
    assert kwargs["input_form"] == "digits"


@pytest.mark.django_db
def test_a_rejected_application_zwid_is_still_reported(client, application):
    url = reverse("team:application_manual_zwift_verify", args=[application.pk])

    with patch("apps.team.views.logfire") as fake_logfire:
        client.post(url, {"zwiftpower_url": f"https://zwiftpower.com/profile.php?z={MAX_ZWID + 1}"})

    kwargs = fake_logfire.warning.call_args[1]
    assert kwargs["entered_zwid"] == MAX_ZWID + 1
    assert kwargs["input_form"] == "zwiftpower_url"


@pytest.mark.django_db
def test_an_entry_carrying_no_number_reports_only_its_shape(client, user):
    client.force_login(user)

    with patch("apps.accounts.views.logfire") as fake_logfire:
        client.post(reverse("accounts:manual_zwift_verify"), {"zwiftpower_url": PERSONAL_TEXT})

    kwargs = fake_logfire.warning.call_args[1]
    assert kwargs["entered_zwid"] is None
    assert kwargs["input_form"] == "other"


@pytest.mark.django_db
def test_a_digit_string_too_long_for_int_is_rejected_not_crashed_on(client, user):
    """int() refuses a string of more than 4300 digits, so the conversion cannot be blind."""
    client.force_login(user)

    resp = client.post(reverse("accounts:manual_zwift_verify"), {"zwiftpower_url": "9" * 5000})

    assert resp.status_code == 200
    assert b"valid ZwiftPower profile URL" in resp.content

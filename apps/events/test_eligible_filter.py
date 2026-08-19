"""The add-rider picker's "Eligible only" filter.

Eligibility is decided server-side and shipped as ``data-eligible`` on each option, so
these tests assert on that attribute. The client-side show/hide is exercised separately
against the template's own script.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad


@pytest.fixture
def event(db) -> Event:
    """Build a visible event with signups open.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7),
        visible=True, signups_open=True,
    )


def _rider(user_model, event, username: str, **fields):
    """Register a rider for the event.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username=username, email=f"{username}@example.test", first_name=username.title(), **fields,
    )
    EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)
    return user


def _options(body: str) -> dict[str, str]:
    """Map each picker option's label to its data-eligible value.

    Returns:
        ``{name: "1" | "0"}``.

    """
    import re

    return {m[1]: m[0] for m in re.findall(r'data-eligible="([01])">([^<]+)</option>', body)}


@pytest.mark.django_db
def test_power_bound_marks_riders_ineligible(client, event, user_model, event_admin) -> None:
    Squad.objects.create(
        event=event, name="B DEV",
        max_zftp_wkg=Decimal("3.74"), enforce_max_zftp_wkg=True,
    )
    _rider(user_model, event, "strong", z_ftp=Decimal("300.0"), z_metrics_weight_grams=66000)  # 4.55
    _rider(user_model, event, "inband", z_ftp=Decimal("230.0"), z_metrics_weight_grams=66000)  # 3.48
    client.force_login(event_admin)

    opts = _options(client.get(reverse("events:squad_manage", args=[event.pk])).content.decode())

    assert opts["Strong"] == "0"
    assert opts["Inband"] == "1"


@pytest.mark.django_db
def test_zauth_requirement_marks_unconnected_riders_ineligible(client, event, user_model, event_admin) -> None:
    Squad.objects.create(event=event, name="Verified", require_zauth=True)
    _rider(user_model, event, "connected", zwid_verification_method=user_model.VerificationMethod.ZAUTH)
    _rider(user_model, event, "legacy", zwid_verification_method=user_model.VerificationMethod.LEGACY)
    client.force_login(event_admin)

    opts = _options(client.get(reverse("events:squad_manage", args=[event.pk])).content.decode())

    assert opts["Connected"] == "1"
    assert opts["Legacy"] == "0"


@pytest.mark.django_db
def test_gender_requirement_is_included(client, event, user_model, event_admin) -> None:
    Squad.objects.create(event=event, name="Women", gender="Female", enforce_gender=True)
    _rider(user_model, event, "fem", gender="female")
    _rider(user_model, event, "male", gender="male")
    client.force_login(event_admin)

    opts = _options(client.get(reverse("events:squad_manage", args=[event.pk])).content.decode())

    assert opts["Fem"] == "1"
    assert opts["Male"] == "0"


@pytest.mark.django_db
def test_everyone_is_eligible_when_nothing_is_enforced(client, event, user_model, event_admin) -> None:
    """A squad with no rules must not start marking riders ineligible."""
    Squad.objects.create(event=event, name="Open")
    _rider(user_model, event, "anyone")
    client.force_login(event_admin)

    opts = _options(client.get(reverse("events:squad_manage", args=[event.pk])).content.decode())

    assert set(opts.values()) == {"1"}


@pytest.mark.django_db
def test_the_checkbox_is_rendered(client, event, user_model, event_admin) -> None:
    Squad.objects.create(event=event, name="Open")
    _rider(user_model, event, "anyone")
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    assert "data-eligible-only" in body
    assert "Eligible only" in body
    assert "data-rider-picker" in body


@pytest.mark.django_db
def test_eligibility_matches_what_assignment_actually_enforces(client, event, user_model, event_admin) -> None:
    """A rider the filter keeps must be one the POST accepts, or the filter lies."""
    from apps.events.models import SquadMember

    squad = Squad.objects.create(event=event, name="Verified", require_zauth=True)
    blocked = _rider(user_model, event, "legacy", zwid_verification_method=user_model.VerificationMethod.LEGACY)
    signup = EventSignup.objects.get(event=event, user=blocked)
    client.force_login(event_admin)

    opts = _options(client.get(reverse("events:squad_manage", args=[event.pk])).content.decode())
    client.post(
        reverse("events:squad_assign", args=[event.pk]),
        data={"signup_id": signup.pk, "squad_id": squad.pk},
    )

    assert opts["Legacy"] == "0"
    assert not SquadMember.objects.filter(squad=squad, user=blocked).exists()

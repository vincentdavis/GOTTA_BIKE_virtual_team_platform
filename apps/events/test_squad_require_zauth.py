"""Squads can require members to have connected Zwift through zauth.

This is the gate that makes the zFTP/zMAP bounds workable: those metrics only exist
for connected riders, so a squad enforcing a power bound almost always wants this on
too -- and the rider then gets told to connect rather than told a number is missing.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad, SquadMember


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True,
    )


def test_off_by_default_lets_anyone_through() -> None:
    assert Squad(name="S").check_zauth_eligibility(False) == (True, "")


def test_blocks_a_rider_who_has_not_connected() -> None:
    ok, reason = Squad(name="S", require_zauth=True).check_zauth_eligibility(False)
    assert ok is False
    assert "zauth" in reason


def test_admits_a_connected_rider() -> None:
    assert Squad(name="S", require_zauth=True).check_zauth_eligibility(True) == (True, "")


@pytest.mark.django_db
def test_legacy_verification_does_not_count(user_model) -> None:
    """The point is that Zwift itself confirmed the account."""
    legacy = user_model.objects.create_user(
        username="legacy", email="l@example.test",
        zwid_verified=True, zwid_verification_method=user_model.VerificationMethod.LEGACY,
    )
    zauth = user_model.objects.create_user(
        username="zauth", email="z@example.test",
        zwid_verified=True, zwid_verification_method=user_model.VerificationMethod.ZAUTH,
    )
    squad = Squad(name="S", require_zauth=True)

    assert squad.check_zauth_eligibility(legacy.is_zauth_verified)[0] is False
    assert squad.check_zauth_eligibility(zauth.is_zauth_verified)[0] is True


def test_enforcement_summary_names_the_requirement() -> None:
    assert "Zwift connected (zauth)" in Squad(name="S", require_zauth=True).enforcement_summary
    assert "Zwift connected (zauth)" not in Squad(name="S").enforcement_summary


@pytest.mark.django_db
def test_assign_is_blocked_end_to_end(client, event, user_model, event_admin) -> None:
    squad = Squad.objects.create(event=event, name="Synthesis", require_zauth=True)
    rider = user_model.objects.create_user(username="r", email="r@example.test")
    signup = EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED)
    client.force_login(event_admin)

    client.post(
        reverse("events:squad_assign", args=[event.pk]),
        data={"signup_id": signup.pk, "squad_id": squad.pk},
    )

    assert not SquadMember.objects.filter(squad=squad, user=rider).exists()


@pytest.mark.django_db
def test_assign_succeeds_without_the_requirement(client, event, user_model, event_admin) -> None:
    """Control for the test above: proves that POST really does assign when unblocked."""
    squad = Squad.objects.create(event=event, name="Open", require_zauth=False)
    rider = user_model.objects.create_user(username="r2", email="r2@example.test")
    signup = EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED)
    client.force_login(event_admin)

    client.post(
        reverse("events:squad_assign", args=[event.pk]),
        data={"signup_id": signup.pk, "squad_id": squad.pk},
    )

    assert SquadMember.objects.filter(squad=squad, user=rider).exists()


@pytest.mark.django_db
def test_the_form_round_trips_the_toggle(client, event, event_admin) -> None:
    squad = Squad.objects.create(event=event, name="Synthesis")
    client.force_login(event_admin)

    client.post(
        reverse("events:squad_edit", args=[event.pk, squad.pk]),
        data={"name": "Synthesis", "gender": "COED", "require_zauth": "on",
              "max_zftp_wkg": "3.74", "enforce_max_zftp_wkg": "on"},
    )
    squad.refresh_from_db()

    assert squad.require_zauth is True
    assert squad.max_zftp_wkg == Decimal("3.74")

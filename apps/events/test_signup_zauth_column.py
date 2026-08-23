"""The Zauth column on the event signup list.

A legacy or admin verification still reads as "Verified", but a squad with
``require_zauth`` turns it away -- so the column shows the method, not a bare yes/no,
because the method is the only thing that explains the rejection.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup


@pytest.fixture
def event(db) -> Event:
    """Build a visible event with signups open.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30),
        visible=True, signups_open=True,
    )


def _signup(user_model, event, username, **verification):
    """Register a rider carrying the given verification state.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(
        username=username, email=f"{username}@example.test",
        first_name=username.title(), last_name="R", **verification,
    )
    EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)
    return user


def _cell(body: str, user) -> str:
    """Extract the rendered Zauth cell for one rider.

    Returns:
        The cell markup.

    """
    row = body[body.index(f"{user.first_name} R"):]
    start = row.index('data-col="zauth"')
    return row[start:row.index("</td>", start)]


@pytest.mark.django_db
def test_zauth_legacy_and_unverified_are_three_distinct_states(client, event, event_admin, user_model) -> None:
    model = user_model
    oauth = _signup(model, event, "oauth", zwid_verified=True,
                    zwid_verification_method=model.VerificationMethod.ZAUTH)
    legacy = _signup(model, event, "legacy", zwid_verified=True,
                     zwid_verification_method=model.VerificationMethod.LEGACY)
    none = _signup(model, event, "none")
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "badge-success" in _cell(body, oauth)
    # Verified, but not by Zwift -- shown as the method so the require_zauth block reads.
    assert "badge-warning" in _cell(body, legacy)
    assert "Legacy" in _cell(body, legacy)
    assert "badge" not in _cell(body, none)


@pytest.mark.django_db
def test_the_column_is_offered_in_the_picker(client, event, event_admin, user_model) -> None:
    _signup(user_model, event, "rider")
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert 'col-toggle" data-col="zauth"' in body
    assert '<th data-col="zauth"' in body


@pytest.mark.django_db
def test_the_column_sorts_by_state_not_by_label(client, event, event_admin, user_model) -> None:
    """Sorting on the badge text would order Admin/Legacy/Zauth alphabetically."""
    model = user_model
    oauth = _signup(model, event, "oauth", zwid_verified=True,
                    zwid_verification_method=model.VerificationMethod.ZAUTH)
    none = _signup(model, event, "none")
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert 'data-sort-value="1"' in _cell(body, oauth)
    assert 'data-sort-value="3"' in _cell(body, none)

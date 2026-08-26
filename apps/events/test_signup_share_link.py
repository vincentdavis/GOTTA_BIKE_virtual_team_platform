"""Sharing a link that opens an event's signup form.

The link lands on the event page with the form already open, so a rider following it does
not have to find the button. Signup questions are part of that form, so they get asked
either way.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup

# Unique to the rendered control -- the attribute also appears in the click handler.
SHARE_BUTTON = 'aria-label="Copy a link that opens the signup form"'


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


def _page(client, event):
    """Load the event detail page.

    Returns:
        The decoded body.

    """
    response = client.get(reverse("events:event_detail", args=[event.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_the_share_button_offers_an_absolute_link(auth_client, event) -> None:
    """It is meant to be pasted into Discord, so a relative path is no use."""
    body = _page(auth_client, event)

    assert SHARE_BUTTON in body
    assert f"{reverse('events:event_detail', args=[event.pk])}?signup=1" in body
    assert "http://testserver" in body


@pytest.mark.django_db
def test_the_link_uses_a_query_string_not_a_fragment(auth_client, event) -> None:
    """The page is login-gated.

    allauth's ?next= carries a query string through the login round-trip; a #fragment
    would be dropped, so a logged-out rider would land on the page with nothing open.
    """
    body = _page(auth_client, event)

    assert "?signup=1" in body
    assert "#signup" not in body


@pytest.mark.django_db
def test_no_share_button_when_signups_are_closed(auth_client, event) -> None:
    """Sharing a link to a form nobody can submit is worse than no link."""
    event.signups_open = False
    event.save(update_fields=["signups_open"])

    assert SHARE_BUTTON not in _page(auth_client, event)


@pytest.mark.django_db
def test_a_registered_rider_is_sent_to_their_existing_answers(auth_client, team_member, event) -> None:
    """Following the link after signing up should not offer a blank form.

    The page renders the edit modal instead of the signup modal for a registered rider,
    and the landing script prefers whichever is present.
    """
    EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)

    body = _page(auth_client, event)

    assert 'id="edit_signup_modal"' in body
    assert 'getElementById("edit_signup_modal") || document.getElementById("signup_modal")' in body


@pytest.mark.django_db
def test_the_landing_script_is_present_for_a_new_rider(auth_client, event) -> None:
    """Without it the shared link would just be an ordinary event link."""
    body = _page(auth_client, event)

    assert 'params.get("signup") === "1"' in body
    assert 'id="signup_modal"' in body

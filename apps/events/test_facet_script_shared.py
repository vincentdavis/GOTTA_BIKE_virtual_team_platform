"""The answer-facet script is shared by every page that renders the panel.

It used to be inline in event_detail.html and hard-coded to that page's table. It now
lives in a partial and reads the table name off the panel, so the extraction has to
leave the event page working exactly as before.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, SignupQuestion, Squad


@pytest.fixture
def event_with_question(db) -> Event:
    """Build an event carrying one signup question.

    Returns:
        The event.

    """
    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7),
        visible=True, signups_open=True,
    )
    SignupQuestion.objects.create(
        event=event, label="Which nights?", question_type=SignupQuestion.Type.MULTI,
        options=["Tue", "Thu"], order=1,
    )
    return event


@pytest.mark.django_db
def test_event_page_still_names_its_own_table(client, event_with_question, user_model, event_admin) -> None:
    rider = user_model.objects.create_user(username="r", email="r@example.test")
    EventSignup.objects.create(event=event_with_question, user=rider, status=EventSignup.Status.REGISTERED)
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event_with_question.pk])).content.decode()

    assert 'data-answer-facets="detail"' in body
    assert 'data-signup-table="detail"' in body
    assert "window.answerFacetsRender" in body


@pytest.mark.django_db
def test_the_two_pages_ask_for_different_tables(client, event_with_question, user_model, event_admin) -> None:
    """If both said "detail" the add-riders panel would silently filter nothing."""
    squad = Squad.objects.create(event=event_with_question, name="B DEV")
    rider = user_model.objects.create_user(username="r", email="r@example.test")
    EventSignup.objects.create(event=event_with_question, user=rider, status=EventSignup.Status.REGISTERED)
    client.force_login(event_admin)

    detail = client.get(reverse("events:event_detail", args=[event_with_question.pk])).content.decode()
    add = client.get(
        reverse("events:squad_add_riders", args=[event_with_question.pk, squad.pk])
    ).content.decode()

    assert 'data-answer-facets="detail"' in detail
    assert 'data-answer-facets="add-riders"' in add

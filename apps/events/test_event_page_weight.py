"""The event page builds only what it shows.

A big event (hundreds of signups, dozens of squads) took seconds to render and megabytes to
send. Most of it was markup nobody saw: every signup row carried a squad picker listing the
event's every squad, hidden by the page's CSS since squads are assigned on the Assign Riders
page, and every Answers cell rendered its question's markdown label afresh.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.accounts.templatetags import accounts_tags
from apps.events.models import Event, EventSignup, SignupQuestion, Squad, SquadMember


@pytest.fixture
def event(db):
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True, signups_open=True
    )


def _signup(user_model, event, username, **fields):
    user = user_model.objects.create_user(username=username, email=f"{username}@example.test", first_name=username)
    return EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED, **fields)


def _squad_cell(body, signup):
    start = body.index(f'id="squad-cell-{signup.pk}"')
    return body[start : body.index("</td>", start)]


@pytest.mark.django_db
def test_the_event_page_lists_a_riders_squads_without_the_assign_controls(client, event, event_admin, user_model):
    placed = _signup(user_model, event, "ana")
    unplaced = _signup(user_model, event, "bea")
    squad = Squad.objects.create(event=event, name="Equinox B1")
    Squad.objects.create(event=event, name="Vitality C2")
    SquadMember.objects.create(squad=squad, user=placed.user, status=SquadMember.Status.MEMBER)
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "Equinox B1" in _squad_cell(body, placed)
    for signup in (placed, unplaced):
        cell = _squad_cell(body, signup)
        assert "<select" not in cell
        assert "<form" not in cell
    assert "Vitality C2</option>" not in body


@pytest.mark.django_db
def test_the_assign_riders_page_keeps_the_controls(client, event, event_admin, user_model):
    signup = _signup(user_model, event, "ana")
    squad = Squad.objects.create(event=event, name="Equinox B1")
    SquadMember.objects.create(squad=squad, user=signup.user, status=SquadMember.Status.MEMBER)
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_assign_page", args=[event.pk])).content.decode()

    cell = _squad_cell(body, signup)
    assert 'name="remove_squad_id"' in cell
    assert "<select" in cell


@pytest.mark.django_db
def test_a_question_label_is_rendered_once_however_many_riders_answered(client, event, event_admin, user_model):
    question = SignupQuestion.objects.create(
        event=event, label="Which **days**?", question_type="multi", options=["Tue", "Wed"], order=1
    )
    for name in ("ana", "bea", "cleo"):
        _signup(user_model, event, name, custom_answers={str(question.pk): ["Tue"]})
    client.force_login(event_admin)
    accounts_tags._inline_markdown_html.cache_clear()

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert body.count("Which <strong>days</strong>?") >= 3
    assert accounts_tags._inline_markdown_html.cache_info().misses == 1


# --- By Squad: fetched the first time the tab is shown -------------------------------------------


def _squad_with_member(event, user_model):
    squad = Squad.objects.create(event=event, name="Equinox B1")
    signup = _signup(user_model, event, "ana")
    SquadMember.objects.create(squad=squad, user=signup.user, status=SquadMember.Status.MEMBER)
    return squad


def _panel(client, event):
    return client.get(
        reverse("events:event_squads_panel", args=[event.pk]),
        headers={"HX-Request": "true", "HX-Target": "squads-panel"},
    )


@pytest.mark.django_db
def test_the_page_leaves_by_squad_to_be_fetched_when_the_tab_is_opened(client, event, event_admin, user_model):
    _squad_with_member(event, user_model)
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    panel = body[body.index('id="squads-panel"') :]
    panel = panel[: panel.index("</div>")]
    assert f'hx-get="{reverse("events:event_squads_panel", args=[event.pk])}"' in panel
    assert 'hx-trigger="intersect once"' in panel
    assert 'href="?tab=squads"' in panel  # the way in without JS
    assert 'id="squad-table"' not in body
    assert "data-lazy-panel" in panel


@pytest.mark.django_db
def test_the_fetch_answers_with_the_panel_alone(client, event, event_admin, user_model):
    _squad_with_member(event, user_model)
    client.force_login(event_admin)

    response = _panel(client, event)

    body = response.content.decode()
    assert response.status_code == 200
    assert 'id="squad-table"' in body
    assert "Equinox B1" in body
    assert "data-member-signup=" in body  # an event admin's members carry their signup rows
    assert "function toggleSquadMembers" in body  # its scripts arrive with it
    assert "<html" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["?tab=squads", ""])
def test_the_page_builds_by_squad_in_place_when_it_opens_on_it(client, event, team_member, user_model, query):
    """Asked for by ?tab=squads, or the default tab of a viewer who cannot see the signups."""
    _squad_with_member(event, user_model)
    client.force_login(team_member)

    body = client.get(reverse("events:event_detail", args=[event.pk]) + query).content.decode()

    assert 'data-default-tab="squads"' in body
    assert 'id="squad-table"' in body
    assert "Equinox B1" in body


@pytest.mark.django_db
def test_the_panel_is_never_a_page_of_its_own(client, event, event_admin):
    client.force_login(event_admin)

    response = client.get(reverse("events:event_squads_panel", args=[event.pk]))

    assert response.status_code == 302
    assert response["Location"] == reverse("events:event_detail", args=[event.pk]) + "?tab=squads"


@pytest.mark.django_db
def test_a_hidden_events_squads_are_not_fetched(client, event, team_member, user_model):
    _squad_with_member(event, user_model)
    event.visible = False
    event.save(update_fields=["visible"])
    client.force_login(team_member)

    assert _panel(client, event).status_code == 404

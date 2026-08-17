"""Answer facets: bucket construction, the client payload, and who is allowed to see them."""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.answer_facets import (
    ANSWERED_VALUE,
    NONE_VALUE,
    answers_payload,
    build_facets,
    normalized_answer,
    panel_starts_open,
)
from apps.events.models import Event, EventSignup, SignupQuestion


@pytest.fixture
def event(db) -> Event:
    """Build a visible, currently-running event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today - timedelta(days=1), end_date=today + timedelta(days=7), visible=True,
    )


def _q(event: Event, qtype: str, label: str, options=None, order: int = 0) -> SignupQuestion:
    """Create a signup question.

    Returns:
        The question.

    """
    return SignupQuestion.objects.create(
        event=event, label=label, question_type=qtype, options=options or [], order=order,
    )


def _signup(event: Event, user, answers: dict) -> EventSignup:
    """Create a registered signup carrying the given custom answers.

    Returns:
        The signup.

    """
    return EventSignup.objects.create(
        event=event, user=user, status=EventSignup.Status.REGISTERED, custom_answers=answers,
    )


@pytest.mark.django_db
def test_boolean_offers_two_buckets_because_false_is_never_stored(event) -> None:
    """An unchecked box stores no key, so a "No" bucket would read 0 forever."""
    q = _q(event, SignupQuestion.Type.BOOLEAN, "TT bike available?")

    facet = build_facets([q], [])[0]

    assert [b["value"] for b in facet["buckets"]] == [ANSWERED_VALUE, NONE_VALUE]
    assert facet["buckets"][0]["label"] == "Confirmed"
    assert facet["buckets"][1]["label"] == "Not confirmed (or no answer)"
    assert "No" not in [b["label"] for b in facet["buckets"]]


@pytest.mark.django_db
def test_text_filters_only_by_answered_or_not(event) -> None:
    """Free text gets answered/not buckets; the prose itself stays in the table cell."""
    q = _q(event, SignupQuestion.Type.TEXT, "Injuries?")

    facet = build_facets([q], [])[0]

    assert [b["label"] for b in facet["buckets"]] == ["Answered", "No answer"]


@pytest.mark.django_db
def test_choice_buckets_follow_the_admin_declared_option_order(event) -> None:
    """Option order is meaningful (Tue before Wed), so buckets must not be alphabetized."""
    q = _q(event, SignupQuestion.Type.MULTI, "Nights?", options=["Tue", "Wed", "Thu", "Fri"])

    facet = build_facets([q], [])[0]

    assert [b["value"] for b in facet["buckets"]] == ["Tue", "Wed", "Thu", "Fri", NONE_VALUE]


@pytest.mark.django_db
def test_a_removed_option_still_held_by_a_rider_gets_its_own_bucket(event, team_member) -> None:
    """Stored answers are historical, so a grandfathered value must stay filterable."""
    q = _q(event, SignupQuestion.Type.SINGLE, "Role", options=["Lead-out", "Sweep"])
    _signup(event, team_member, {str(q.pk): "Domestique"})  # option since deleted by an admin

    facet = build_facets([q], list(event.signups.all()))[0]
    retired = [b for b in facet["buckets"] if b["retired"]]

    assert [b["value"] for b in retired] == ["Domestique"]
    assert retired[0]["label"] == "Domestique (removed)"


@pytest.mark.django_db
def test_payload_omits_unanswered_questions_entirely(event, team_member) -> None:
    """Missing key is the only "unanswered" representation; a falsy value would blur it."""
    multi = _q(event, SignupQuestion.Type.MULTI, "Nights?", options=["Tue"], order=1)
    boolean = _q(event, SignupQuestion.Type.BOOLEAN, "TT bike?", order=2)
    text = _q(event, SignupQuestion.Type.TEXT, "Notes", order=3)
    signup = _signup(event, team_member, {str(multi.pk): ["Tue"]})

    payload = answers_payload([signup], [multi, boolean, text])

    assert payload[str(signup.pk)] == {str(multi.pk): ["Tue"]}
    assert str(boolean.pk) not in payload[str(signup.pk)]
    assert str(text.pk) not in payload[str(signup.pk)]


@pytest.mark.django_db
def test_payload_collapses_text_to_a_flag_rather_than_shipping_the_prose(event, team_member) -> None:
    """A 2000-char answer is already in the table cell; sending it twice bloats every row."""
    text = _q(event, SignupQuestion.Type.TEXT, "Notes")
    signup = _signup(event, team_member, {str(text.pk): "x" * 2000})

    assert answers_payload([signup], [text])[str(signup.pk)] == {str(text.pk): True}


@pytest.mark.django_db
def test_malformed_stored_shapes_read_as_unanswered(event, team_member) -> None:
    """A shape that doesn't match the question type must degrade, never raise."""
    multi = _q(event, SignupQuestion.Type.MULTI, "Nights?", options=["Tue"], order=1)
    single = _q(event, SignupQuestion.Type.SINGLE, "Role", options=["Lead"], order=2)
    boolean = _q(event, SignupQuestion.Type.BOOLEAN, "TT bike?", order=3)
    signup = _signup(event, team_member, {
        str(multi.pk): "Tue",          # str where a list belongs
        str(single.pk): ["Lead"],      # list where a str belongs
        str(boolean.pk): "yes",        # str where a bool belongs
    })

    for q in (multi, single, boolean):
        assert normalized_answer(signup, q) is None
    assert answers_payload([signup], [multi, single, boolean])[str(signup.pk)] == {}


@pytest.mark.django_db
def test_panel_collapses_once_the_question_list_gets_long(event) -> None:
    """Thirty expanded facet cards would push the table off the screen."""
    qs = [_q(event, SignupQuestion.Type.BOOLEAN, f"Q{i}", order=i) for i in range(6)]

    assert panel_starts_open(qs) is True
    assert panel_starts_open([*qs, _q(event, SignupQuestion.Type.BOOLEAN, "Q7", order=7)]) is False
    assert panel_starts_open([]) is False


@pytest.mark.django_db
def test_event_admin_gets_the_panel_and_the_payload(client, event, event_admin) -> None:
    q = _q(event, SignupQuestion.Type.MULTI, "Nights?", options=["Tue", "Wed"])
    _signup(event, event_admin, {str(q.pk): ["Tue"]})
    client.force_login(event_admin)

    resp = client.get(reverse("events:event_detail", args=[event.pk]))
    body = resp.content.decode()

    assert resp.context["answer_facets"]
    assert 'id="answer-panel"' in body
    assert 'id="answer-payload"' in body
    assert 'id="answer-chips"' in body


@pytest.mark.django_db
def test_non_admin_never_receives_the_answer_payload(client, event, team_member) -> None:
    """With show_signups on a team member sees names only; the payload would leak answers.

    Without this, any team member could read every rider's answers straight out of the HTML.
    """
    event.show_signups = True
    event.save(update_fields=["show_signups"])
    q = _q(event, SignupQuestion.Type.MULTI, "Nights?", options=["Tue", "Wed"])
    _signup(event, team_member, {str(q.pk): ["Tue"]})
    client.force_login(team_member)

    resp = client.get(reverse("events:event_detail", args=[event.pk]))
    body = resp.content.decode()

    assert resp.context["answer_facets"] == []
    assert resp.context["answer_payload"] == {}
    assert 'id="answer-payload"' not in body
    assert 'id="answer-panel"' not in body
    assert "Tue" not in body


@pytest.mark.django_db
def test_answers_header_no_longer_claims_to_sort(client, event, event_admin) -> None:
    """Its textContent is every answer concatenated, so header sort was meaningless."""
    q = _q(event, SignupQuestion.Type.MULTI, "Nights?", options=["Tue"])
    _signup(event, event_admin, {str(q.pk): ["Tue"]})
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert '<th data-col="custom_answers" data-sort="text"' not in body
    assert 'data-col="custom_answers">Answers' in body
    assert "sort in a facet" in body


@pytest.mark.django_db
def test_rows_carry_a_pk_and_an_explicit_name_sort_value(client, event, event_admin) -> None:
    """The name cell now also holds the mobile answer line, which must not pollute sort/search."""
    q = _q(event, SignupQuestion.Type.BOOLEAN, "TT bike?")
    signup = _signup(event, event_admin, {str(q.pk): True})
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert f'data-signup-pk="{signup.pk}"' in body
    assert 'data-sort-value="' in body
    assert "data-answer-focus" in body


@pytest.mark.django_db
def test_zero_match_state_ships_an_escape_hatch(client, event, event_admin) -> None:
    """A filter matching nothing must not leave a bare header row with no way back."""
    q = _q(event, SignupQuestion.Type.MULTI, "Nights?", options=["Tue"])
    _signup(event, event_admin, {str(q.pk): ["Tue"]})
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert 'id="answer-empty"' in body
    assert 'id="answer-empty-clear"' in body
    assert "No signup matches these answers." in body


@pytest.fixture
def captain(user_model, event):
    """Build a squad captain on the event: passes the eligibility gate, not event_admin.

    Returns:
        The captain user.

    """
    from apps.events.models import Squad

    squad = Squad.objects.create(event=event, name="Squad A")
    user = user_model.objects.create_user(
        username="cap", email="cap@example.test", permission_overrides={"team_member": True},
    )
    squad.captains.add(user)
    return user


@pytest.mark.django_db
def test_squad_captain_can_filter_by_answers(client, event, captain) -> None:
    """Captains build lineups, so they get the signups table and the facet panel."""
    q = _q(event, SignupQuestion.Type.MULTI, "Nights?", options=["Tue", "Wed"])
    _signup(event, captain, {str(q.pk): ["Tue"]})
    client.force_login(captain)

    resp = client.get(reverse("events:event_detail", args=[event.pk]))
    body = resp.content.decode()

    assert resp.context["can_view_signup_table"] is True
    assert resp.context["answer_facets"]
    assert 'id="answer-panel"' in body
    assert 'id="answer-payload"' in body


@pytest.mark.django_db
def test_captain_sees_the_table_even_with_show_signups_off(client, event, captain) -> None:
    """The eligibility gate stands on its own; it must not depend on the public toggle."""
    assert event.show_signups is False
    _q(event, SignupQuestion.Type.BOOLEAN, "TT bike?")
    _signup(event, captain, {})
    client.force_login(captain)

    resp = client.get(reverse("events:event_detail", args=[event.pk]))

    assert resp.context["can_view_signups"] is True
    assert 'id="answer-panel"' in resp.content.decode()


@pytest.mark.django_db
def test_rider_notes_stay_admin_only(client, event, captain, event_admin) -> None:
    """The signup form promises riders the notes box is "for the event admins"."""
    signup = _signup(event, captain, {})
    signup.notes = "Please do not schedule me against my old team."
    signup.save(update_fields=["notes"])
    _q(event, SignupQuestion.Type.BOOLEAN, "TT bike?")

    client.force_login(captain)
    cap_body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()
    assert "Please do not schedule me" not in cap_body
    # Match the cells, not the bare attribute: it also appears in the mobile force-hide CSS.
    assert '<td data-col="notes"' not in cap_body

    client.force_login(event_admin)
    admin_body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()
    assert "Please do not schedule me" in admin_body
    assert '<td data-col="notes"' in admin_body


@pytest.mark.django_db
def test_notes_header_and_cell_are_gated_together(client, event, captain) -> None:
    """The sorter maps header position onto row cells, so they must vanish as a pair."""
    _q(event, SignupQuestion.Type.BOOLEAN, "TT bike?")
    _signup(event, captain, {})
    client.force_login(captain)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert '<th data-col="notes"' not in body
    assert '<td data-col="notes"' not in body

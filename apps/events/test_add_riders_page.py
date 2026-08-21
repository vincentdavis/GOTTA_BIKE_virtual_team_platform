"""Per-squad add-riders page: a full-page picker a captain can use.

The dropdown on the manage page can't express "answered Tuesday and sits in this power
band". This page can, and unlike the admin assign page it is scoped to one squad so a
captain may open it.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, SignupQuestion, Squad, SquadMember


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


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad with a power ceiling.

    Returns:
        The squad.

    """
    return Squad.objects.create(
        event=event, name="B DEV", max_zftp_wkg=Decimal("3.74"), enforce_max_zftp_wkg=True,
    )


def _rider(user_model, event, username, **fields):
    """Register a rider for the event.

    Everyone gets ``team_member``: the page sits behind @team_member_required, which is
    true of any real rider, and without it the fixtures would fail the wrong gate.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username=username, email=f"{username}@example.test", first_name=username.title(),
        permission_overrides={"team_member": True}, **fields,
    )
    EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)
    return user


def _url(event, squad):
    """Build the page URL.

    Returns:
        The URL.

    """
    return reverse("events:squad_add_riders", args=[event.pk, squad.pk])


@pytest.mark.django_db
def test_a_squad_captain_can_open_it(client, event, squad, user_model) -> None:
    """The whole point: the admin assign page is event_admin-only, this one isn't."""
    captain = _rider(user_model, event, "cap")
    squad.captains.add(captain)
    client.force_login(captain)

    assert client.get(_url(event, squad)).status_code == 200


@pytest.mark.django_db
def test_an_unrelated_team_member_cannot(client, event, squad, team_member) -> None:
    """Being a team member is not enough -- the gate is managing *this* squad."""
    client.force_login(team_member)

    resp = client.get(_url(event, squad))

    assert resp.status_code == 302
    assert resp["Location"] == reverse("events:event_detail", args=[event.pk])


@pytest.mark.django_db
def test_lists_registered_riders_and_marks_eligibility(client, event, squad, user_model, event_admin) -> None:
    _rider(user_model, event, "strong", z_ftp=Decimal("300.0"), z_metrics_weight_grams=66000)  # 4.55
    _rider(user_model, event, "inband", z_ftp=Decimal("230.0"), z_metrics_weight_grams=66000)  # 3.48
    client.force_login(event_admin)

    body = client.get(_url(event, squad)).content.decode()

    assert 'data-eligible="0"' in body
    assert 'data-eligible="1"' in body
    assert "Strong" in body and "Inband" in body


@pytest.mark.django_db
def test_existing_members_are_not_offered_again(client, event, squad, user_model, event_admin) -> None:
    already = _rider(user_model, event, "already")
    SquadMember.objects.create(squad=squad, user=already, status=SquadMember.Status.MEMBER)
    client.force_login(event_admin)

    body = client.get(_url(event, squad)).content.decode()

    assert "Already" not in body


@pytest.mark.django_db
def test_answer_facets_render_against_this_table(client, event, squad, user_model, event_admin) -> None:
    """The panel names its own table, so the shared script filters the right rows."""
    SignupQuestion.objects.create(
        event=event, label="Which nights?", question_type=SignupQuestion.Type.MULTI,
        options=["Tue", "Thu"], order=1,
    )
    _rider(user_model, event, "someone")
    client.force_login(event_admin)

    body = client.get(_url(event, squad)).content.decode()

    assert 'data-answer-facets="add-riders"' in body
    assert 'data-signup-table="add-riders"' in body
    assert 'id="answer-payload"' in body
    assert "Which nights?" in body


@pytest.mark.django_db
def test_adding_a_rider_returns_to_this_page(client, event, squad, user_model, event_admin) -> None:
    # An unbounded squad: this test is about the redirect, not about eligibility.
    squad = Squad.objects.create(event=event, name="Open")
    rider = _rider(user_model, event, "newbie")
    signup = EventSignup.objects.get(event=event, user=rider)
    client.force_login(event_admin)

    resp = client.post(
        reverse("events:squad_assign", args=[event.pk]),
        data={"signup_id": signup.pk, "squad_id": squad.pk, "next": _url(event, squad)},
    )

    assert resp.status_code == 302
    assert resp["Location"] == _url(event, squad)
    assert SquadMember.objects.filter(squad=squad, user=rider).exists()


@pytest.mark.django_db
def test_an_offsite_next_is_refused(client, event, squad, user_model, event_admin) -> None:
    """`next` is attacker-controllable on a form any captain can reach."""
    squad = Squad.objects.create(event=event, name="Open")
    rider = _rider(user_model, event, "newbie")
    signup = EventSignup.objects.get(event=event, user=rider)
    client.force_login(event_admin)

    resp = client.post(
        reverse("events:squad_assign", args=[event.pk]),
        data={"signup_id": signup.pk, "squad_id": squad.pk, "next": "https://evil.example/steal"},
    )

    assert resp["Location"] == reverse("events:event_detail", args=[event.pk])


@pytest.mark.django_db
def test_the_manage_panel_links_here(client, event, squad, user_model, event_admin) -> None:
    _rider(user_model, event, "someone")
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    assert _url(event, squad) in body


@pytest.mark.django_db
def test_the_zr_filter_offers_velo_tiers_not_letters(client, event, squad, user_model, event_admin) -> None:
    """The picker lists vELO tiers, not letters.

    ZR stores tiers (Diamond..Copper); the picker offered A-E, so choosing any ZR
    value matched nothing at all.
    """
    from apps.events.models import ZR_CATEGORY_ORDER

    _rider(user_model, event, "someone")
    client.force_login(event_admin)

    body = client.get(_url(event, squad)).content.decode()

    for tier in ZR_CATEGORY_ORDER:
        assert f'<option value="{tier}">{tier}</option>' in body, tier
    assert '<option value="A">A</option>' not in body.split('id="filter-zr"')[1].split("</select>")[0]


@pytest.mark.django_db
def test_a_zr_tier_actually_matches_a_rider(client, event, squad, user_model, event_admin) -> None:
    """A tier the picker offers actually matches a row.

    The row's data-zr must carry the same spelling as the option, or the filter is
    still cosmetic.
    """
    from apps.zwiftracing.models import ZRRider

    rider = _rider(user_model, event, "emmy")
    rider.zwid = 4242
    rider.save(update_fields=["zwid"])
    ZRRider.objects.create(zwid=4242, name="Emmy", race_current_category="Emerald")
    client.force_login(event_admin)

    body = client.get(_url(event, squad)).content.decode()

    assert 'data-zr="Emerald"' in body
    assert '<option value="Emerald">Emerald</option>' in body


@pytest.mark.django_db
def test_riders_with_no_zr_record_can_be_isolated(client, event, squad, user_model, event_admin) -> None:
    """Without this option there is no way to see exactly who cannot be judged.

    A rider with no ZRRider row renders data-zr="", which every tier excludes and
    "All ZR" buries among everyone else.
    """
    from apps.zwiftracing.models import ZRRider

    rated = _rider(user_model, event, "emmy")
    rated.zwid = 4242
    rated.save(update_fields=["zwid"])
    ZRRider.objects.create(zwid=4242, name="Emmy", race_current_category="Emerald")
    _rider(user_model, event, "nobody")
    client.force_login(event_admin)

    body = client.get(_url(event, squad)).content.decode()

    assert '<option value="__none__">No ZR record</option>' in body
    assert 'data-zr="Emerald"' in body
    assert 'data-zr=""' in body

"""CSV export of an event's signups.

Restricted more tightly than the signup table itself: the export is a bulk extract of
every rider's details in one file, so it is limited to the people running the event.
"""

import csv
import io
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, SignupQuestion, Squad, SquadMember


@pytest.fixture
def event(db) -> Event:
    """Build a visible event with a head captain role and a coordinator role.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL Season 5", start_date=today, end_date=today + timedelta(days=30),
        visible=True, signups_open=True,
        head_captain_role_id=777, coordinator_role_ids=[555],
    )


def _rider(user_model, event, username, **extra):
    """Register a rider for the event.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(
        username=username, email=f"{username}@example.test",
        first_name=username.title(), last_name="R", **extra,
    )
    EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)
    return user


def _actor(user_model, username, **extra):
    """Build a team member with no elevated permissions beyond those given.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username=username, email=f"{username}@example.test",
        permission_overrides={"team_member": True}, **extra,
    )


def _read(response) -> list[list[str]]:
    """Parse a CSV response body.

    Returns:
        Rows including the header.

    """
    return list(csv.reader(io.StringIO(response.content.decode())))


@pytest.mark.django_db
def test_the_head_captain_can_export(client, event, user_model) -> None:
    _rider(user_model, event, "ann")
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    resp = client.get(reverse("events:signup_export", args=[event.pk]))

    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert "zrl-season-5-signups.csv" in resp["Content-Disposition"]


@pytest.mark.django_db
def test_a_coordinator_can_export(client, event, user_model) -> None:
    _rider(user_model, event, "ann")
    client.force_login(_actor(user_model, "coord", discord_roles={"555": "EMEA Coordinator"}))

    assert client.get(reverse("events:signup_export", args=[event.pk])).status_code == 200


@pytest.mark.django_db
def test_an_event_admin_cannot(client, event, event_admin, user_model) -> None:
    """The button is hidden for them, and the URL has to refuse them too.

    Hiding a link is not access control -- the export is one guessable GET away.
    """
    _rider(user_model, event, "ann")
    client.force_login(event_admin)

    assert client.get(reverse("events:signup_export", args=[event.pk])).status_code == 403


@pytest.mark.django_db
def test_a_plain_team_member_cannot(client, event, team_member, user_model) -> None:
    _rider(user_model, event, "ann")
    client.force_login(team_member)

    assert client.get(reverse("events:signup_export", args=[event.pk])).status_code == 403


@pytest.mark.django_db
def test_a_coordinator_role_on_another_event_does_not_carry_over(client, event, user_model) -> None:
    """coordinator_role_ids are per-event, so the gate must be too."""
    other = Event.objects.create(
        title="Other", start_date=date.today(), end_date=date.today() + timedelta(days=1),
        visible=True, coordinator_role_ids=[555],
    )
    _rider(user_model, event, "ann")
    client.force_login(_actor(user_model, "coord2", discord_roles={"555": "EMEA Coordinator"}))

    assert client.get(reverse("events:signup_export", args=[other.pk])).status_code == 200
    assert client.get(reverse("events:signup_export", args=[event.pk])).status_code == 200
    event.coordinator_role_ids = [999]
    event.save(update_fields=["coordinator_role_ids"])
    assert client.get(reverse("events:signup_export", args=[event.pk])).status_code == 403


@pytest.mark.django_db
def test_only_registered_riders_are_exported(client, event, user_model) -> None:
    _rider(user_model, event, "ann")
    gone = _rider(user_model, event, "gone")
    EventSignup.objects.filter(user=gone).update(status=EventSignup.Status.WITHDRAWN)
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    rows = _read(client.get(reverse("events:signup_export", args=[event.pk])))

    assert len(rows) == 2                      # header + Ann
    assert rows[1][0] == "Ann R"


@pytest.mark.django_db
def test_hidden_columns_are_still_exported(client, event, user_model) -> None:
    """The export is the whole list, not whatever the column picker currently shows.

    Phenotype and the Max90 ratings default to hidden in the table.
    """
    _rider(user_model, event, "ann")
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    header = _read(client.get(reverse("events:signup_export", args=[event.pk])))[0]

    for column in ("Phenotype", "Max90 Rating", "zMAP", "Zauth", "ZWID", "Notes"):
        assert column in header


@pytest.mark.django_db
def test_each_signup_question_gets_its_own_column(client, event, user_model) -> None:
    q = SignupQuestion.objects.create(
        event=event, label="Preferred day?", question_type=SignupQuestion.Type.SINGLE,
        options=["Tue", "Thu"], order=1,
    )
    rider = _rider(user_model, event, "ann")
    EventSignup.objects.filter(user=rider).update(custom_answers={str(q.pk): "Thu"})
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    rows = _read(client.get(reverse("events:signup_export", args=[event.pk])))

    assert rows[0][-1] == "Preferred day?"
    assert rows[1][-1] == "Thu"


@pytest.mark.django_db
def test_squads_and_zauth_land_in_the_right_columns(client, event, user_model) -> None:
    squad = Squad.objects.create(event=event, name="Alpha")
    rider = _rider(
        user_model, event, "ann", zwid_verified=True,
        zwid_verification_method=user_model.VerificationMethod.ZAUTH,
    )
    SquadMember.objects.create(squad=squad, user=rider, status=SquadMember.Status.MEMBER)
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    rows = _read(client.get(reverse("events:signup_export", args=[event.pk])))
    row = dict(zip(rows[0], rows[1], strict=True))

    assert row["Squads"] == "Alpha"
    assert row["Zauth"] == "Zauth"
    assert row["Name"] == "Ann R"


@pytest.mark.django_db
def test_the_button_only_renders_for_those_who_may_use_it(client, event, event_admin, user_model) -> None:
    _rider(user_model, event, "ann")
    export_url = reverse("events:signup_export", args=[event.pk])

    client.force_login(event_admin)
    assert export_url not in client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    client.force_login(_actor(user_model, "hc2", discord_roles={"777": "Head Captain"}))
    assert export_url in client.get(reverse("events:event_detail", args=[event.pk])).content.decode()


@pytest.mark.django_db
def test_rider_text_cannot_smuggle_a_spreadsheet_formula(client, event, user_model) -> None:
    """Notes and free-text answers are rider-authored and land in a file opened in Excel.

    A cell starting with = + - or @ is evaluated on open, so it is prefixed to stay text.
    """
    q = SignupQuestion.objects.create(
        event=event, label="Anything else?", question_type=SignupQuestion.Type.TEXT, order=1,
    )
    rider = _rider(user_model, event, "ann")
    EventSignup.objects.filter(user=rider).update(
        notes='=HYPERLINK("http://evil.test","click")',
        custom_answers={str(q.pk): "+1234567890"},
    )
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    rows = _read(client.get(reverse("events:signup_export", args=[event.pk])))
    row = dict(zip(rows[0], rows[1], strict=True))

    assert row["Notes"].startswith("'=")
    assert row["Anything else?"].startswith("'+")


@pytest.mark.django_db
def test_negative_numbers_are_left_as_numbers(client, event, user_model) -> None:
    """Blanket-prefixing every cell starting with "-" would break numeric columns."""
    from apps.zwiftracing.models import ZRRider

    rider = _rider(user_model, event, "ann", zwid=4242)
    ZRRider.objects.create(zwid=4242, name="Ann", race_current_rating=-12)
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    rows = _read(client.get(reverse("events:signup_export", args=[event.pk])))
    row = dict(zip(rows[0], rows[1], strict=True))

    assert not row["Current Rating"].startswith("'")   # a Decimal, so never prefixed
    assert row["Current Rating"].startswith("-12")
    assert rider.pk

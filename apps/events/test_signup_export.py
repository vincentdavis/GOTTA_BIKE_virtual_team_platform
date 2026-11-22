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
    """With no columns chosen, the export is the default list, not whatever the table shows.

    Phenotype and the Max90 ratings default to hidden in the table.
    """
    _rider(user_model, event, "ann")
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    header = _read(client.get(reverse("events:signup_export", args=[event.pk])))[0]

    for column in ("Phenotype", "vELO 1 Max90", "zMAP", "Zauth", "ZWID", "Notes"):
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
def test_negative_numbers_are_left_as_numbers(client, event, user_model, rider_profile_factory) -> None:
    """Blanket-prefixing every cell starting with "-" would break numeric columns."""
    rider = _rider(user_model, event, "ann", zwid=4242)
    rider_profile_factory(zwid=4242, name="Ann", velo=-12.0)
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    rows = _read(client.get(reverse("events:signup_export", args=[event.pk])))
    row = dict(zip(rows[0], rows[1], strict=True))

    assert not row["vELO 1"].startswith("'")   # a number, so never prefixed
    assert row["vELO 1"].startswith("-12")
    assert rider.pk


# --- Email addresses: off by default, turned on per event by an event admin (Role Setup page) ---


def _with_emails(event: Event) -> Event:
    """Turn the event's email download on, as an event admin would on Role Setup.

    Returns:
        The event.

    """
    event.signup_export_emails = True
    event.save(update_fields=["signup_export_emails"])
    return event


@pytest.mark.django_db
def test_emails_are_off_by_default(client, event, user_model) -> None:
    """A new event never lets emails out: no column, no menu, and the URL refuses them."""
    _rider(user_model, event, "ann")
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))
    export_url = reverse("events:signup_export", args=[event.pk])

    assert event.signup_export_emails is False
    assert "Email" not in _read(client.get(export_url))[0]
    assert client.get(export_url, {"email": "1"}).status_code == 403
    assert 'value="email"' not in client.get(reverse("events:event_detail", args=[event.pk])).content.decode()


@pytest.mark.django_db
def test_with_the_setting_on_a_download_can_include_emails(client, event, user_model) -> None:
    _with_emails(event)
    _rider(user_model, event, "ann")
    client.force_login(_actor(user_model, "coord", discord_roles={"555": "EMEA Coordinator"}))
    export_url = reverse("events:signup_export", args=[event.pk])

    resp = client.get(export_url, {"email": "1"})
    rows = _read(resp)
    row = dict(zip(rows[0], rows[1], strict=True))

    assert resp.status_code == 200
    assert rows[0][:3] == ["Name", "Discord", "Email"]
    assert row["Email"] == "ann@example.test"
    assert "zrl-season-5-signups-with-emails.csv" in resp["Content-Disposition"]
    # Still opt-in per download: the plain download stays email-free.
    assert "Email" not in _read(client.get(export_url))[0]


@pytest.mark.django_db
def test_the_setting_lets_no_one_else_download(client, event, event_admin, team_member, user_model) -> None:
    """Turning emails on widens what the download carries, never who may download."""
    _with_emails(event)
    _rider(user_model, event, "ann")
    export_url = reverse("events:signup_export", args=[event.pk])

    for user in (event_admin, team_member):
        client.force_login(user)
        assert client.get(export_url, {"email": "1"}).status_code == 403


@pytest.mark.django_db
def test_the_detail_page_offers_the_email_download_only_when_on(client, event, user_model) -> None:
    """The dialog's Email checkbox appears only when allowed, and is never ticked for you."""
    _rider(user_model, event, "ann")
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))
    detail_url = reverse("events:event_detail", args=[event.pk])

    assert 'value="email"' not in client.get(detail_url).content.decode()
    _with_emails(event)
    body = client.get(detail_url).content.decode()
    email_box = body[body.index('value="email"') :].split(">", 1)[0]
    assert 'data-default="0"' in email_box
    assert "checked" not in email_box


@pytest.mark.django_db
def test_only_event_admins_can_change_the_setting(client, event, event_admin, user_model) -> None:
    """The head captain and coordinators edit Role Setup and do the downloading, so they can't flip it."""
    setting_url = reverse("events:event_signup_emails_setting", args=[event.pk])

    for actor in (
        _actor(user_model, "hc", discord_roles={"777": "Head Captain"}),
        _actor(user_model, "coord", discord_roles={"555": "EMEA Coordinator"}),
        user_model.objects.create_user(
            username="roles",
            permission_overrides={"team_member": True, "assign_roles": True},
        ),
    ):
        client.force_login(actor)
        assert client.post(setting_url, {"signup_export_emails": "on"}).status_code == 403
        event.refresh_from_db()
        assert event.signup_export_emails is False

    client.force_login(event_admin)
    resp = client.post(setting_url, {"signup_export_emails": "on"})
    assert resp.status_code == 302
    assert resp["Location"] == reverse("events:event_role_setup", args=[event.pk])
    event.refresh_from_db()
    assert event.signup_export_emails is True

    client.post(setting_url, {})
    event.refresh_from_db()
    assert event.signup_export_emails is False
    assert client.get(setting_url).status_code == 405


@pytest.mark.django_db
def test_role_setup_shows_the_switch_only_to_event_admins(client, event, event_admin, user_model) -> None:
    role_setup_url = reverse("events:event_role_setup", args=[event.pk])
    setting_url = reverse("events:event_signup_emails_setting", args=[event.pk])

    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))
    body = client.get(role_setup_url).content.decode()
    assert setting_url not in body
    assert "Only event admins can change this." in body
    assert "Email addresses can't be downloaded" in body

    client.force_login(event_admin)
    body = client.get(role_setup_url).content.decode()
    assert setting_url in body
    assert 'name="signup_export_emails"' in body


def test_the_role_form_cannot_set_it() -> None:
    """The main Role Setup form is open to the head captain, so the setting must stay out of it."""
    from apps.events.forms import EventRoleSetupForm

    assert "signup_export_emails" not in EventRoleSetupForm.base_fields


# --- Choosing the columns: ?cols=, from the download dialog ---


def _hc_download(client, user_model, event, **params) -> list[list[str]]:
    """Download as the head captain.

    Returns:
        The parsed rows, header first.

    """
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))
    resp = client.get(reverse("events:signup_export", args=[event.pk]), params)
    assert resp.status_code == 200
    return _read(resp)


@pytest.mark.django_db
def test_chosen_columns_come_out_in_the_lists_order_with_name_first(client, event, user_model) -> None:
    """Ticking order doesn't matter; unknown keys are ignored; Name is always there."""
    _rider(user_model, event, "ann", zwid=4242)

    rows = _hc_download(client, user_model, event, cols="zwid,nonsense,discord")

    assert rows[0] == ["Name", "Discord", "ZWID"]
    assert rows[1] == ["Ann R", "", "4242"]


@pytest.mark.django_db
def test_repeated_cols_from_the_dialogs_checkboxes_work_too(client, event, user_model) -> None:
    _rider(user_model, event, "ann")

    rows = _hc_download(client, user_model, event, cols=["name", "zauth", "race_verified"])

    assert rows[0] == ["Name", "Zauth", "Race Verified"]


@pytest.mark.django_db
def test_unticking_everything_still_gives_names_not_the_defaults(client, event, user_model) -> None:
    """The dialog always sends cols=name, so "nothing chosen" can't fall back to every column."""
    _rider(user_model, event, "ann")

    assert _hc_download(client, user_model, event, cols="name")[0] == ["Name"]


@pytest.mark.django_db
def test_the_default_columns(client, event, user_model) -> None:
    """No cols at all: what the CSV always carried, vELO 1 headed as such, and no stat columns."""
    q = SignupQuestion.objects.create(
        event=event,
        label="Preferred day?",
        question_type=SignupQuestion.Type.SINGLE,
        options=["Tue"],
        order=1,
    )
    _rider(user_model, event, "ann")

    header = _hc_download(client, user_model, event)[0]

    assert header == [
        "Name",
        "Discord",
        "ZWID",
        "Timezone",
        "Gender",
        "Squads",
        "Notes",
        "Signed Up",
        "ZP Category",
        "ZP Category (W)",
        "FTP",
        "WKG",
        "zFTP",
        "zFTP W/kg",
        "zMAP",
        "zMAP W/kg",
        "Zauth",
        "Race Verified",
        "vELO 1",
        "vELO 1 Cat",
        "vELO 1 Cat (W)",
        "vELO 1 Max30",
        "vELO 1 Max30 Cat",
        "vELO 1 Max30 Cat (W)",
        "vELO 1 Max90",
        "vELO 1 Max90 Cat",
        "vELO 1 Max90 Cat (W)",
        "Age",
        "Phenotype",
        q.label,
    ]


@pytest.mark.django_db
def test_velo1_womens_categories_are_a_womans_only(client, event, user_model, rider_profile_factory) -> None:
    """Both riders carry a women's category upstream; only the woman's is shown."""
    _rider(user_model, event, "ann", zwid=101)
    _rider(user_model, event, "bob", zwid=102)
    women = {"racing_women": "Diamond"}
    rider_profile_factory(
        zwid=101,
        name="Ann",
        velo=1700.0,
        category_racing="Ruby",
        gender="female",
        category=women,
    )
    rider_profile_factory(
        zwid=102,
        name="Bob",
        velo=1600.0,
        category_racing="Ruby",
        gender="male",
        category=women,
    )

    rows = _hc_download(client, user_model, event, cols="velo1,velo1_cat,velo1_cat_w")
    by_name = {row[0]: row for row in rows[1:]}

    assert rows[0] == ["Name", "vELO 1", "vELO 1 Cat", "vELO 1 Cat (W)"]
    assert by_name["Ann R"][1:] == ["1700", "Ruby", "Diamond"]
    assert by_name["Bob R"][1:] == ["1600", "Ruby", ""]


@pytest.mark.django_db
def test_the_tables_stat_columns_can_be_downloaded(client, event, user_model, rider_profile_factory) -> None:
    """The table's vELO 2 and other Zwift Racing columns, under its own headers, as numbers."""
    _rider(user_model, event, "ann", zwid=101)
    rider_profile_factory(
        zwid=101,
        name="Ann",
        velo2={"race": 650.4, "category": "Ruby"},
        race_counts={"wins": 0, "finishes": 12},
    )

    rows = _hc_download(client, user_model, event, cols="zr_v2_race,zr_v2_cat,zr_finishes,zr_wins")
    row = dict(zip(rows[0], rows[1], strict=True))

    assert row["vELO 2"] == "650"
    assert row["vELO 2 Cat"] == "Ruby"
    assert row["Finishes"] == "12"
    assert row["Wins"] == "0"  # a real zero, not a blank


@pytest.mark.django_db
def test_every_stat_column_is_offered(event) -> None:
    from apps.events import rider_stats, signup_export

    offered = {c.key for g in signup_export.export_groups(event, []) for c in g.columns}

    assert {column.key for column in rider_stats.STAT_COLUMNS} <= offered


@pytest.mark.django_db
def test_column_keys_are_unique_and_events_add_their_own(event) -> None:
    from apps.events import signup_export

    q = SignupQuestion.objects.create(event=event, label="Day?", question_type=SignupQuestion.Type.TEXT, order=1)
    event.squad_gender_required = True
    event.event_role = 4242
    event.signup_export_emails = True

    keys = [c.key for g in signup_export.export_groups(event, [q]) for c in g.columns]

    assert len(keys) == len(set(keys))
    assert {"email", "squad_gender", "event_role", f"q{q.pk}"} <= set(keys)


@pytest.mark.django_db
def test_email_in_cols_is_refused_while_the_setting_is_off(client, event, user_model) -> None:
    _rider(user_model, event, "ann")
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))
    export_url = reverse("events:signup_export", args=[event.pk])

    assert client.get(export_url, {"cols": "name,email"}).status_code == 403
    _with_emails(event)
    rows = _read(client.get(export_url, {"cols": "email,name"}))
    assert rows[0] == ["Name", "Email"]
    assert rows[1] == ["Ann R", "ann@example.test"]


@pytest.mark.django_db
def test_the_dialog_lists_the_columns_for_those_who_may_download(client, event, event_admin, user_model) -> None:
    _rider(user_model, event, "ann")
    detail_url = reverse("events:event_detail", args=[event.pk])

    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))
    body = client.get(detail_url).content.decode()
    assert 'id="signup-export-dialog"' in body
    assert 'name="cols" value="zr_v2_race"' in body
    assert 'name="cols" value="velo1_cat_w"' in body

    client.force_login(event_admin)
    assert 'id="signup-export-dialog"' not in client.get(detail_url).content.decode()

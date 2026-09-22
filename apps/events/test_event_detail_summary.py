"""The signup button and count badge, withdrawn riders, and where Add members lives.

The counts row that used to sit under the signup button (signups / male / female / squads)
was removed: the tab badges already carry the signup and squad numbers, and the squads and
riders themselves are listed in full further down the page. What survives it is the rule
those figures were written for -- the signup count is registered-only, so a rider who
withdrew is not counted, while their row stays in the table, marked.
"""

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad


@pytest.fixture
def event(db) -> Event:
    """Build a visible, currently-running event with signups on show.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="Summer Series",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=7),
        visible=True,
        show_signups=True,
    )


def _rider(user_model, name: str, gender: str):
    """Build a signed-up rider.

    discord_username is set because the signup table renders
    ``get_full_name|default:discord_username`` -- without it the row shows a blank name.

    Args:
        user_model: The active User class.
        name: Used for the username, e-mail and Discord name.
        gender: One of the User.Gender values.

    Returns:
        The created user.

    """
    return user_model.objects.create_user(
        username=name,
        email=f"{name}@example.test",
        discord_username=name,
        gender=gender,
        permission_overrides={"team_member": True},
    )


def _badge_count(body: str) -> int:
    """Read the registered-signup count off the badge beside the Signups heading.

    Args:
        body: The rendered page.

    Returns:
        The number the badge shows; fails the test when the page carries no badge.

    """
    match = re.search(r'id="signup-count-badge"[^>]*>\s*(\d+)\s*<', body)
    if match is None:
        pytest.fail("the page has no signup-count badge")
    return int(match.group(1))


@pytest.mark.django_db
def test_add_members_is_only_in_the_gear_menu(client, event, superuser):
    """It used to be a standalone button below the squads; the dialog it opens stays put."""
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "Add members" in body
    assert ">\n            Add Members\n          </button>" not in body  # the old button
    assert 'id="add_members_modal"' in body
    # One trigger, in the menu.
    assert body.count("add_members_modal').showModal()") == 1


@pytest.mark.django_db
def test_withdrawn_riders_are_left_out_of_the_counts(client, event, superuser, user_model):
    """Withdrawing flips the status rather than deleting the row.

    Before this, an event where people had pulled out still counted them as signed up.
    """
    EventSignup.objects.create(event=event, user=_rider(user_model, "in1", "male"))
    EventSignup.objects.create(event=event, user=_rider(user_model, "in2", "female"))
    EventSignup.objects.create(
        event=event,
        user=_rider(user_model, "out1", "male"),
        status=EventSignup.Status.WITHDRAWN,
    )
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert _badge_count(body) == 2, "the withdrawn rider must not be counted"


@pytest.mark.django_db
def test_withdrawn_riders_are_still_listed_and_marked(client, event, superuser, user_model):
    """They stay in the table -- who pulled out is worth seeing -- but are labelled.

    Labelled in words rather than by the dimmed row alone, so the state does not depend on
    seeing colour.
    """
    EventSignup.objects.create(
        event=event,
        user=_rider(user_model, "out1", "male"),
        status=EventSignup.Status.WITHDRAWN,
    )
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "out1" in body, "the withdrawn rider should still appear in the table"
    assert "data-signup-withdrawn" in body
    assert ">Withdrawn</span>" in body


@pytest.mark.django_db
def test_active_rows_carry_no_marker(client, event, superuser, user_model):
    EventSignup.objects.create(event=event, user=_rider(user_model, "in1", "male"))
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "data-signup-withdrawn" not in body
    assert ">Withdrawn</span>" not in body


@pytest.mark.django_db
def test_table_stays_reachable_when_everyone_withdrew(client, event, superuser, user_model):
    """The count is registered-only; the table is not.

    Gating the expand toggle on the count would leave the marked rows rendered but with no
    way to open them.
    """
    EventSignup.objects.create(
        event=event,
        user=_rider(user_model, "out1", "male"),
        status=EventSignup.Status.WITHDRAWN,
    )
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert _badge_count(body) == 0
    assert 'id="signups-toggle"' in body or "signups-content" in body
    assert ">Withdrawn</span>" in body


def test_facet_script_excludes_withdrawn_rows_from_the_badge():
    """The script rewrites #signup-count-badge from the rendered rows.

    Counting every row there would make the badge change value on load, disagreeing with the
    server-rendered registered-only count on the very same screen.
    """
    script = (
        Path(__file__).resolve().parent.parent.parent
        / "templates/events/_answer_facets_script.html"
    ).read_text()
    assert "data-signup-withdrawn" in script
    assert "var totalRows = activeRows.length;" in script


@pytest.mark.django_db
def test_signup_action_is_a_full_width_button_below_the_links(client, event, team_member):
    """It is the main thing most people come here to do, so it gets the card's full width.

    Placement is asserted by position: it must fall after the links row and before the
    Signups heading, not back beside it.
    """
    event.signups_open = True
    event.save(update_fields=["signups_open"])
    client.force_login(team_member)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "btn-block" in body
    assert "Sign up for this event" in body
    assert body.index("Sign up for this event") < body.index(">Signups</h3>")


@pytest.mark.django_db
def test_button_states_the_rider_is_already_signed_up(client, event, team_member):
    event.signups_open = True
    event.save(update_fields=["signups_open"])
    EventSignup.objects.create(event=event, user=team_member)
    client.force_login(team_member)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "signed up" in body
    assert "edit_signup_modal" in body
    assert "Sign up for this event" not in body


@pytest.mark.django_db
def test_closed_signups_say_so_without_a_button(client, event, team_member):
    event.signups_open = False
    event.save(update_fields=["signups_open"])
    client.force_login(team_member)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "Signups are closed for this event." in body
    assert "Sign up for this event" not in body


@pytest.mark.django_db
def test_no_counts_row_under_the_signup_button(client, event, team_member, user_model):
    """The signups / male / female / squads row was removed; the tab badges carry the numbers.

    Asserted by its labels rather than by the markup, so it stays true however such a row
    would be built if anyone added one back.
    """
    event.signups_open = True
    event.save(update_fields=["signups_open"])
    Squad.objects.create(event=event, name="A")
    EventSignup.objects.create(event=event, user=_rider(user_model, "m1", "male"))
    client.force_login(team_member)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    for label in (">signup</dt>", ">signups</dt>", ">male</dt>", ">female</dt>", ">squad</dt>", ">squads</dt>"):
        assert label not in body, f"{label} belongs to the removed counts row"
    # The counts a reader still gets, on the tabs.
    assert re.search(r">Squads\s*<span[^>]*>\s*1\s*</span>", body)
    assert re.search(r">Signups\s*<span[^>]*>\s*1\s*</span>", body)

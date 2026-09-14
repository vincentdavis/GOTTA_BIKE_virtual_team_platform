"""Which of a rider's event signups may appear on their roster card.

This is a permission question wearing a feature's clothes. An ordinary member can see who
signed up for an event ONLY when that event has ``show_signups`` on -- the event page gates
its own list exactly that way, and the flag defaults to OFF. Putting every signup on the
roster would hand every team member, for every event at once, the list each event's own page
deliberately withholds.

So the tests below are mostly about what does NOT appear.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventSignup
from apps.team.rosterv2 import build_roster_index, event_chips
from conftest import _make_user


def _event(title="Tour de Coalition", *, show_signups=True, visible=True, days_out=7, length=1):
    start = timezone.now().date() + timedelta(days=days_out)
    return Event.objects.create(
        title=title,
        start_date=start,
        end_date=start + timedelta(days=length),
        visible=visible,
        show_signups=show_signups,
    )


def _member(user_model, username, zwid):
    user = _make_user(user_model, username=username, permissions={"team_member": True})
    user.zwid = zwid
    user.zwid_verified = True
    user.zwid_verification_method = "zauth"
    user.discord_id = f"90000{zwid}"
    user.discord_username = username
    user.save()
    return user


def _signup(event, user, status=EventSignup.Status.REGISTERED):
    return EventSignup.objects.create(event=event, user=user, status=status)


def _card_events(viewer_id=None):
    rows = build_roster_index(viewer_id=viewer_id).rows
    return {row.card.name: [chip.name for chip in (row.account.events if row.account else ())] for row in rows}


# --- what shows ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_upcoming_event_that_shows_its_signups_appears(roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    _signup(_event("Tour de Coalition"), _member(user_model, "ada", 4242))

    assert _card_events()["Ada Racer"] == ["Tour de Coalition"]


@pytest.mark.django_db
def test_several_events_are_listed_soonest_first(roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "ada", 4242)
    _signup(_event("Later Race", days_out=30), user)
    _signup(_event("Sooner Race", days_out=3), user)

    assert _card_events()["Ada Racer"] == ["Sooner Race", "Later Race"]


@pytest.mark.django_db
def test_the_chip_links_to_the_event(auth_client, roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    event = _event("Tour de Coalition")
    _signup(event, _member(user_model, "ada", 4242))

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert reverse("events:event_detail", args=[event.pk]) in body
    assert "Tour de Coalition" in body


# --- what must not show ----------------------------------------------------------------------


@pytest.mark.django_db
def test_an_event_that_hides_its_signups_never_appears(roster_rider, user_model):
    """The crux. show_signups defaults to OFF, and its own page withholds the list."""
    roster_rider(zwid=4242, name="Ada Racer")
    _signup(_event("Private Selection", show_signups=False), _member(user_model, "ada", 4242))

    assert _card_events()["Ada Racer"] == []


@pytest.mark.django_db
def test_an_invisible_event_never_appears(roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    _signup(_event("Draft Event", visible=False), _member(user_model, "ada", 4242))

    assert _card_events()["Ada Racer"] == []


@pytest.mark.django_db
def test_a_finished_event_never_appears(roster_rider, user_model):
    """"Currently signed up for" means ahead of them, not a race they rode last month."""
    roster_rider(zwid=4242, name="Ada Racer")
    _signup(_event("Last Month", days_out=-40), _member(user_model, "ada", 4242))

    assert _card_events()["Ada Racer"] == []


@pytest.mark.django_db
def test_an_event_ending_today_still_appears(roster_rider, user_model):
    """The boundary: a multi-day event in its final day has not finished."""
    roster_rider(zwid=4242, name="Ada Racer")
    _signup(_event("Ends Today", days_out=-3, length=3), _member(user_model, "ada", 4242))

    assert _card_events()["Ada Racer"] == ["Ends Today"]


@pytest.mark.django_db
def test_a_withdrawn_signup_never_appears(roster_rider, user_model):
    """A withdrawal is a status change, not a deletion, so the row is still there."""
    roster_rider(zwid=4242, name="Ada Racer")
    _signup(_event(), _member(user_model, "ada", 4242), status=EventSignup.Status.WITHDRAWN)

    assert _card_events()["Ada Racer"] == []


@pytest.mark.django_db
def test_an_unverified_riders_signups_never_reach_the_card(roster_rider, user_model):
    """Same gate as the rest of the account half: an unverified zwid joins nothing.

    Otherwise typing somebody else's Zwift id would put your race calendar on their card.
    """
    roster_rider(zwid=4242, name="Someone Else")
    user = _member(user_model, "impostor", 4242)
    user.zwid_verified = False
    user.save(update_fields=["zwid_verified"])
    _signup(_event("Tour de Coalition"), user)

    assert _card_events()["Someone Else"] == []


# --- your own card ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_you_always_see_your_own_signups(roster_rider, user_model):
    """Nobody needs permission to be told what they themselves signed up for."""
    roster_rider(zwid=4242, name="Ada Racer")
    ada = _member(user_model, "ada", 4242)
    _signup(_event("Private Selection", show_signups=False), ada)

    assert _card_events(viewer_id=ada.pk)["Ada Racer"] == ["Private Selection"]


@pytest.mark.django_db
def test_your_own_exception_does_not_leak_anybody_elses(roster_rider, user_model):
    """The viewer sees their own private signup and NOT their teammate's."""
    roster_rider(zwid=4242, name="Ada Racer")
    roster_rider(zwid=4243, name="Bo Racer")
    ada = _member(user_model, "ada", 4242)
    bo = _member(user_model, "bo", 4243)
    private = _event("Private Selection", show_signups=False)
    _signup(private, ada)
    _signup(private, bo)

    events = _card_events(viewer_id=ada.pk)

    assert events["Ada Racer"] == ["Private Selection"]
    assert events["Bo Racer"] == []


@pytest.mark.django_db
def test_with_no_viewer_nobody_gets_the_private_exception(roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    _signup(_event("Private Selection", show_signups=False), _member(user_model, "ada", 4242))

    assert _card_events(viewer_id=None)["Ada Racer"] == []


# --- cost ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_signups_cost_one_query_however_many_riders(roster_rider, user_model):
    event = _event()
    for n in range(3):
        roster_rider(zwid=1000 + n, name=f"Rider {n}")
        _signup(event, _member(user_model, f"r{n}", 1000 + n))
    build_roster_index()
    with CaptureQueriesContext(connection) as few:
        build_roster_index()

    for n in range(3, 20):
        roster_rider(zwid=1000 + n, name=f"Rider {n}")
        _signup(event, _member(user_model, f"r{n}", 1000 + n))
    with CaptureQueriesContext(connection) as many:
        build_roster_index()

    assert len(many) == len(few)


@pytest.mark.django_db
def test_no_accounts_means_no_query_at_all():
    """The roster is mostly riders with no account here; they cannot have signed up."""
    with CaptureQueriesContext(connection) as captured:
        event_chips([])

    assert len(captured) == 0


@pytest.mark.django_db
def test_the_page_shows_the_reader_their_own_private_signup(client, roster_rider, user_model):
    """Through the view, as the rider themselves.

    The own-card exception only works if the view actually tells the index who is reading.
    Calling build_roster_index(viewer_id=...) in a test cannot see that wiring go missing,
    and its absence is silent -- the page simply never shows you your own events.
    """
    roster_rider(zwid=4242, name="Ada Racer")
    ada = _member(user_model, "ada", 4242)
    _signup(_event("Private Selection", show_signups=False), ada)
    client.force_login(ada)

    body = client.get(reverse("team:rosterv2")).content.decode()

    assert "Private Selection" in body


@pytest.mark.django_db
def test_the_page_does_not_show_a_teammates_private_signup(client, roster_rider, user_model):
    """The same page, read by somebody else, must not carry it."""
    roster_rider(zwid=4242, name="Ada Racer")
    ada = _member(user_model, "ada", 4242)
    _signup(_event("Private Selection", show_signups=False), ada)
    bo = _member(user_model, "bo", 9999)
    client.force_login(bo)

    body = client.get(reverse("team:rosterv2")).content.decode()

    assert "Ada Racer" in body
    assert "Private Selection" not in body

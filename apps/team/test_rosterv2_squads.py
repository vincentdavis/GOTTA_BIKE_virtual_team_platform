"""Finding riders on the roster by the name of a squad they are in.

Only squads in events running today count -- the event's dates include today -- and only
visible events: a hidden event is not shown anywhere. Membership of a visible event's squads
is already on that event's page for every team member, so the search reveals nothing new.
"""

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.events.models import Squad, SquadMember
from apps.team.rosterv2 import matching_squads, search, shared_roster_index, squad_summaries
from apps.team.test_rosterv2_events import _event, _member

RESULTS = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "roster-results"}


def _running(title="Tour de Coalition", **kwargs):
    """Make an event that started yesterday and ends tomorrow.

    Returns:
        The event.

    """
    return _event(title, days_out=-1, length=2, **kwargs)


def _squad(event, name, *members, status=SquadMember.Status.MEMBER):
    squad = Squad.objects.create(event=event, name=name)
    for user in members:
        SquadMember.objects.create(squad=squad, user=user, status=status)
    return squad


def _rider(roster_rider, user_model, username, zwid, name):
    roster_rider(zwid=zwid, name=name)
    return _member(user_model, username, zwid)


def _card_names(body):
    return [
        chunk.split(">", 1)[1].split("<", 1)[0]
        for chunk in body.split('<a class="link link-hover" href="/user/profile/')[1:]
    ]


# --- which squads match ----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_squad_name_finds_its_members(auth_client, roster_rider, user_model):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    bo = _rider(roster_rider, user_model, "bo", 1002, "Bo Climber")
    _rider(roster_rider, user_model, "cy", 1003, "Cy Sprinter")
    _squad(_running(), "Thunder Hawks", ada, bo)

    body = auth_client.get(reverse("team:roster"), {"q": "thunder"}).content.decode()

    assert sorted(_card_names(body)) == ["Ada Racer", "Bo Climber"]
    assert "matched: squad Thunder Hawks, Tour de Coalition" in body


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["THUNDER", "thünder hawks", "  hawks  "])
def test_squad_names_match_the_way_rider_names_do(roster_rider, user_model, query):
    """Case, accents and surrounding space are folded away, as for a rider's name."""
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_running(), "Thunder Hawks", ada)

    assert [squad.name for squad in matching_squads(query)] == ["Thunder Hawks"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("days_out", "length", "running"),
    [
        pytest.param(0, 0, True, id="one-day-event-today"),
        pytest.param(-5, 5, True, id="ends-today"),
        pytest.param(0, 5, True, id="starts-today"),
        pytest.param(1, 3, False, id="starts-tomorrow"),
        pytest.param(-5, 4, False, id="ended-yesterday"),
    ],
)
def test_only_events_running_today_count(roster_rider, user_model, days_out, length, running):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_event("League", days_out=days_out, length=length), "Alpha", ada)

    assert bool(matching_squads("alpha")) is running


@pytest.mark.django_db
def test_a_hidden_event_never_matches(roster_rider, user_model):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_running(visible=False), "Alpha", ada)

    assert matching_squads("alpha") == []


@pytest.mark.django_db
@pytest.mark.parametrize("status", [SquadMember.Status.PENDING, SquadMember.Status.REJECTED])
def test_only_full_members_are_found(auth_client, roster_rider, user_model, status):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_running(), "Alpha", ada, status=status)

    assert matching_squads("alpha")[0].user_ids == frozenset()
    body = auth_client.get(reverse("team:roster"), {"q": "alpha"}).content.decode()
    assert "Ada Racer" not in body


@pytest.mark.django_db
def test_a_blank_query_matches_no_squad(roster_rider, user_model):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_running(), "Alpha", ada)

    assert matching_squads("") == []
    assert matching_squads("   ") == []


@pytest.mark.django_db
def test_the_lookup_costs_two_queries_however_many_squads_and_members(roster_rider, user_model):
    event = _running()
    riders = [_rider(roster_rider, user_model, f"r{n}", 2000 + n, f"Rider {n}") for n in range(6)]
    _squad(event, "Alpha", *riders[:3])
    with CaptureQueriesContext(connection) as few:
        matching_squads("alpha")

    for n in range(5):
        _squad(event, f"Alpha {n}", *riders)
    with CaptureQueriesContext(connection) as many:
        matching_squads("alpha")

    assert len(few) == len(many) == 2


@pytest.mark.django_db
def test_no_matching_squad_costs_one_query(roster_rider, user_model):
    _squad(_running(), "Alpha")

    with CaptureQueriesContext(connection) as captured:
        assert matching_squads("zulu") == []

    assert len(captured) == 1


# --- how the matches combine with the name search -------------------------------------------


@pytest.mark.django_db
def test_squad_and_name_matches_are_both_found(roster_rider, user_model):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _rider(roster_rider, user_model, "al", 1002, "Alpha Male")  # the name matches too
    _squad(_running(), "Alpha", ada)
    rows = shared_roster_index().rows

    hits = {row.card.name: row.matched_as for row in search(rows, "alpha", matching_squads("alpha"))}

    assert hits == {"Ada Racer": "squad Alpha, Tour de Coalition", "Alpha Male": ""}


@pytest.mark.django_db
def test_a_rider_found_by_name_keeps_that_reason(roster_rider, user_model):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Alpha Racer")
    _squad(_running(), "Alpha", ada)
    rows = shared_roster_index().rows

    (hit,) = search(rows, "alpha", matching_squads("alpha"))

    assert hit.matched_as == ""  # the card's own name says why


@pytest.mark.django_db
def test_a_rider_in_two_matching_squads_names_both(roster_rider, user_model):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_running("Tour de Coalition"), "Alpha A", ada)
    _squad(_running("Spring League"), "Alpha B", ada)
    rows = shared_roster_index().rows

    (hit,) = search(rows, "alpha", matching_squads("alpha"))

    # In the order the squads are listed: the events' start dates, then their titles.
    assert hit.matched_as == "squad Alpha B, Spring League; squad Alpha A, Tour de Coalition"


@pytest.mark.django_db
def test_digits_still_find_a_rider_only_by_their_exact_zwid(roster_rider, user_model):
    """A squad named with the digits adds its members; a zwid merely containing them does not."""
    _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _rider(roster_rider, user_model, "bo", 11001, "Bo Climber")
    cy = _rider(roster_rider, user_model, "cy", 3003, "Cy Sprinter")
    _squad(_running(), "Squad 1001", cy)
    rows = shared_roster_index().rows

    hits = {row.card.name: row.matched_as for row in search(rows, "1001", matching_squads("1001"))}

    assert hits == {"Ada Racer": "", "Cy Sprinter": "squad Squad 1001, Tour de Coalition"}


# --- what the page says ----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_page_lists_the_matching_squads(auth_client, roster_rider, user_model):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    event = _running()
    _squad(event, "Thunder Hawks", ada)

    body = auth_client.get(reverse("team:roster"), {"q": "hawks"}).content.decode()

    assert '<p id="roster-squads-label" class="font-semibold">Squads in events running now</p>' in body
    assert 'aria-labelledby="roster-squads-label"' in body  # the list is named by it
    assert '<span class="font-medium">Thunder Hawks</span>' in body
    assert f'<a class="link" href="{reverse("events:event_detail", args=[event.pk])}">Tour de Coalition</a>' in body
    assert re.search(r"Tour de Coalition</a>:\s+1 member\s*</li>", body)


@pytest.mark.django_db
def test_members_without_a_card_are_counted_apart(auth_client, roster_rider, user_model):
    """A squad of three showing two cards would otherwise look like a broken search."""
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    bo = _rider(roster_rider, user_model, "bo", 1002, "Bo Climber")
    no_stats = user_model.objects.create_user(username="nostats", zwid=1003, zwid_verified=True)
    _squad(_running(), "Alpha", ada, bo, no_stats)

    body = auth_client.get(reverse("team:roster"), {"q": "alpha"}).content.decode()

    assert "3 members, 2 with a card here" in body
    assert sorted(_card_names(body)) == ["Ada Racer", "Bo Climber"]


@pytest.mark.django_db
def test_a_squad_with_no_cards_is_still_named(auth_client, roster_rider, user_model):
    """No cards to show, but the squad was found: say so, on screen and out loud."""
    _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    first = user_model.objects.create_user(username="first", zwid=1003, zwid_verified=True)
    second = user_model.objects.create_user(username="second")
    _squad(_running(), "Alpha", first, second)

    body = auth_client.get(reverse("team:roster"), {"q": "alpha"}, **RESULTS).content.decode()

    assert "2 members, 0 with a card here" in body
    assert _card_names(body) == []
    assert '<p id="roster-announcer" hx-swap-oob="innerHTML">No riders match. 1 squad matched.</p>' in body


@pytest.mark.django_db
def test_the_summary_counts_members_and_cards(roster_rider, user_model):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    stranger = user_model.objects.create_user(username="stranger")
    event = _running()
    _squad(event, "Alpha", ada, stranger)

    (summary,) = squad_summaries(matching_squads("alpha"), shared_roster_index().rows)

    assert summary == {
        "name": "Alpha",
        "event_title": "Tour de Coalition",
        "event_url": reverse("events:event_detail", args=[event.pk]),
        "members": 2,
        "on_roster": 1,
    }


@pytest.mark.django_db
def test_no_squad_summary_without_a_squad_match(auth_client, roster_rider, user_model):
    _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_running(), "Alpha")

    body = auth_client.get(reverse("team:roster"), {"q": "ada"}).content.decode()

    assert "Squads in events running now" not in body


@pytest.mark.django_db
def test_a_live_search_finds_squads_too(auth_client, roster_rider, user_model):
    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_running(), "Thunder Hawks", ada)

    body = auth_client.get(reverse("team:roster"), {"q": "hawks"}, **RESULTS).content.decode()

    assert "<html" not in body
    assert "Squads in events running now" in body
    assert _card_names(body) == ["Ada Racer"]
    assert '<p id="roster-announcer" hx-swap-oob="innerHTML">Showing 1 of 1 rider. 1 squad matched.</p>' in body


@pytest.mark.django_db
def test_a_squad_search_pages_like_any_other(auth_client, roster_rider, user_model, monkeypatch):
    from apps.team import views as team_views

    monkeypatch.setattr(team_views, "ROSTER_PAGE_SIZE", 2)
    riders = [_rider(roster_rider, user_model, f"r{n}", 3000 + n, f"Rider {n}") for n in range(3)]
    _squad(_running(), "Alpha", *riders)

    first = auth_client.get(reverse("team:roster"), {"q": "alpha"}).content.decode()
    more = auth_client.get(
        reverse("team:roster"),
        {"q": "alpha", "page": 2},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="roster-more",
    ).content.decode()

    assert len(_card_names(first)) == 2
    assert _card_names(more) == ["Rider 2"]
    assert "Squads in events running now" not in more  # the summary is not repeated per page


@pytest.mark.django_db
def test_the_not_linked_list_ignores_squads(client, membership_admin, roster_rider, user_model, monkeypatch):
    """A worklist is about accounts, not squads; the lookup is not even run."""
    from apps.team import views as team_views

    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_running(), "Alpha", ada)
    monkeypatch.setattr(team_views, "matching_squads", lambda query: pytest.fail("looked up squads"))
    client.force_login(membership_admin)

    body = client.get(reverse("team:roster"), {"q": "alpha", "link": "no_account"}).content.decode()

    assert "Squads in events running now" not in body


@pytest.mark.django_db
@pytest.mark.parametrize(("headers", "message"), [({}, "Roster viewed"), (RESULTS, "Roster results updated")])
def test_the_search_text_is_still_not_logged(auth_client, roster_rider, user_model, monkeypatch, headers, message):
    from apps.team import views as team_views

    ada = _rider(roster_rider, user_model, "ada", 1001, "Ada Racer")
    _squad(_running(), "Thunder Hawks", ada)
    lines = []
    for level in ("info", "debug"):
        monkeypatch.setattr(
            team_views.logfire, level, lambda message, _level=level, **kw: lines.append((_level, message, kw))
        )

    auth_client.get(reverse("team:roster"), {"q": "Thunder"}, **headers)

    assert "Thunder" not in repr(lines)
    assert [kw["squads_matched"] for _, logged, kw in lines if logged == message] == [1]

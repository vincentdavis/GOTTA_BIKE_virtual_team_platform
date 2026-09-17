"""Finding a rider by any name they are known by, without turning the box into an oracle.

Three things here are load-bearing and none is obvious:

* the zwid matches EXACTLY or not at all -- substring matching narrows a rider's id a digit
  at a time, which is how the roster this replaces behaves;
* an account's names only join the haystack of a card that actually joined that account,
  or the search box asserts the very link the card body refuses to assert;
* folding happens in Python, because SQLite's LIKE folds ASCII only while Postgres uses a
  locale-aware UPPER(), so a database-side search would behave differently in dev and prod.
"""

import pytest
from django.urls import reverse

from apps.team.rosterv2 import as_zwid, build_roster_index, fold, search, without_club_tag
from apps.zwiftracing.models import ZRRider
from conftest import _make_user


def _member(user_model, username, zwid, *, verified=True, **extra):
    user = _make_user(user_model, username=username, permissions={"team_member": True}, **extra)
    user.zwid = zwid
    user.zwid_verified = verified
    user.zwid_verification_method = "zauth"
    user.discord_id = f"90000{zwid}"
    user.discord_username = username
    user.save()
    return user


def _names(rows):
    return sorted(row.card.name for row in rows)


# --- folding -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ada Racer", "ada racer"),
        ("ADA RACER", "ada racer"),
        ("  Ada   Racer  ", "ada racer"),
        ("Ané Dupré", "ane dupre"),
        ("Jos&#233;", "jose"),  # ZwiftRacing stores entities raw; nothing unescapes them
        ("straße", "strasse"),
        ("", ""),
    ],
)
def test_folding_makes_names_comparable(raw, expected):
    assert fold(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ada R [COALITION]", "Ada R"),
        ("Ada R (COALITION)", "Ada R"),
        ("[COALITION]", ""),
        ("Ada R", "Ada R"),
    ],
)
def test_the_club_tag_can_be_stripped(raw, expected):
    assert without_club_tag(raw) == expected


@pytest.mark.parametrize("raw", ["1_2", "+12", "\uff11\uff12", " 12a", "", "12.0"])
def test_only_a_plain_run_of_ascii_digits_is_a_zwid(raw):
    """int() alone accepts underscores, signs and full-width digits; isdigit() accepts more."""
    assert as_zwid(raw) is None


def test_a_plain_number_is_a_zwid():
    assert as_zwid(" 4242 ") == 4242


# --- what a rider can be found by -----------------------------------------------------


@pytest.mark.django_db
def test_a_rider_is_found_by_their_zwift_name(roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")

    assert _names(search(build_roster_index().rows, "ada")) == ["Ada Racer"]


@pytest.mark.django_db
def test_case_and_accents_are_ignored(roster_rider):
    roster_rider(zwid=1001, name="Ané DUPRÉ")

    rows = build_roster_index().rows
    for query in ("ane", "ANE dupre", "Ané", "dupré"):
        assert len(search(rows, query)) == 1, query


@pytest.mark.django_db
def test_a_club_tag_is_searchable_and_so_is_the_name_without_it(roster_rider):
    """Both forms, never one instead of the other: hundreds of riders are looked up by club.

    The tag sits in the MIDDLE here on purpose. With a trailing tag both queries match the
    raw name anyway, so the test would pass with the stripped form never indexed at all --
    which is exactly what it did before a mutation caught it.
    """
    roster_rider(zwid=1001, name="Ada [COALITION] Racer")

    rows = build_roster_index().rows

    assert len(search(rows, "coalition")) == 1, "the club tag itself must stay findable"
    assert len(search(rows, "ada racer")) == 1, "the name must be findable without the tag in the way"


@pytest.mark.django_db
def test_a_rider_is_found_by_a_name_only_zwiftracing_kept(roster_rider):
    """Zauth keeps one merged name, so the losing spelling exists only in the source tables."""
    roster_rider(zwid=1001, name="A. Racer")
    ZRRider.objects.create(zwid=1001, name="Ada Racer [COALITION]")

    hits = search(build_roster_index().rows, "ada racer")

    assert len(hits) == 1
    assert hits[0].matched_as == "Ada Racer [COALITION]"


@pytest.mark.django_db
def test_a_joined_rider_is_found_by_their_discord_and_real_names(roster_rider, user_model):
    roster_rider(zwid=4242, name="Zwift Handle")
    _member(user_model, "ada_discord", 4242, first_name="Ada", last_name="Lovelace")

    rows = build_roster_index().rows

    assert len(search(rows, "ada_discord")) == 1
    assert len(search(rows, "lovelace")) == 1
    assert len(search(rows, "ada lovelace")) == 1


@pytest.mark.django_db
def test_a_real_name_is_searchable_but_never_rendered(auth_client, roster_rider, user_model):
    """Findable and displayed are different permissions, and only the haystack grants one."""
    roster_rider(zwid=4242, name="Zwift Handle")
    _member(user_model, "ada_discord", 4242, first_name="Ada", last_name="Lovelace")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "Lovelace" not in body
    assert "Zwift Handle" in body


@pytest.mark.django_db
def test_an_unverified_riders_names_never_enter_the_haystack(roster_rider, user_model):
    """The search box must not assert the link the card body refuses to.

    Searching a Discord handle and getting back somebody else's ZwiftPower card says that
    zwid is theirs -- the very claim the verification rule exists to withhold.
    """
    roster_rider(zwid=4242, name="Someone Elses Name")
    _member(user_model, "bob_smith", 4242, verified=False, first_name="Bob", last_name="Smith")

    rows = build_roster_index().rows

    assert search(rows, "bob") == []
    assert search(rows, "smith") == []
    assert search(rows, "bob_smith") == []


@pytest.mark.django_db
def test_a_contested_zwid_lends_nobodys_names(roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    _member(user_model, "first_claim", 4242, first_name="Ann", last_name="One")
    _member(user_model, "second_claim", 4242, first_name="Bea", last_name="Two")

    rows = build_roster_index().rows

    assert search(rows, "ann") == []
    assert search(rows, "bea") == []


# --- the zwid ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_zwid_matches_whole_and_never_as_a_fragment(roster_rider):
    """Substring matching lets a member narrow a zwid a digit at a time."""
    roster_rider(zwid=1234567, name="Ada Racer")

    rows = build_roster_index().rows

    assert len(search(rows, "1234567")) == 1
    assert search(rows, "123") == []
    assert search(rows, "234567") == []


@pytest.mark.django_db
def test_digits_still_match_a_name_that_contains_them(roster_rider):
    """A digit query must not short-circuit the name search: 110 real names contain digits."""
    roster_rider(zwid=999_111, name="Team 202")
    roster_rider(zwid=202, name="Low Number")

    hits = search(build_roster_index().rows, "202")

    assert _names(hits) == ["Low Number", "Team 202"]


@pytest.mark.django_db
def test_finding_a_rider_by_zwid_prints_it_back_on_the_card(auth_client, roster_rider):
    """Searching by id and being shown the id is how you confirm you found the right rider.

    This test used to assert the opposite. The card was built without the zwid, then Vincent
    asked for it -- Zwift's public id, printed by the roster this page replaces, and the thing
    you quote when asking anyone else about a rider.
    """
    roster_rider(zwid=8675309, name="Ada Racer")

    body = auth_client.get(reverse("team:roster") + "?q=8675309").content.decode()

    # Scoped to the card: the query echoes in the search box and its chip regardless, so a
    # whole-page assertion would pass with the card printing nothing.
    cards = body.split('class="card bg-base-100')[1:]
    assert cards, "expected the rider's card to render"
    assert [card for card in cards if "8675309" in card and "Ada Racer" in card]


# --- "matched:" -------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_hit_on_the_cards_own_name_explains_nothing(roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")

    assert search(build_roster_index().rows, "ada")[0].matched_as == ""


@pytest.mark.django_db
def test_a_hit_on_another_name_says_which(auth_client, roster_rider, user_model):
    roster_rider(zwid=4242, name="Zwift Handle")
    _member(user_model, "ada_discord", 4242, first_name="Ada", last_name="Lovelace")

    body = auth_client.get(reverse("team:roster") + "?q=lovelace").content.decode()

    assert "matched:" in body


# --- the page -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_search_survives_paging(auth_client, roster_rider):
    """Page 2 of a search must not quietly become page 2 of everyone."""
    for n in range(60):
        roster_rider(zwid=6000 + n, name=f"Sprinter {n:03d}")
    for n in range(5):
        roster_rider(zwid=7000 + n, name=f"Climber {n:03d}")

    first = auth_client.get(reverse("team:roster") + "?q=sprinter").content.decode()

    assert "q=sprinter" in first
    second = auth_client.get(reverse("team:roster") + "?q=sprinter&page=2").content.decode()
    assert "Climber" not in second
    assert "Showing 60 of 65 riders" in first


@pytest.mark.django_db
def test_no_match_says_so_and_offers_a_way_back(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")

    body = auth_client.get(reverse("team:roster") + "?q=nobody").content.decode()

    assert "No riders match" in body
    assert "Clear the search" in body


@pytest.mark.django_db
def test_an_empty_query_shows_everyone(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")

    for query in ("", "   "):
        body = auth_client.get(reverse("team:roster") + f"?q={query}").content.decode()
        assert "Ada Racer" in body
        # Everything between the start of the results and the first card: the chips and the
        # count live there, and an empty query must claim neither a filter nor a narrowed result.
        # (From the results, not the search box: the box's own help text says "matches".)
        above_the_cards = body.split('id="roster-results"', 1)[1].split('class="card bg-base-100', 1)[0]
        assert "match" not in above_the_cards


@pytest.mark.django_db
def test_the_search_text_is_not_logged(auth_client, roster_rider):
    """Rider-authored free text: looking a teammate up by real name should not land in telemetry."""
    from unittest.mock import patch

    roster_rider(zwid=1001, name="Ada Racer")

    with patch("apps.team.views.logfire.info") as info:
        auth_client.get(reverse("team:roster") + "?q=Ada+Lovelace")

    logged = " ".join(str(call) for call in info.call_args_list)
    assert "Lovelace" not in logged

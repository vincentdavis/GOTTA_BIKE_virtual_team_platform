"""One rule for a rider's gender, everywhere it is shown, filtered or checked (Vincent's call).

The member's own account first, then zauth's profile -- Zwift's own answer, then ZwiftPower's,
then zwiftracing's, merged by zauth -- then, on pages that hold them, our own ZwiftPower row (a
women's category means female) and zwiftracing row, for a rider zauth has not profiled.

What it replaced: the card roster read zauth's answer alone, and that was blank for nearly the
whole team (ZwiftPower's roster sends no gender, and its blank hid zwiftracing's), so 2,112 of
2,122 riders showed as "not recorded". The roster table counted anyone without a women's
category as a man; the Sheets export compared "M"/"F" with the account's "male"/"female", so
only zwiftracing's answer ever counted; the event pages read the account alone.
"""

from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts import views as account_views
from apps.data_connection.services import _get_field_value, _passes_filters
from apps.events.models import Event, EventSignup, Squad
from apps.events.views import _mark_eligibility
from apps.rider_data.models import member_gender, rider_gender
from apps.team.rosterv2 import apply_filters, build_roster_index, parse_filters
from apps.team.services import ZP_DIV_TO_CATEGORY, UnifiedRider, get_unified_team_roster
from conftest import _make_user

# --- The rule -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("genders", "expected"),
    [
        (("female", "M"), "female"),  # the account wins
        (("", "M"), "male"),  # zauth fills a blank account
        (("  ", None, "F"), "female"),  # blank and missing are no answer
        (("other", "F"), "other"),  # an account's "other" is never overruled by Zwift's two choices
        (("W",), "female"),
        (("men",), "male"),
        ((), ""),
        (("", None), ""),
    ],
)
def test_the_first_source_with_an_answer_decides(genders, expected):
    assert rider_gender(*genders) == expected


def _member(user_model, username, *, zwid=None, gender="", verified=True):
    return _make_user(
        user_model,
        username=username,
        permissions={"team_member": True},
        zwid=zwid,
        gender=gender,
        zwid_verified=verified,
        zwid_verification_method="zauth" if verified else "",
    )


@pytest.mark.django_db
def test_a_members_own_answer_costs_no_query(user_model):
    member = _member(user_model, "ada", zwid=4241, gender="female")

    with CaptureQueriesContext(connection) as queries:
        assert member_gender(member) == "female"

    assert len(queries) == 0


@pytest.mark.django_db
def test_zauths_answer_fills_a_blank_account(user_model, rider_profile_factory):
    rider_profile_factory(zwid=4242, gender="female")
    member = _member(user_model, "bea", zwid=4242)

    assert member_gender(member) == "female"
    # An answer the caller already holds is used as it is, "" (no profile) included.
    assert member_gender(member, "male") == "male"
    assert member_gender(member, "") == ""
    member.zwid = None
    assert member_gender(member) == ""


# --- The card roster ----------------------------------------------------------------------


def _roster(**params):
    index = build_roster_index()
    return [row.card.name for row in apply_filters(list(index.rows), parse_filters(params, index.rows))]


@pytest.mark.django_db
def test_the_card_roster_puts_the_account_before_zauth(roster_rider, user_model):
    roster_rider(zwid=2001, name="Says Female", gender="male")
    _member(user_model, "says_female", zwid=2001, gender="female")
    roster_rider(zwid=2002, name="Zauth Only", gender="female")
    roster_rider(zwid=2003, name="Blank Account", gender="male")
    _member(user_model, "blank_account", zwid=2003)
    roster_rider(zwid=2004, name="Says Other", gender="female")
    _member(user_model, "says_other", zwid=2004, gender="other")

    assert _roster(gender="women") == ["Says Female", "Zauth Only"]
    assert _roster(gender="men") == ["Blank Account"]
    assert _roster(gender="unknown") == ["Says Other"]


@pytest.mark.django_db
def test_an_unproven_claim_cannot_change_a_riders_gender(roster_rider, user_model):
    roster_rider(zwid=2101, name="Real Rider", gender="female")
    _member(user_model, "claimer", zwid=2101, gender="male", verified=False)

    assert _roster(gender="women") == ["Real Rider"]


@pytest.mark.django_db
def test_the_card_roster_shows_a_womans_velo2_tier_by_the_same_rule(roster_rider, user_model):
    roster_rider(zwid=2201, name="Her", gender="male", velo2={"category_women": "Ruby"})
    _member(user_model, "her", zwid=2201, gender="female")

    [row] = build_roster_index().rows

    assert row.card.velo2_category_women == "Ruby"


# --- The roster table ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rider", "expected"),
    [
        (UnifiedRider(zwid=1, user_gender="female", profile_gender="male"), "F"),
        (UnifiedRider(zwid=1, in_zwiftpower=True, profile_gender="male", zp_divw=20), "M"),
        (UnifiedRider(zwid=1, in_zwiftpower=True, zp_divw=20, zr_gender="M"), "F"),
        (UnifiedRider(zwid=1, in_zwiftpower=True, zr_gender="M"), "M"),
        # No women's category used to read as male.
        (UnifiedRider(zwid=1, in_zwiftpower=True), ""),
        (UnifiedRider(zwid=1, user_gender="other", profile_gender="female"), ""),
    ],
)
def test_the_roster_table_follows_the_rule(rider, expected):
    assert rider.gender == expected


@pytest.mark.django_db
def test_the_roster_table_reads_zauths_answer(rider_profile_factory, zp_team_rider_factory):
    zp_team_rider_factory(zwid=2301, divw=0)
    rider_profile_factory(zwid=2301, gender="female")

    [rider] = [rider for rider in get_unified_team_roster() if rider.zwid == 2301]

    assert rider.gender == "F"


# --- The Sheets export --------------------------------------------------------------------


def test_the_sheets_gender_filter_reads_the_account():
    member = {"zwid": 1, "gender": "female"}

    assert _passes_filters({"gender": "F"}, member, None, {"gender": "M"})
    assert not _passes_filters({"gender": "M"}, member, None, {"gender": "M"})


def test_the_sheets_gender_filter_falls_back_in_order():
    assert _passes_filters({"gender": "F"}, {"gender": ""}, None, {"gender": "M"}, "female")
    assert _passes_filters({"gender": "F"}, None, {"divw": 20}, {"gender": "M"})
    assert _passes_filters({"gender": "M"}, None, {"divw": 0}, {"gender": "M"})
    assert not _passes_filters({"gender": "F"}, None, None, None)
    assert _passes_filters({"gender": ""}, None, None, None)


def test_the_sheets_gender_column_holds_every_riders_gender():
    assert _get_field_value("gender", {"gender": "male"}, None, {"gender": "F"}) == "male"
    assert _get_field_value("gender", None, None, {"gender": "F"}) == "female"
    assert _get_field_value("gender", None, None, None, profile_gender="female") == "female"
    assert _get_field_value("gender", None, None, None) == ""


# --- The event pages ----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_squads_gender_check_reads_zauth_when_the_account_is_blank(user_model, rider_profile_factory):
    today = date.today()
    event = Event.objects.create(title="Women's league", start_date=today, end_date=today + timedelta(days=30))
    squad = Squad.objects.create(event=event, name="Women A", gender="Female", enforce_gender=True)
    rider_profile_factory(zwid=3001, gender="female")
    rider_profile_factory(zwid=3002, gender="male")
    riders = (
        _member(user_model, "zauth_says_female", zwid=3001),
        _member(user_model, "zauth_says_male", zwid=3002),
        _member(user_model, "account_says_female", zwid=3002, gender="female"),
        _member(user_model, "nobody_says", zwid=3003),
    )
    signups = [EventSignup.objects.create(event=event, user=rider) for rider in riders]

    _mark_eligibility(squad, signups, {})

    assert [signup.is_eligible for signup in signups] == [True, False, True, False]


# --- The rider's own profile ----------------------------------------------------------------


@pytest.mark.django_db
def test_the_profiles_zwiftpower_category_follows_the_rule(
    user_model, rider_profile_factory, zp_team_rider_factory, monkeypatch
):
    monkeypatch.setattr(account_views.zwift_client, "is_configured", lambda: False)
    zp_team_rider_factory(zwid=4301, div=20, divw=30)
    rider_profile_factory(zwid=4301, gender="female")
    member = _member(user_model, "zauth_knows_her", zwid=4301)

    zp_data = account_views._build_zwift_status_context(member)["zp_data"]

    assert zp_data["category"] == ZP_DIV_TO_CATEGORY[30]
    assert zp_data["category_mixed"] == ZP_DIV_TO_CATEGORY[20]

    member.gender = "male"  # the account wins
    zp_data = account_views._build_zwift_status_context(member)["zp_data"]

    assert zp_data["category"] == ZP_DIV_TO_CATEGORY[20]
    assert "category_mixed" not in zp_data

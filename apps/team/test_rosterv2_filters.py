"""Narrowing and ordering the roster.

Two of these exist because the roster this replaces gets them wrong today: it buckets every
rider whose gender is blank in with the men, and it orders Zwift Racing tiers alphabetically,
which puts Amethyst above Diamond. Both are silent — the page looks fine and says something
untrue — so both are pinned here rather than left to review.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import GuildMember
from apps.team.rosterv2 import (
    ZR_CATEGORY_ORDER,
    apply_filters,
    build_roster_index,
    filter_options,
    gender_bucket,
    parse_filters,
    sort_rows,
)
from conftest import _make_user


def _rows(**params):
    """Build the index, then narrow it the way the view does.

    Returns:
        The filtered rows.

    """
    index = build_roster_index()
    return apply_filters(list(index.rows), parse_filters(params, index.rows))


def _names(rows):
    return [row.card.name for row in rows]


def _member(user_model, username, zwid, *, joined_days_ago=None, race_ready=False, extra=False):
    user = _make_user(user_model, username=username, permissions={"team_member": True})
    user.zwid = zwid
    user.zwid_verified = True
    user.zwid_verification_method = "zauth"
    user.discord_id = f"90000{zwid}"
    user.discord_username = username
    user.is_race_ready = race_ready
    user.is_extra_verified = extra
    user.save()
    if joined_days_ago is not None:
        GuildMember.objects.create(
            discord_id=user.discord_id,
            username=username,
            user=user,
            joined_at=timezone.now() - timedelta(days=joined_days_ago),
        )
    return user


# --- gender: three states, not two ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "bucket"),
    [
        ("F", "women"), ("f", "women"), ("female", "women"), ("Women", "women"),
        ("M", "men"), ("male", "men"),
        ("", "unknown"), ("   ", "unknown"), ("X", "unknown"), ("other", "unknown"),
    ],
)
def test_gender_has_three_buckets(raw, bucket):
    """Upstream says M/F, zauth has passed male/female; anything else is genuinely unknown."""
    assert gender_bucket(raw) == bucket


@pytest.mark.django_db
def test_a_rider_with_no_recorded_gender_is_not_counted_as_a_man(roster_rider):
    """The live bug on the roster this replaces: four `else:` branches bucket blank as men."""
    roster_rider(zwid=1001, name="Known Woman", gender="F")
    roster_rider(zwid=1002, name="Known Man", gender="M")
    roster_rider(zwid=1003, name="Not Recorded", gender="")

    assert _names(_rows(gender="men")) == ["Known Man"]
    assert _names(_rows(gender="women")) == ["Known Woman"]
    assert _names(_rows(gender="unknown")) == ["Not Recorded"]


# --- the tiers, in the right order --------------------------------------------------------


@pytest.mark.django_db
def test_the_tier_options_run_from_diamond_down_not_alphabetically(roster_rider):
    """Alphabetical ordering is live on the v1 roster and puts the top tier sixth."""
    for zwid, tier in enumerate(("Copper", "Diamond", "Gold", "Amethyst"), start=1001):
        roster_rider(zwid=zwid, name=f"Rider {tier}", category_racing=tier)

    options = filter_options(build_roster_index().rows)["zr"]

    assert options == ["Diamond", "Amethyst", "Gold", "Copper"]
    assert options != sorted(options)


def test_the_tier_order_is_the_one_the_rest_of_the_app_uses():
    assert ZR_CATEGORY_ORDER[0] == "Diamond"
    assert ZR_CATEGORY_ORDER[-1] == "Copper"
    assert len(ZR_CATEGORY_ORDER) == 10


# --- the filters --------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_category_filter_matches_the_open_or_the_womens_category(roster_rider):
    roster_rider(zwid=1001, name="Open B", category_open="B", category_women="")
    roster_rider(zwid=1002, name="Womens B", category_open="C", category_women="B")
    roster_rider(zwid=1003, name="Neither", category_open="D", category_women="")

    assert sorted(_names(_rows(category="B"))) == ["Open B", "Womens B"]


@pytest.mark.django_db
def test_filters_narrow_by_tier_phenotype_and_age(roster_rider):
    roster_rider(zwid=1001, name="Target", category_racing="Gold", phenotype="Sprinter", age="Vet")
    roster_rider(zwid=1002, name="Other", category_racing="Copper", phenotype="Climber", age="Snr")

    assert _names(_rows(zr="Gold")) == ["Target"]
    assert _names(_rows(phenotype="Sprinter")) == ["Target"]
    assert _names(_rows(age="Vet")) == ["Target"]


@pytest.mark.django_db
def test_a_hidden_age_bracket_is_not_offered_as_a_filter(roster_rider):
    """The index blanks "-", so it must not appear as something to filter on either."""
    roster_rider(zwid=1001, name="No Bracket", age="-")

    assert filter_options(build_roster_index().rows)["ages"] == []


@pytest.mark.django_db
def test_race_verified_filters_use_the_account_not_the_card(roster_rider, user_model):
    roster_rider(zwid=4242, name="Verified Rider")
    _member(user_model, "verified", 4242, race_ready=True)
    roster_rider(zwid=4243, name="Extra Rider")
    _member(user_model, "extra", 4243, race_ready=True, extra=True)
    roster_rider(zwid=4244, name="Plain Rider")

    assert sorted(_names(_rows(verified="verified"))) == ["Extra Rider", "Verified Rider"]
    assert _names(_rows(verified="extra")) == ["Extra Rider"]


@pytest.mark.django_db
def test_the_account_filter_separates_members_from_zwiftpower_only_riders(roster_rider, user_model):
    roster_rider(zwid=4242, name="Member Rider")
    _member(user_model, "member", 4242)
    roster_rider(zwid=4243, name="Scouted Rider")

    assert _names(_rows(account="yes")) == ["Member Rider"]
    assert _names(_rows(account="no")) == ["Scouted Rider"]


@pytest.mark.django_db
def test_a_power_minimum_never_sweeps_in_a_rider_we_have_no_figure_for(roster_rider):
    """"At least 3.5" must not quietly include riders whose W/kg we do not hold."""
    roster_rider(zwid=1001, name="Strong", wkg_20min=4.2, ftp=320.0)
    roster_rider(zwid=1002, name="Steady", wkg_20min=3.0, ftp=240.0)
    roster_rider(zwid=1003, name="Unknown Power", wkg_20min=None, ftp=None)

    assert _names(_rows(wkg="3.5")) == ["Strong"]
    assert _names(_rows(ftp="300")) == ["Strong"]


@pytest.mark.django_db
def test_the_joined_filter_uses_the_current_discord_stint(roster_rider, user_model):
    roster_rider(zwid=4242, name="New Member")
    _member(user_model, "new", 4242, joined_days_ago=10)
    roster_rider(zwid=4243, name="Old Member")
    _member(user_model, "old", 4243, joined_days_ago=400)
    roster_rider(zwid=4244, name="No Account")

    assert _names(_rows(joined="30")) == ["New Member"]


@pytest.mark.django_db
def test_an_unknown_filter_value_narrows_nothing(roster_rider):
    """A hand-edited URL should show everyone, not an empty page nobody can explain."""
    roster_rider(zwid=1001, name="Ada Racer", category_racing="Gold")

    assert _names(_rows(zr="Unobtanium")) == ["Ada Racer"]
    assert _names(_rows(gender="banana")) == ["Ada Racer"]
    assert _names(_rows(wkg="0.1")) == ["Ada Racer"]


@pytest.mark.django_db
def test_filters_combine(roster_rider):
    roster_rider(zwid=1001, name="Both", category_racing="Gold", phenotype="Sprinter")
    roster_rider(zwid=1002, name="Tier Only", category_racing="Gold", phenotype="Climber")

    assert _names(_rows(zr="Gold", phenotype="Sprinter")) == ["Both"]


# --- sorting ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_riders_with_no_figure_sort_last_in_both_directions(roster_rider):
    """Descending by vELO must not open with every rider we know nothing about."""
    roster_rider(zwid=1001, name="High", velo=1800.0)
    roster_rider(zwid=1002, name="Low", velo=1200.0)
    roster_rider(zwid=1003, name="Unrated", velo=None)

    rows = list(build_roster_index().rows)

    assert _names(sort_rows(rows, "velo", "desc")) == ["High", "Low", "Unrated"]
    assert _names(sort_rows(rows, "velo", "asc")) == ["Low", "High", "Unrated"]


@pytest.mark.django_db
def test_each_sort_orders_by_its_own_figure(roster_rider):
    roster_rider(zwid=1001, name="Alpha", velo=1200.0, ftp=300.0, wkg_20min=3.0)
    roster_rider(zwid=1002, name="Beta", velo=1800.0, ftp=200.0, wkg_20min=4.5)

    rows = list(build_roster_index().rows)

    assert _names(sort_rows(rows, "velo", "desc")) == ["Beta", "Alpha"]
    assert _names(sort_rows(rows, "ftp", "desc")) == ["Alpha", "Beta"]
    assert _names(sort_rows(rows, "wkg", "desc")) == ["Beta", "Alpha"]


@pytest.mark.django_db
def test_newest_and_longest_serving_are_opposite_ends_of_the_same_figure(roster_rider, user_model):
    roster_rider(zwid=4242, name="Veteran")
    _member(user_model, "veteran", 4242, joined_days_ago=900)
    roster_rider(zwid=4243, name="Rookie")
    _member(user_model, "rookie", 4243, joined_days_ago=5)

    rows = list(build_roster_index().rows)

    assert _names(sort_rows(rows, "newest", "desc")) == ["Rookie", "Veteran"]
    assert _names(sort_rows(rows, "longest", "asc")) == ["Veteran", "Rookie"]


@pytest.mark.django_db
def test_an_unknown_sort_falls_back_to_name_order(roster_rider):
    roster_rider(zwid=1001, name="Zoe")
    roster_rider(zwid=1002, name="Ada")

    assert _names(sort_rows(list(build_roster_index().rows), "zwid", "")) == ["Ada", "Zoe"]


@pytest.mark.django_db
def test_there_is_no_way_to_sort_by_zwid(roster_rider):
    """v1 exposes sort=zwid to anyone who types it, which orders the page by a hidden id."""
    from apps.team.rosterv2 import SORTS

    assert "zwid" not in SORTS
    assert not [key for key in SORTS if "zwid" in key]


# --- the page -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_active_filter_shows_a_chip_that_removes_it(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Gold Rider", category_racing="Gold")
    roster_rider(zwid=1002, name="Copper Rider", category_racing="Copper")

    body = auth_client.get(reverse("team:roster") + "?zr=Gold").content.decode()

    assert "Zwift Racing: Gold" in body
    assert "Copper Rider" not in body
    assert "Clear all" in body
    # zr is the only control set, so removing it leaves an empty querystring. Asserting the
    # exact href is what makes this test able to fail: an OR of three near-misses could not.
    assert 'href="?"' in body
    assert 'aria-label="Remove the Zwift Racing filter, Gold"' in body


@pytest.mark.django_db
def test_filtering_and_paging_keep_each_other(auth_client, roster_rider):
    for n in range(55):
        roster_rider(zwid=6000 + n, name=f"Gold {n:03d}", category_racing="Gold")
    roster_rider(zwid=7000, name="Copper One", category_racing="Copper")

    first = auth_client.get(reverse("team:roster") + "?zr=Gold").content.decode()
    second = auth_client.get(reverse("team:roster") + "?zr=Gold&page=2").content.decode()

    assert "Showing 55 of 56 riders" in first
    assert "zr=Gold" in first
    assert "Copper One" not in second
    assert second.count('class="card bg-base-100') == 7


@pytest.mark.django_db
def test_the_filter_panel_opens_itself_when_a_filter_is_on(auth_client, roster_rider):
    """Otherwise the reason the roster looks short is hidden behind a closed summary."""
    roster_rider(zwid=1001, name="Ada Racer", category_racing="Gold")

    closed = auth_client.get(reverse("team:roster")).content.decode()
    opened = auth_client.get(reverse("team:roster") + "?zr=Gold").content.decode()

    assert '" open>' not in closed
    assert '" open>' in opened


@pytest.mark.django_db
def test_every_control_has_a_real_label(auth_client, roster_rider):
    """Both control strips this could have been copied from ship unlabelled inputs."""
    roster_rider(zwid=1001, name="Ada Racer")

    body = auth_client.get(reverse("team:roster")).content.decode()

    for control in ("f-category", "f-zr", "f-gender", "f-phenotype", "f-age", "f-verified",
                    "f-account", "f-wkg", "f-ftp", "f-joined", "f-racing", "f-country",
                    "f-sort", "f-dir", "roster-search"):
        assert f'for="{control}"' in body, f"{control} has no label"
        assert f'id="{control}"' in body, f"{control} is not there"


# --- country ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_riders_can_be_narrowed_to_one_country(roster_rider):
    roster_rider(zwid=1001, name="American", country="us")
    roster_rider(zwid=1002, name="French", country="fr")

    assert _names(_rows(country="US")) == ["American"]
    assert _names(_rows(country="FR")) == ["French"]


@pytest.mark.django_db
def test_the_uk_nations_are_found_under_the_flag_they_fly(roster_rider):
    """The card shows the Union Flag for a Welsh rider, so "United Kingdom" must find them.

    Filtering on the raw code would strand four nations' riders in options nobody thought to
    offer, while their cards visibly fly a flag the filter denies.
    """
    roster_rider(zwid=1001, name="Welsh", country="gb-wls")
    roster_rider(zwid=1002, name="Scottish", country="gb-sct")
    roster_rider(zwid=1003, name="Plain British", country="gb")
    roster_rider(zwid=1004, name="French", country="fr")

    assert sorted(_names(_rows(country="GB"))) == ["Plain British", "Scottish", "Welsh"]


@pytest.mark.django_db
def test_the_country_options_are_the_countries_present_named_and_alphabetical(roster_rider):
    roster_rider(zwid=1001, name="A", country="us")
    roster_rider(zwid=1002, name="B", country="fr")
    roster_rider(zwid=1003, name="C", country="gb-wls")
    roster_rider(zwid=1004, name="D", country="gb")

    options = filter_options(build_roster_index().rows)["countries"]

    # Wales and plain GB collapse to one option, labelled by name and sorted by it.
    assert options == [("FR", "France"), ("GB", "United Kingdom"), ("US", "United States of America")]


@pytest.mark.django_db
def test_an_unknown_country_code_is_not_offered_and_narrows_nothing(roster_rider):
    """A code django_countries does not know has no flag either, so it has nothing to filter on."""
    roster_rider(zwid=1001, name="Mystery", country="zzz")
    roster_rider(zwid=1002, name="Known", country="fr")

    assert filter_options(build_roster_index().rows)["countries"] == [("FR", "France")]
    assert sorted(_names(_rows(country="ZZZ"))) == ["Known", "Mystery"]


@pytest.mark.django_db
def test_a_rider_with_no_country_is_excluded_by_a_country_filter(roster_rider):
    roster_rider(zwid=1001, name="Somewhere", country="fr")
    roster_rider(zwid=1002, name="Nowhere", country="")

    assert _names(_rows(country="FR")) == ["Somewhere"]


@pytest.mark.django_db
def test_the_country_chip_names_the_country_rather_than_its_code(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Welsh Rider", country="gb-wls")

    body = auth_client.get(reverse("team:roster") + "?country=GB").content.decode()

    assert "Country: United Kingdom" in body
    assert "Country: GB" not in body

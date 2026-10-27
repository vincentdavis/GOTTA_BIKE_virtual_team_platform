"""A woman's vELO 2 women's category, on the pages that still read the zwiftracing table.

Those pages take each rider's ZR category -- vELO 1's mixed one -- from ``ZRRider``, and put
the women's vELO 2 category beside it from the rider's cached profile, through
``rider_data.models.velo2_women_by_zwid``. zwiftracing sends a women's category for every
rider, so each page decides who is a woman by the gender it already shows.
"""

import pytest

from apps.team.services import get_unified_team_roster
from apps.zwiftpower.views import _enrich_results_by_zwid
from conftest import _make_user

VELO2 = {"race": 430.0, "category": "Amethyst", "category_women": "Emerald"}


def _rider(user_model, rider_profile_factory, username, zwid, gender):
    user = _make_user(user_model, username=username)
    user.zwid = zwid
    user.gender = gender
    user.save()
    rider_profile_factory(zwid=zwid, gender="", velo2=VELO2)
    return user


@pytest.mark.django_db
def test_the_team_roster_gives_its_women_their_womens_category(
    user_model, rider_profile_factory, zp_team_rider_factory
):
    _rider(user_model, rider_profile_factory, "ana", 101, "female")
    _rider(user_model, rider_profile_factory, "bo", 102, "male")
    # No account: the roster calls her a woman from ZwiftPower's women's division.
    zp_team_rider_factory(zwid=103, divw=30)
    rider_profile_factory(zwid=103, gender="", velo2=VELO2)

    by_zwid = {rider.zwid: rider for rider in get_unified_team_roster()}

    assert by_zwid[101].zr_v2_women_category == "Emerald"
    assert by_zwid[102].zr_v2_women_category == ""
    assert by_zwid[103].zr_v2_women_category == "Emerald"


@pytest.mark.django_db
def test_the_results_pages_give_a_woman_her_womens_category(user_model, rider_profile_factory):
    _rider(user_model, rider_profile_factory, "ana", 101, "female")
    _rider(user_model, rider_profile_factory, "bo", 102, "male")

    enriched = _enrich_results_by_zwid([101, 102])

    assert enriched[101]["zr_v2_women_category"] == "Emerald"
    assert enriched[102]["zr_v2_women_category"] == ""

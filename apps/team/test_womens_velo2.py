"""vELO 2's categories on the pages that still read the zwiftracing table.

Those pages take each rider's ZR category -- vELO 1's mixed one -- from ``ZRRider``, and put
vELO 2's mixed category beside it from the rider's cached profile, through
``rider_data.models.velo2_categories_by_zwid``. zwiftracing sends a women's category for
every rider too; the roster table keeps it for the riders it shows as women, for its filter.
"""

import pytest
from django.urls import reverse

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
def test_the_team_roster_gives_everyone_the_mixed_tier_and_its_women_theirs(
    user_model, rider_profile_factory, zp_team_rider_factory
):
    _rider(user_model, rider_profile_factory, "ana", 101, "female")
    _rider(user_model, rider_profile_factory, "bo", 102, "male")
    # No account: the roster calls her a woman from ZwiftPower's women's division.
    zp_team_rider_factory(zwid=103, divw=30)
    rider_profile_factory(zwid=103, gender="", velo2=VELO2)

    by_zwid = {rider.zwid: rider for rider in get_unified_team_roster()}

    assert [by_zwid[z].zr_v2_category for z in (101, 102, 103)] == ["Amethyst"] * 3
    assert [by_zwid[z].zr_v2_women_category for z in (101, 102, 103)] == ["Emerald", "", "Emerald"]


@pytest.mark.django_db
def test_the_roster_table_filters_by_velo2_tiers(client, team_member, user_model, rider_profile_factory):
    for username, zwid, gender in (("ana", 101, "female"), ("bo", 102, "male")):
        user = _rider(user_model, rider_profile_factory, username, zwid, gender)
        user.first_name = username.title()
        user.save()
    client.force_login(team_member)
    url = reverse("team:roster_table")

    mixed = client.get(url, {"velo2": "Amethyst"}).context["roster"]
    women = client.get(url, {"velo2_women": "Emerald"}).context["roster"]

    assert sorted(r.zwid for r in mixed) == [101, 102]
    assert [r.zwid for r in women] == [101]


@pytest.mark.django_db
def test_the_results_pages_give_everyone_the_mixed_tier(user_model, rider_profile_factory):
    _rider(user_model, rider_profile_factory, "ana", 101, "female")
    _rider(user_model, rider_profile_factory, "bo", 102, "male")

    enriched = _enrich_results_by_zwid([101, 102])

    assert (enriched[101]["zr_v2_category"], enriched[102]["zr_v2_category"]) == ("Amethyst", "Amethyst")

"""ZwiftRacing ratings on the profile: three tiers, and the best ever recorded.

The API reports current / 30-day / 90-day, and all three go null once a rider stops
racing -- so someone who was strong a year ago looked identical to someone the API has
never heard of. The history table is the only record that they had a rating at all.
"""

from decimal import Decimal

import pytest

from apps.accounts.views import zr_rating_tiers
from apps.zwiftracing.models import ZRRider
from apps.zwiftracing.tasks import _map_rider_to_model

# The exact payload for a rider who has not raced in 90+ days.
LAPSED = {
    "riderId": 6960102, "name": "Julius Ad (COALITION)", "gender": "M", "country": "de",
    "age": "Mas", "height": 191, "weight": 90, "zpCategory": "B", "zpFTP": 355,
    "power": None, "race": None,
    "handicaps": {"profile": {"flat": 76.5, "rolling": 50.6, "hilly": -10.7, "mountainous": -93.2}},
    "phenotype": None, "club": {"id": 11991, "name": "COALITION"},
    "seed": None, "velo": None, "zrs": None, "weightChangedAt": 1758043980,
}

ACTIVE = {
    **LAPSED,
    "race": {
        "current": {"rating": 1517.47, "date": 1787401800, "mixed": {"category": "Sapphire", "number": 4}},
        "max30": {"rating": 1529.69, "date": 1785886200, "expires": 1789993800,
                  "mixed": {"category": "Sapphire", "number": 4}},
        "max90": {"rating": 1559.60, "date": 1781911800, "expires": 1795177800,
                  "mixed": {"category": "Sapphire", "number": 4}},
    },
}


@pytest.mark.django_db
def test_all_three_tiers_are_offered(db) -> None:
    """Current is form today; the two maxima are what a rider is seeded against."""
    rider = ZRRider.objects.create(zwid=1, **_map_rider_to_model(ACTIVE))

    tiers = zr_rating_tiers(rider)

    assert [t["label"] for t in tiers] == ["Current", "30-day max", "90-day max"]
    assert [float(t["rating"]) for t in tiers] == [1517.47, 1529.69, 1559.60]
    assert {t["category"] for t in tiers} == {"Sapphire"}


@pytest.mark.django_db
def test_a_lapsed_rider_keeps_the_rows_but_shows_no_ratings(db) -> None:
    """Hiding empty rows made a lapsed rider look like one the API never heard of."""
    rider = ZRRider.objects.create(zwid=2, **_map_rider_to_model(LAPSED))

    tiers = zr_rating_tiers(rider)

    assert len(tiers) == 3
    assert all(t["rating"] is None for t in tiers)


@pytest.mark.django_db
def test_the_cache_is_nulled_when_the_api_stops_reporting(db) -> None:
    """Confirms the sync overwrites rather than leaving stale ratings behind."""
    rider = ZRRider.objects.create(zwid=3, **_map_rider_to_model(ACTIVE))
    assert rider.race_current_rating is not None

    for field, value in _map_rider_to_model(LAPSED).items():
        setattr(rider, field, value)
    rider.save()

    rider.refresh_from_db()
    assert rider.race_current_rating is None
    assert rider.race_max90_rating is None
    assert rider.race_current_category == ""


@pytest.mark.django_db
def test_the_best_rating_survives_in_history(db) -> None:
    """The point of the exercise: a lapsed rider's peak is still recoverable."""
    rider = ZRRider.objects.create(zwid=4, **_map_rider_to_model(ACTIVE))
    for field, value in _map_rider_to_model(LAPSED).items():
        setattr(rider, field, value)
    rider.save()

    best = rider.best_rating_seen()

    assert best is not None
    # max90 was the highest figure ever reported, above any current rating.
    assert best["rating"] == Decimal("1559.60")
    assert best["category"] == "Sapphire"
    assert best["source"] == "max90"


@pytest.mark.django_db
def test_a_rider_who_never_had_a_rating_has_no_best(db) -> None:
    """Absence must read as absence, not as a zero."""
    rider = ZRRider.objects.create(zwid=5, **_map_rider_to_model(LAPSED))

    assert rider.best_rating_seen() is None


@pytest.mark.django_db
def test_the_highest_current_wins_when_it_beats_max90(db) -> None:
    """Both fields are considered: syncs can miss the window a 90-day max covers."""
    payload = {**ACTIVE, "race": {
        "current": {"rating": 1700.0, "date": 1, "mixed": {"category": "Ruby", "number": 5}},
        "max30": {"rating": 1200.0, "date": 1, "mixed": {"category": "Gold", "number": 3}},
        "max90": {"rating": 1200.0, "date": 1, "mixed": {"category": "Gold", "number": 3}},
    }}
    rider = ZRRider.objects.create(zwid=6, **_map_rider_to_model(payload))

    best = rider.best_rating_seen()

    assert best["source"] == "current"
    assert best["category"] == "Ruby"

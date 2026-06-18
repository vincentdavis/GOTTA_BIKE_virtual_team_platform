"""Tests for verification_days_bulk: correctness and that it avoids N+1 queries."""

import pytest

from apps.team.services import verification_days_bulk


@pytest.mark.django_db
def test_bulk_matches_records(user_model, verification_factory):
    racer = user_model.objects.create(username="racer", is_race_ready=True)
    empty = user_model.objects.create(username="empty")
    verification_factory(racer, "weight_full")  # verified, record_date today

    result = verification_days_bulk([racer, empty])

    # No records -> everything None/False.
    assert result[empty.id] == {
        "weight_days": None,
        "height_days": None,
        "power_days": None,
        "race_ready_days": None,
        "has_height": False,
    }
    # Weight present, height/power absent.
    assert result[racer.id]["weight_days"] is not None
    assert result[racer.id]["height_days"] is None
    assert result[racer.id]["has_height"] is False
    # Default required types are weight_light + height; only weight is present,
    # so race-ready days is constrained by the weight record.
    assert result[racer.id]["race_ready_days"] == result[racer.id]["weight_days"]


@pytest.mark.django_db
def test_bulk_query_count_does_not_scale_with_users(django_assert_max_num_queries, user_model, verification_factory):
    users = [user_model.objects.create(username=f"u{i}", is_race_ready=True) for i in range(6)]
    for user in users:
        verification_factory(user, "weight_full")
        verification_factory(user, "height")
        verification_factory(user, "power")

    verification_days_bulk(users)  # warm Constance cache so its reads aren't counted below
    # Batched cost is a small constant (one records query + a fixed set of Constance
    # reads), independent of user count. The old per-user path issued ~3-4 queries per
    # user (would be ~20+ here); 6 users stay well under this constant bound.
    with django_assert_max_num_queries(10):
        verification_days_bulk(users)

"""Tests for the inline rider-row edits on a TTT plan (``ttt_planner:rider_update``).

The row posts each field independently, so the endpoint has to tell "this request
is about zero_pull" apart from "this request is about the pull duration" — an
unchecked checkbox submits nothing at all.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.ttt_planner.models import PlanRider, TttPlan


@pytest.fixture
def plan_rider(db, team_member):
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    rider = PlanRider.objects.create(
        plan=plan, order=0, name="Recovery", weight_kg=75, height_cm=180, ftp_w=250, pull_duration_s=30
    )
    return plan, rider


def _url(plan, rider):
    return reverse("ttt_planner:rider_update", args=[plan.pk, rider.pk])


@pytest.mark.django_db
def test_zero_pull_can_be_switched_on(auth_client, plan_rider):
    plan, rider = plan_rider

    auth_client.post(_url(plan, rider), {"zero_pull_submitted": "1", "zero_pull": "on"})

    rider.refresh_from_db()
    assert rider.zero_pull is True


@pytest.mark.django_db
def test_zero_pull_can_be_switched_back_off(auth_client, plan_rider):
    """The regression: an unchecked box posts no value, so it must still clear."""
    plan, rider = plan_rider
    rider.zero_pull = True
    rider.save(update_fields=["zero_pull"])

    auth_client.post(_url(plan, rider), {"zero_pull_submitted": "1"})

    rider.refresh_from_db()
    assert rider.zero_pull is False


@pytest.mark.django_db
def test_updating_pull_duration_leaves_zero_pull_alone(auth_client, plan_rider):
    """The duration input posts on its own and must not touch the recovery flag."""
    plan, rider = plan_rider
    rider.zero_pull = True
    rider.save(update_fields=["zero_pull"])

    auth_client.post(_url(plan, rider), {"pull_duration_s": "60"})

    rider.refresh_from_db()
    assert rider.pull_duration_s == 60
    assert rider.zero_pull is True


@pytest.mark.django_db
def test_updating_pull_power_leaves_zero_pull_alone(auth_client, plan_rider):
    plan, rider = plan_rider
    rider.zero_pull = True
    rider.save(update_fields=["zero_pull"])

    auth_client.post(_url(plan, rider), {"pull_power_w": "275"})

    rider.refresh_from_db()
    assert rider.pull_power_w == 275
    assert rider.zero_pull is True


@pytest.mark.django_db
def test_a_bare_zero_pull_post_still_sets_it(auth_client, plan_rider):
    """Back-compat: a caller sending only zero_pull=on (no marker) still works."""
    plan, rider = plan_rider

    auth_client.post(_url(plan, rider), {"zero_pull": "on"})

    rider.refresh_from_db()
    assert rider.zero_pull is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("-50", 0), ("1501", 1500), ("99999", 1500), ("275", 275)],
)
@pytest.mark.django_db
def test_pull_power_is_capped_at_the_max(auth_client, plan_rider, raw, expected):
    """1500 W is the product cap, and the clamp also keeps the column safe.

    A negative raises IntegrityError everywhere; a value above smallint only fails on
    PostgreSQL, so unclamped it would pass locally and 500 in production.
    """
    plan, rider = plan_rider

    resp = auth_client.post(_url(plan, rider), {"pull_power_w": raw})

    rider.refresh_from_db()
    assert resp.status_code == 200
    assert rider.pull_power_w == expected


@pytest.mark.django_db
def test_model_validators_reject_out_of_range_values(plan_rider):
    """Pull W is set through the admin inline, which validates instead of clamping."""
    _plan, rider = plan_rider
    rider.pull_power_w = PlanRider.MAX_PULL_POWER_W + 1
    rider.pull_duration_s = PlanRider.MAX_PULL_DURATION_S + 1

    with pytest.raises(ValidationError) as exc:
        rider.full_clean()

    assert "pull_power_w" in exc.value.error_dict
    assert "pull_duration_s" in exc.value.error_dict


@pytest.mark.django_db
def test_model_validators_accept_the_boundary_values(plan_rider):
    _plan, rider = plan_rider
    rider.pull_power_w = PlanRider.MAX_PULL_POWER_W
    rider.pull_duration_s = PlanRider.MAX_PULL_DURATION_S

    rider.full_clean()  # must not raise


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("301", 300), ("99999999999", 300), ("300", 300), ("45", 45)],
)
@pytest.mark.django_db
def test_pull_duration_is_capped_at_the_max(auth_client, plan_rider, raw, expected):
    """300s is the product cap; auto-balance never generates more than 180s anyway."""
    plan, rider = plan_rider

    resp = auth_client.post(_url(plan, rider), {"pull_duration_s": raw})

    rider.refresh_from_db()
    assert resp.status_code == 200
    assert rider.pull_duration_s == expected
    assert expected <= PlanRider.MAX_PULL_DURATION_S


@pytest.mark.django_db
def test_the_duration_input_advertises_the_cap(auth_client, plan_rider):
    """The input's max comes from the model constant, so markup and server can't drift.

    Django renders an unresolvable attribute as an empty string, so this also catches
    the constant silently not being reachable from the template.
    """
    plan, rider = plan_rider

    resp = auth_client.post(_url(plan, rider), {"pull_duration_s": "45"})

    assert f'max="{PlanRider.MAX_PULL_DURATION_S}"'.encode() in resp.content


@pytest.mark.django_db
def test_negative_pull_duration_is_clamped_to_zero(auth_client, plan_rider):
    plan, rider = plan_rider

    auth_client.post(_url(plan, rider), {"pull_duration_s": "-30"})

    rider.refresh_from_db()
    assert rider.pull_duration_s == 0


@pytest.mark.django_db
def test_a_non_owner_cannot_edit_the_row(client, plan_rider, user_model):
    plan, rider = plan_rider
    other = user_model.objects.create_user(username="intruder", permission_overrides={"team_member": True})
    client.force_login(other)

    resp = client.post(_url(plan, rider), {"zero_pull_submitted": "1", "zero_pull": "on"})

    rider.refresh_from_db()
    assert resp.status_code == 403
    assert rider.zero_pull is False

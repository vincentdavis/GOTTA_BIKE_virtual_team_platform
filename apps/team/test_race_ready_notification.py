"""Reviewing a verification record announces what actually happened to Race Verified status.

``User.refresh_race_ready()`` returns ``(is_race_ready, is_extra_verified)``. The review
branches used to compare that pair with a bool: never equal, so every review counted as a
change, and the truthy pair reached ``notify_race_ready_change`` as "now race ready" -- so a
rejection added the Discord race-ready role and announced a gain. These pin the real outcomes.
"""

from unittest import mock

import pytest
from django.urls import reverse

from apps.team.models import RaceReadyRecord

# The default CATEGORY_REQUIREMENTS for a rider with no ZwiftPower category.
REQUIRED = ("weight_light", "height")


@pytest.fixture
def reviewer(db, user_model):
    """Build a reviewer who may review records and change a reviewed record's status.

    Returns:
        The reviewer.

    """
    return user_model.objects.create_user(
        username="reviewer",
        email="reviewer@example.test",
        gender="female",
        # performance_verification_team is what allows changing an already-reviewed record.
        permission_overrides={
            "approve_verification": True,
            "performance_verification_team": True,
            "team_member": True,
        },
    )


@pytest.fixture
def rider(db, user_model):
    """Build a rider with no Discord link, so reviewing sends no DM.

    Returns:
        The rider.

    """
    return user_model.objects.create_user(username="rider", email="rider@example.test", gender="female")


def _review(client, reviewer, record, action: str):
    """Post a review action, with the notification tasks captured.

    Args:
        client: Test client.
        reviewer: The signed-in reviewer.
        record: The record being reviewed.
        action: "verify", "reject" or "reset_pending".

    Returns:
        The mock standing in for ``notify_race_ready_change``.

    """
    client.force_login(reviewer)
    with (
        mock.patch("apps.team.views.notify_race_ready_change") as notify,
        mock.patch("apps.team.views.notify_captains_verification"),
    ):
        client.post(reverse("team:verification_record_detail", args=[record.pk]), {"action": action})
    return notify


def _announced(notify) -> list[bool]:
    """Read the race-ready values the view announced.

    Args:
        notify: The mock standing in for ``notify_race_ready_change``.

    Returns:
        One entry per announcement.

    """
    return [call.kwargs["is_now_race_ready"] for call in notify.enqueue.call_args_list]


@pytest.mark.django_db
def test_rejecting_the_last_required_record_announces_a_loss(client, reviewer, rider, verification_factory):
    """The bug: this announced a gain and handed out the race-ready role."""
    for verify_type in REQUIRED:
        record = verification_factory(rider, verify_type)
    rider.refresh_race_ready()
    assert rider.is_race_ready is True

    notify = _review(client, reviewer, record, "reject")

    rider.refresh_from_db()
    assert rider.is_race_ready is False
    assert _announced(notify) == [False]


@pytest.mark.django_db
def test_resetting_the_last_required_record_announces_a_loss(client, reviewer, rider, verification_factory):
    """Same for a reset to pending."""
    for verify_type in REQUIRED:
        record = verification_factory(rider, verify_type)
    rider.refresh_race_ready()

    notify = _review(client, reviewer, record, "reset_pending")

    rider.refresh_from_db()
    assert rider.is_race_ready is False
    assert _announced(notify) == [False]


@pytest.mark.django_db
def test_verifying_the_last_required_record_announces_a_gain(client, reviewer, rider, verification_factory):
    """The status really is gained here, so it is announced as one."""
    verification_factory(rider, "weight_light")
    pending = verification_factory(rider, "height", status=RaceReadyRecord.Status.PENDING)
    rider.refresh_race_ready()
    assert rider.is_race_ready is False

    notify = _review(client, reviewer, pending, "verify")

    rider.refresh_from_db()
    assert rider.is_race_ready is True
    assert _announced(notify) == [True]


@pytest.mark.django_db
def test_a_review_that_changes_nothing_announces_nothing(client, reviewer, rider, verification_factory):
    """A rider who was and stays Race Verified gets no role change and no message."""
    for verify_type in REQUIRED:
        verification_factory(rider, verify_type)
    extra = verification_factory(rider, "power", status=RaceReadyRecord.Status.PENDING)
    rider.refresh_race_ready()
    assert rider.is_race_ready is True

    notify = _review(client, reviewer, extra, "verify")

    rider.refresh_from_db()
    assert rider.is_race_ready is True
    assert _announced(notify) == []

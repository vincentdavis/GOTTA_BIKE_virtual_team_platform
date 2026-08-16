"""Tests for the RecordView audit trail on verification records."""

import pytest
from django.urls import reverse

from apps.team.models import RaceReadyRecord, RecordView


@pytest.fixture
def approver(user_model):
    """Build a reviewer who can open verification records but is not on the PVT.

    Returns:
        A user with team_member + approve_verification.

    """
    return user_model.objects.create_user(
        username="approver",
        email="approver@example.test",
        first_name="Ada",
        last_name="Approver",
        permission_overrides={"team_member": True, "approve_verification": True},
    )


@pytest.fixture
def pvt_approver(user_model):
    """Build a reviewer on the performance verification team.

    Returns:
        A user who can see media even on reviewed records.

    """
    return user_model.objects.create_user(
        username="pvt",
        email="pvt@example.test",
        first_name="Pat",
        last_name="Verifier",
        permission_overrides={
            "team_member": True,
            "approve_verification": True,
            "performance_verification_team": True,
        },
    )


@pytest.mark.django_db
def test_opening_a_record_logs_a_view(client, approver, user, verification_factory) -> None:
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    client.force_login(approver)

    resp = client.get(reverse("team:verification_record_detail", args=[record.pk]))

    assert resp.status_code == 200
    view = RecordView.objects.get(record=record, user=approver)
    assert view.view_count == 1
    assert view.first_viewed_at is not None
    assert view.last_viewed_at is not None


@pytest.mark.django_db
def test_repeat_views_increment_the_same_row(client, approver, user, verification_factory) -> None:
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    client.force_login(approver)
    url = reverse("team:verification_record_detail", args=[record.pk])

    client.get(url)
    client.get(url)
    client.get(url)

    assert RecordView.objects.filter(record=record, user=approver).count() == 1
    assert RecordView.objects.get(record=record, user=approver).view_count == 3


@pytest.mark.django_db
def test_media_view_counted_when_media_is_shown(client, approver, user, verification_factory) -> None:
    """A pending record with evidence renders its media to any approver."""
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    client.force_login(approver)

    client.get(reverse("team:verification_record_detail", args=[record.pk]))

    view = RecordView.objects.get(record=record, user=approver)
    assert view.view_count == 1
    assert view.media_view_count == 1


@pytest.mark.django_db
def test_media_view_not_counted_when_media_is_hidden(client, approver, user, verification_factory) -> None:
    """A verified record hides media from non-PVT reviewers, so only the page view counts."""
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.VERIFIED, height=175)
    client.force_login(approver)

    client.get(reverse("team:verification_record_detail", args=[record.pk]))

    view = RecordView.objects.get(record=record, user=approver)
    assert view.view_count == 1
    assert view.media_view_count == 0


@pytest.mark.django_db
def test_pvt_sees_media_on_reviewed_record(client, pvt_approver, user, verification_factory) -> None:
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.VERIFIED, height=175)
    client.force_login(pvt_approver)

    client.get(reverse("team:verification_record_detail", args=[record.pk]))

    view = RecordView.objects.get(record=record, user=pvt_approver)
    assert view.media_view_count == 1


@pytest.mark.django_db
def test_record_without_media_does_not_count_a_media_view(client, approver, user, verification_factory) -> None:
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175, url="")
    client.force_login(approver)

    client.get(reverse("team:verification_record_detail", args=[record.pk]))

    view = RecordView.objects.get(record=record, user=approver)
    assert view.view_count == 1
    assert view.media_view_count == 0


@pytest.mark.django_db
def test_each_viewer_gets_their_own_row(client, approver, pvt_approver, user, verification_factory) -> None:
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    url = reverse("team:verification_record_detail", args=[record.pk])

    client.force_login(approver)
    client.get(url)
    client.force_login(pvt_approver)
    client.get(url)

    assert RecordView.objects.filter(record=record).count() == 2


@pytest.mark.django_db
def test_viewers_are_listed_on_the_page(client, approver, pvt_approver, user, verification_factory) -> None:
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    url = reverse("team:verification_record_detail", args=[record.pk])

    client.force_login(approver)
    client.get(url)
    client.force_login(pvt_approver)
    resp = client.get(url)

    body = resp.content.decode()
    assert "Viewed By" in body
    assert "Ada Approver" in body  # the earlier viewer is listed for the current reviewer
    assert list(resp.context["record_views"])


@pytest.mark.django_db
def test_post_that_matches_no_action_branch_is_still_logged(client, approver, user, verification_factory) -> None:
    """A POST falling through every handler still renders the page, so it must be audited.

    Regression: gating the audit write on ``request.method == "GET"`` let a reviewer POST
    (e.g. a stale Verify click on an already-reviewed record) and read the page untracked.
    """
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.VERIFIED, height=175)
    client.force_login(approver)  # non-PVT: can_change_status False, record not pending

    resp = client.post(reverse("team:verification_record_detail", args=[record.pk]), {"action": "verify"})

    assert resp.status_code == 200  # fell through to the render, no redirect
    assert RecordView.objects.get(record=record, user=approver).view_count == 1


@pytest.mark.django_db
def test_denied_viewer_is_not_logged(client, team_member, user, verification_factory) -> None:
    """Someone without approve_verification is redirected and leaves no audit row."""
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    client.force_login(team_member)

    client.get(reverse("team:verification_record_detail", args=[record.pk]))

    assert RecordView.objects.filter(record=record).count() == 0

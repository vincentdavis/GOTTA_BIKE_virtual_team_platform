"""Tests for the performance-verification-team power-submission notification task."""

from unittest.mock import patch

import pytest

from apps.team.models import RaceReadyRecord
from apps.team.tasks import notify_pvt_power_submission


@pytest.fixture
def submitter(user_model):
    return user_model.objects.create_user(
        username="submitter",
        discord_id="1000",
        discord_username="submitter",
    )


def _make_pvt(user_model, username, discord_id):
    return user_model.objects.create_user(
        username=username,
        discord_id=discord_id,
        discord_username=username,
        permission_overrides={"performance_verification_team": True},
    )


def _power_record(user):
    return RaceReadyRecord.objects.create(
        user=user,
        verify_type="power",
        media_type="link",
        url="https://example.com/evidence",
        ftp=250,
    )


@pytest.mark.django_db
def test_notifies_pvt_members_excluding_submitter(user_model, submitter):
    pvt_a = _make_pvt(user_model, "pvt_a", "2001")
    pvt_b = _make_pvt(user_model, "pvt_b", "2002")
    # A PVT member who is also the submitter must not be DMed about their own record
    submitter.permission_overrides = {"performance_verification_team": True}
    submitter.save(update_fields=["permission_overrides"])

    record = _power_record(submitter)

    with patch("apps.team.tasks.send_discord_dm", return_value=True) as mock_dm:
        result = notify_pvt_power_submission.func(record_id=record.id)

    dmed_ids = {call.args[0] for call in mock_dm.call_args_list}
    assert dmed_ids == {pvt_a.discord_id, pvt_b.discord_id}
    assert submitter.discord_id not in dmed_ids
    assert result["status"] == "complete"
    assert result["notified"] == 2


@pytest.mark.django_db
def test_skips_non_power_records(user_model, submitter):
    _make_pvt(user_model, "pvt_a", "2001")
    record = RaceReadyRecord.objects.create(
        user=submitter,
        verify_type="height",
        media_type="link",
        url="https://example.com/evidence",
        height=180,
    )

    with patch("apps.team.tasks.send_discord_dm", return_value=True) as mock_dm:
        result = notify_pvt_power_submission.func(record_id=record.id)

    mock_dm.assert_not_called()
    assert result["status"] == "skipped"


@pytest.mark.django_db
def test_no_recipients_returns_gracefully(user_model, submitter):
    record = _power_record(submitter)

    with patch("apps.team.tasks.send_discord_dm", return_value=True) as mock_dm:
        result = notify_pvt_power_submission.func(record_id=record.id)

    mock_dm.assert_not_called()
    assert result["status"] == "no_recipients"

"""Tests for warn_expiring_verifications — per-verify_type reconciliation of expiry DMs."""

from unittest.mock import patch

import pytest

from apps.team.tasks import warn_expiring_verifications


def _discord_user(user, discord_id="555001"):
    user.discord_id = discord_id
    if not user.first_name:
        user.first_name = "Test"
    user.save(update_fields=["discord_id", "first_name"])
    return user


@pytest.mark.django_db
def test_no_dm_when_renewed_same_type_still_covers(verification_factory, user):
    """A weight record renewed before expiry must not trigger an 'expiring' DM (reported bug)."""
    _discord_user(user)
    verification_factory(user, "weight_full", days_ago=119)  # old: 1 day left
    verification_factory(user, "weight_full", days_ago=61)  # renewed: 59 days left

    with patch("apps.team.tasks.send_discord_dm", return_value=True) as dm, patch("apps.team.tasks.time.sleep"):
        result = warn_expiring_verifications.func(days=[1])

    assert dm.call_count == 0
    assert result["warnings_sent"] == 0


@pytest.mark.django_db
def test_dm_sent_for_genuinely_expiring_type(verification_factory, user):
    """A type with no renewal, expiring at the threshold, still gets a DM."""
    _discord_user(user)
    verification_factory(user, "weight_full", days_ago=119)  # 1 day left, no renewal

    with patch("apps.team.tasks.send_discord_dm", return_value=True) as dm, patch("apps.team.tasks.time.sleep"):
        result = warn_expiring_verifications.func(days=[1])

    assert dm.call_count == 1
    assert result["warnings_sent"] == 1
    message = dm.call_args.args[1]
    assert "Weight Full" in message
    assert "expires in **1 days**" in message


@pytest.mark.django_db
def test_other_verifications_lists_other_types_only(verification_factory, user):
    """When a DM fires, 'other verifications' shows other types — never a same-type duplicate."""
    _discord_user(user)
    verification_factory(user, "weight_full", days_ago=119)  # 1 day left -> warns
    verification_factory(user, "power", days_ago=100)  # 265 days left -> other type, safe

    with patch("apps.team.tasks.send_discord_dm", return_value=True) as dm, patch("apps.team.tasks.time.sleep"):
        warn_expiring_verifications.func(days=[1])

    assert dm.call_count == 1
    message = dm.call_args.args[1]
    assert "Your other verifications:" in message
    assert "Power" in message
    # The expiring type appears once (the headline line), never duplicated under "others".
    assert message.count("Weight Full") == 1


@pytest.mark.django_db
def test_superseded_record_days_do_not_trigger_even_at_threshold(verification_factory, user):
    """Even if the OLD record sits exactly on a threshold day, the covering renewal suppresses it."""
    _discord_user(user)
    verification_factory(user, "weight_full", days_ago=117)  # old: 3 days left (a threshold)
    verification_factory(user, "weight_full", days_ago=20)  # renewed: 100 days left

    with patch("apps.team.tasks.send_discord_dm", return_value=True) as dm, patch("apps.team.tasks.time.sleep"):
        result = warn_expiring_verifications.func(days=[15, 7, 3, 1])

    assert dm.call_count == 0
    assert result["warnings_sent"] == 0

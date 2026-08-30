"""Members can turn off Discord DMs, and the opt-out is enforced in one place.

Art. 21 gives a right to object; before this there was no way to stop the bot messaging you
short of deleting your account or blocking the bot. The gate lives inside send_discord_dm
rather than at the five call sites, so a new caller cannot forget it.
"""

from unittest.mock import patch

import pytest
from constance.test import override_config
from django.urls import reverse

from apps.accounts.discord_service import send_discord_dm


@pytest.fixture
def opted_out(user_model, db):
    """Build a member who has opted out of DMs.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username="quiet",
        email="quiet@example.test",
        discord_id="999888777",
        discord_dm_opt_out=True,
    )


@pytest.fixture
def opted_in(user_model, db):
    """Build a member with the default setting.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username="loud",
        email="loud@example.test",
        discord_id="111222333",
    )


@pytest.mark.django_db
def test_dms_are_on_by_default(opted_in):
    assert opted_in.discord_dm_opt_out is False


@pytest.mark.django_db
def test_no_request_is_made_for_an_opted_out_member(opted_out):
    with patch("apps.accounts.discord_service.httpx.Client") as client:
        result = send_discord_dm(opted_out.discord_id, "hello")

    client.assert_not_called()
    assert result is True, (
        "a deliberate skip must read as success: callers use the return value to decide "
        "whether to retry and whether to stamp 'already warned'"
    )


@pytest.mark.django_db
@override_config(DISCORD_BOT_TOKEN="test-token")  # noqa: S106
def test_an_opted_in_member_still_gets_the_request(opted_in):
    with patch("apps.accounts.discord_service.httpx.Client") as client:
        send_discord_dm(opted_in.discord_id, "hello")

    client.assert_called()


@pytest.mark.django_db
@override_config(DISCORD_BOT_TOKEN="test-token")  # noqa: S106
def test_unknown_discord_ids_are_unaffected():
    """Not every DM target is a User row; those must not be blocked by accident."""
    with patch("apps.accounts.discord_service.httpx.Client") as client:
        send_discord_dm("000000000", "hello")

    client.assert_called()


@pytest.mark.django_db
def test_the_setting_is_editable_from_the_profile_form(client, opted_in):
    from apps.accounts.forms import ProfileForm

    assert "discord_dm_opt_out" in ProfileForm().fields

    client.force_login(opted_in)
    body = client.get(reverse("accounts:profile_edit")).content.decode()
    assert "discord_dm_opt_out" in body
    assert "Opt out of Discord DMs" in body


@pytest.mark.django_db
def test_the_form_warns_what_turning_them_off_costs(client, opted_in):
    """The setting is one click; the consequences are not obvious without being told."""
    client.force_login(opted_in)
    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert "DMs are how the team reaches you" in body
    for consequence in ("verification", "expire", "event"):
        assert consequence in body


def test_both_copies_of_the_form_carry_the_setting():
    """Keep the two copies of the profile form in step.

    The page and the HTMX partial each carry their own. The setting was added to the partial
    first and was invisible on the page a member actually opens -- the failure mode that
    duplicated markup produces.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    for template in (
        "templates/accounts/profile_edit.html",
        "templates/accounts/partials/profile_form.html",
    ):
        markup = (root / template).read_text()
        assert "discord_dm_opt_out" in markup, f"{template} is missing the setting"
        assert "DMs are how the team reaches you" in markup, f"{template} is missing the warning"

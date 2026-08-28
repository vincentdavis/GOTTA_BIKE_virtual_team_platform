"""Tests for blocking Discord accounts from signing in."""

from unittest.mock import MagicMock, patch

import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from django.test import Client
from django.urls import reverse

from apps.accounts.adapters import DiscordSocialAccountAdapter
from apps.accounts.models import BlockedDiscordId

BLOCKED_ID = "1201456726373834752"


def _sociallogin(discord_id):
    """Build a sociallogin stand-in carrying the Discord payload the adapter reads.

    Args:
        discord_id: The Discord account id to present.

    Returns:
        A mock with the attributes ``pre_social_login`` touches.

    """
    sociallogin = MagicMock()
    sociallogin.account.extra_data = {"id": discord_id, "username": "someone", "verified": True}
    sociallogin.is_existing = False
    sociallogin.user = None
    return sociallogin


@pytest.mark.django_db
def test_blocked_discord_id_is_refused_at_login(rf):
    BlockedDiscordId.objects.create(discord_id=BLOCKED_ID)
    request = rf.get("/accounts/discord/login/callback/")
    request._messages = MagicMock()

    with pytest.raises(ImmediateHttpResponse):
        DiscordSocialAccountAdapter().pre_social_login(request, _sociallogin(BLOCKED_ID))


@pytest.mark.django_db
def test_block_is_checked_before_the_discord_guild_call(rf):
    """A blocked account must not cost us a Discord API round trip."""
    BlockedDiscordId.objects.create(discord_id=BLOCKED_ID)
    request = rf.get("/accounts/discord/login/callback/")
    request._messages = MagicMock()

    with patch("apps.accounts.adapters.httpx.get") as mock_get, pytest.raises(ImmediateHttpResponse):
        DiscordSocialAccountAdapter().pre_social_login(request, _sociallogin(BLOCKED_ID))
    mock_get.assert_not_called()


@pytest.mark.django_db
def test_unblocked_id_passes_the_block_check(rf):
    """An id that is not on the list gets past the block and on to the usual checks."""
    BlockedDiscordId.objects.create(discord_id=BLOCKED_ID)
    request = rf.get("/accounts/discord/login/callback/")
    request._messages = MagicMock()
    sociallogin = _sociallogin("999999999999999999")

    with patch.object(DiscordSocialAccountAdapter, "_check_guild_membership") as guild_check:
        DiscordSocialAccountAdapter().pre_social_login(request, sociallogin)
    guild_check.assert_called_once()


@pytest.mark.django_db
def test_admin_can_add_and_remove_a_block(admin_authed_client):
    resp = admin_authed_client.post(
        reverse("compliance_block_add"),
        {"discord_id": BLOCKED_ID, "note": "spam"},
        follow=True,
    )
    assert resp.status_code == 200
    block = BlockedDiscordId.objects.get(discord_id=BLOCKED_ID)
    assert block.note == "spam"
    assert block.blocked_by is not None

    admin_authed_client.post(reverse("compliance_block_remove"), {"block_id": block.pk}, follow=True)
    assert not BlockedDiscordId.objects.filter(discord_id=BLOCKED_ID).exists()


@pytest.mark.django_db
def test_blocking_a_member_ends_their_session(admin_authed_client, user_model):
    # A separate Client: `admin_authed_client` *is* the shared `client` fixture, so logging
    # the member in on that one would just sign the admin out.
    member_client = Client()
    member = user_model.objects.create_user(username="tobeblocked", discord_id=BLOCKED_ID)
    member.set_unusable_password()
    member.save()
    member_client.force_login(member)
    assert member_client.get(reverse("accounts:profile")).status_code == 200

    admin_authed_client.post(reverse("compliance_block_add"), {"discord_id": BLOCKED_ID}, follow=True)

    # Rotating the password changes the session auth hash, so the old session is dead.
    assert member_client.get(reverse("accounts:profile")).status_code == 302


@pytest.mark.django_db
def test_non_admin_cannot_block(auth_client):
    assert auth_client.post(reverse("compliance_block_add"), {"discord_id": BLOCKED_ID}).status_code == 403
    block = BlockedDiscordId.objects.create(discord_id=BLOCKED_ID)
    assert auth_client.post(reverse("compliance_block_remove"), {"block_id": block.pk}).status_code == 403
    assert BlockedDiscordId.objects.filter(pk=block.pk).exists()


@pytest.mark.django_db
def test_non_numeric_id_is_rejected(admin_authed_client):
    admin_authed_client.post(reverse("compliance_block_add"), {"discord_id": "@someone"}, follow=True)
    assert not BlockedDiscordId.objects.exists()


@pytest.mark.django_db
def test_admin_cannot_block_themselves(admin_authed_client, app_admin):
    app_admin.discord_id = BLOCKED_ID
    app_admin.save(update_fields=["discord_id"])
    admin_authed_client.post(reverse("compliance_block_add"), {"discord_id": BLOCKED_ID}, follow=True)
    assert not BlockedDiscordId.objects.exists()

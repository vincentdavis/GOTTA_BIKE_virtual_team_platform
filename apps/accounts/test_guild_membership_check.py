"""The Discord guild check at login, with Discord mocked only at the HTTP boundary.

Before these tests the check was always patched out, so nothing noticed that an unset
``GUILD_ID`` skipped it entirely, that a non-JSON 429 body raised a 500, or that the
reconnect-by-``discord_id`` branch wrote a ``SocialAccount`` for a possible non-member and
then crashed assigning allauth's read-only ``is_existing``.

Fixtures (``callback_request``, ``make_sociallogin``, ``guilds_response``, ``discord_login``,
``guild_id``) live in ``apps/accounts/conftest.py``.
"""

from unittest.mock import patch

import httpx
import pytest
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount
from constance.test import override_config
from django.urls import reverse
from django.utils import timezone

from apps.accounts.adapters import DiscordSocialAccountAdapter
from apps.accounts.models import BlockedDiscordId, GuildMember

DISCORD_ID = "700000000000000001"
BLOCKED_ID = "700000000000000999"


@pytest.fixture
def check(guild_id):
    """Run the guild check against a mocked Discord answer (a response or an exception).

    Returns:
        ``run(request, sociallogin, answer)`` returning the ``httpx.get`` mock.

    """

    def run(request, sociallogin, answer):
        kwargs = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
        with override_config(GUILD_ID=guild_id), patch("apps.accounts.adapters.httpx.get", **kwargs) as get:
            DiscordSocialAccountAdapter()._check_guild_membership(request, sociallogin)
        return get

    return run


@pytest.fixture
def pre_social_login(guild_id, guilds_response):
    """Run ``pre_social_login`` with Discord listing the given servers for the rider.

    Returns:
        ``run(request, sociallogin, guild_ids)`` returning the ``httpx.get`` mock.

    """

    def run(request, sociallogin, guild_ids):
        with (
            override_config(GUILD_ID=guild_id, DISCORD_BOT_TOKEN=""),
            patch("apps.accounts.adapters.httpx.get", return_value=guilds_response(guild_ids)) as get,
        ):
            DiscordSocialAccountAdapter().pre_social_login(request, sociallogin)
        return get

    return run


# --- the check on its own -------------------------------------------------------------


@pytest.mark.django_db
def test_member_is_admitted(check, callback_request, make_sociallogin, guilds_response, guild_id):
    get = check(callback_request, make_sociallogin(DISCORD_ID), guilds_response([111, guild_id]))

    get.assert_called_once()
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert not callback_request._messages.add.called


@pytest.mark.django_db
def test_non_member_is_refused(check, callback_request, make_sociallogin, guilds_response):
    with pytest.raises(ImmediateHttpResponse) as refused:
        check(callback_request, make_sociallogin(DISCORD_ID), guilds_response([111, 222]))

    assert refused.value.response["Location"] == reverse("account_login")
    assert "must be a member" in callback_request.error_text()


@pytest.mark.django_db
def test_member_of_no_servers_is_refused(check, callback_request, make_sociallogin, guilds_response):
    with pytest.raises(ImmediateHttpResponse):
        check(callback_request, make_sociallogin(DISCORD_ID), guilds_response([]))


@pytest.mark.django_db
def test_unset_guild_id_refuses_instead_of_skipping(callback_request, make_sociallogin):
    # 0 is what "not configured" looks like: GUILD_ID is an int setting.
    with (
        override_config(GUILD_ID=0),
        patch("apps.accounts.adapters.httpx.get") as get,
        patch("apps.accounts.adapters.logfire") as log,
        pytest.raises(ImmediateHttpResponse) as refused,
    ):
        DiscordSocialAccountAdapter()._check_guild_membership(callback_request, make_sociallogin(DISCORD_ID))

    assert refused.value.response["Location"] == reverse("account_login")
    get.assert_not_called()
    log.error.assert_called_once()
    assert "not configured" in callback_request.error_text()


@pytest.mark.django_db
@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_http_error_status_is_refused(check, callback_request, make_sociallogin, guilds_response, guild_id, status):
    with pytest.raises(ImmediateHttpResponse):
        check(callback_request, make_sociallogin(DISCORD_ID), guilds_response([guild_id], status=status))

    assert "Failed to verify" in callback_request.error_text()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout("timed out"), httpx.ConnectTimeout("timed out"), httpx.ConnectError("no route")],
)
def test_timeout_and_transport_errors_are_refused(check, callback_request, make_sociallogin, exc):
    with pytest.raises(ImmediateHttpResponse):
        check(callback_request, make_sociallogin(DISCORD_ID), exc)

    assert "Failed to verify" in callback_request.error_text()


@pytest.mark.django_db
def test_rate_limit_with_json_body_is_refused(check, callback_request, make_sociallogin, guilds_response):
    answer = guilds_response(status=429, body=b'{"retry_after": 1.5, "global": false}')

    with patch("apps.accounts.adapters.logfire") as log, pytest.raises(ImmediateHttpResponse):
        check(callback_request, make_sociallogin(DISCORD_ID), answer)

    assert log.warning.call_args.kwargs["retry_after"] == pytest.approx(1.5)
    assert "rate limiting" in callback_request.error_text()


@pytest.mark.django_db
@pytest.mark.parametrize("body", [b"<html>Too Many Requests</html>", b"", b"[1, 2]"])
def test_rate_limit_with_non_json_body_is_refused_without_a_500(
    check, callback_request, make_sociallogin, guilds_response, body
):
    """The old code called ``response.json().get(...)`` unguarded here and raised out of the login."""
    answer = guilds_response(status=429, body=body, headers={"Retry-After": "7"})

    with patch("apps.accounts.adapters.logfire") as log, pytest.raises(ImmediateHttpResponse):
        check(callback_request, make_sociallogin(DISCORD_ID), answer)

    assert log.warning.call_args.kwargs["retry_after"] == "7"
    assert "rate limiting" in callback_request.error_text()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [b"not json", b'{"id": "1"}', b'[{"name": "no id"}]', b'[{"id": "not-a-number"}]', b"null"],
)
def test_unreadable_guild_list_is_refused(check, callback_request, make_sociallogin, guilds_response, body):
    with pytest.raises(ImmediateHttpResponse):
        check(callback_request, make_sociallogin(DISCORD_ID), guilds_response(body=body))

    assert "Failed to verify" in callback_request.error_text()


@pytest.mark.django_db
def test_a_login_without_a_token_is_refused(callback_request, make_sociallogin, guild_id):
    with (
        override_config(GUILD_ID=guild_id),
        patch("apps.accounts.adapters.httpx.get") as get,
        pytest.raises(ImmediateHttpResponse),
    ):
        DiscordSocialAccountAdapter()._check_guild_membership(
            callback_request, make_sociallogin(DISCORD_ID, token=None)
        )

    get.assert_not_called()
    assert "Failed to verify" in callback_request.error_text()


@pytest.mark.django_db
def test_the_guild_check_logs_ids_not_usernames(check, callback_request, make_sociallogin, guilds_response):
    with patch("apps.accounts.adapters.logfire") as log, pytest.raises(ImmediateHttpResponse):
        check(callback_request, make_sociallogin(DISCORD_ID), guilds_response([1]))

    kwargs = log.warning.call_args.kwargs
    assert kwargs["discord_id"] == DISCORD_ID
    assert "discord_username" not in kwargs


@pytest.mark.django_db
def test_the_invite_renders_as_a_link_on_the_login_page(client, discord_login):
    """It was queued as plain text, so the toast showed the raw ``<a href=...>`` markup."""
    with override_config(DISCORD_URL="https://discord.gg/x?a=1&b=2", GUILD_NAME="<b>Team</b>"):
        discord_login(client, DISCORD_ID, guild_ids=[111])

    body = client.get(reverse("account_login")).content.decode()
    link = '<a href="https://discord.gg/x?a=1&amp;b=2" target="_blank" rel="noopener" class="link">Join here</a>'
    assert link in body
    # The admin-set name is still escaped: only the invite is markup.
    assert "the &lt;b&gt;Team&lt;/b&gt; Discord server" in body


@pytest.mark.django_db
@pytest.mark.parametrize("discord_url", ["", "#", "javascript:alert(1)"])
def test_no_invite_link_without_an_http_url(check, callback_request, make_sociallogin, guilds_response, discord_url):
    with override_config(DISCORD_URL=discord_url), pytest.raises(ImmediateHttpResponse):
        check(callback_request, make_sociallogin(DISCORD_ID), guilds_response([1]))

    assert "<a" not in callback_request.error_text()


# --- pre_social_login: ordering, reconnect, block list -------------------------------


@pytest.mark.django_db
def test_reconnect_by_discord_id_attaches_the_existing_account(
    pre_social_login, callback_request, make_sociallogin, guild_id, user_model, discord_app
):
    """The branch used to crash on ``sociallogin.is_existing = True`` (a property with no setter)."""
    existing = user_model.objects.create_user(username="kept", email="kept@example.test", discord_id=DISCORD_ID)
    sociallogin = make_sociallogin(DISCORD_ID, email="new-address@example.test")

    pre_social_login(callback_request, sociallogin, [guild_id])

    assert sociallogin.is_existing
    assert sociallogin.user.pk == existing.pk
    assert SocialAccount.objects.get(uid=DISCORD_ID).user_id == existing.pk
    assert user_model.objects.count() == 1


@pytest.mark.django_db
def test_reconnect_logs_ids_not_names(
    pre_social_login, callback_request, make_sociallogin, guild_id, user_model, discord_app
):
    existing = user_model.objects.create_user(
        username="kept", email="kept@example.test", discord_id=DISCORD_ID, first_name="Kept", last_name="Rider"
    )

    with patch("apps.accounts.adapters.logfire") as log:
        pre_social_login(callback_request, make_sociallogin(DISCORD_ID), [guild_id])

    reconnect = next(c for c in log.warning.call_args_list if "reconnecting" in c.args[0])
    assert reconnect.kwargs == {"user_id": existing.pk, "discord_id": DISCORD_ID}


@pytest.mark.django_db
def test_reconnect_writes_nothing_for_a_non_member(pre_social_login, callback_request, make_sociallogin, user_model):
    """The guild check now runs before the reconnect's ``SocialAccount`` write."""
    user_model.objects.create_user(username="kept", email="kept@example.test", discord_id=DISCORD_ID)

    with pytest.raises(ImmediateHttpResponse):
        pre_social_login(callback_request, make_sociallogin(DISCORD_ID), [1])

    assert not SocialAccount.objects.exists()


@pytest.mark.django_db
def test_reconnect_writes_nothing_for_an_unverified_email(
    pre_social_login, callback_request, make_sociallogin, guild_id, user_model
):
    user_model.objects.create_user(username="kept", email="kept@example.test", discord_id=DISCORD_ID)

    with pytest.raises(ImmediateHttpResponse):
        pre_social_login(callback_request, make_sociallogin(DISCORD_ID, verified=False), [guild_id])

    assert not SocialAccount.objects.exists()
    assert "not verified" in callback_request.error_text()


@pytest.mark.django_db
def test_two_accounts_with_the_same_discord_id_are_refused_not_guessed(
    pre_social_login, callback_request, make_sociallogin, guild_id, user_model
):
    for name in ("first", "second"):
        user_model.objects.create_user(username=name, email=f"{name}@example.test", discord_id=DISCORD_ID)

    with pytest.raises(ImmediateHttpResponse) as refused:
        pre_social_login(callback_request, make_sociallogin(DISCORD_ID), [guild_id])

    assert refused.value.response["Location"] == reverse("account_login")
    assert not SocialAccount.objects.exists()
    assert "More than one account" in callback_request.error_text()


@pytest.mark.django_db
def test_a_connect_from_the_connections_page_is_not_reattached(
    pre_social_login, callback_request, make_sociallogin, guild_id, user_model
):
    """A "connect" is attached to the signed-in user by allauth itself; the adapter keeps out."""
    user_model.objects.create_user(username="other", email="other@example.test", discord_id=DISCORD_ID)
    sociallogin = make_sociallogin(DISCORD_ID, process="connect")

    pre_social_login(callback_request, sociallogin, [guild_id])

    assert not sociallogin.is_existing
    assert not SocialAccount.objects.exists()


@pytest.mark.django_db
def test_an_existing_account_owned_by_a_blocked_discord_id_is_refused(
    pre_social_login, callback_request, make_sociallogin, guild_id, user_model
):
    """A second Discord account sharing a blocked person's email must not reach their account."""
    blocked = user_model.objects.create_user(username="blocked", email="shared@example.test", discord_id=BLOCKED_ID)
    BlockedDiscordId.objects.create(discord_id=BLOCKED_ID)
    # What allauth's email lookup has resolved by the time pre_social_login runs.
    sociallogin = make_sociallogin(DISCORD_ID, email="shared@example.test", user=blocked)

    with pytest.raises(ImmediateHttpResponse):
        pre_social_login(callback_request, sociallogin, [guild_id])

    blocked.refresh_from_db()
    assert blocked.discord_id == BLOCKED_ID
    assert "cannot sign in" in callback_request.error_text()


@pytest.mark.django_db
def test_the_account_block_check_runs_before_the_discord_call(callback_request, make_sociallogin, user_model):
    blocked = user_model.objects.create_user(username="blocked", email="shared@example.test", discord_id=BLOCKED_ID)
    BlockedDiscordId.objects.create(discord_id=BLOCKED_ID)

    with patch("apps.accounts.adapters.httpx.get") as get, pytest.raises(ImmediateHttpResponse):
        DiscordSocialAccountAdapter().pre_social_login(callback_request, make_sociallogin(DISCORD_ID, user=blocked))

    get.assert_not_called()


@pytest.mark.django_db
def test_an_unblocked_existing_account_still_signs_in(
    pre_social_login, callback_request, make_sociallogin, guild_id, user_model
):
    """Only a block on the account's own id refuses; some other block does not."""
    BlockedDiscordId.objects.create(discord_id=BLOCKED_ID)
    existing = user_model.objects.create_user(
        username="fine", email="fine@example.test", discord_id="700000000000000555"
    )
    sociallogin = make_sociallogin(DISCORD_ID, user=existing)

    pre_social_login(callback_request, sociallogin, [guild_id])

    existing.refresh_from_db()
    assert existing.discord_id == DISCORD_ID


@pytest.mark.django_db
def test_a_member_login_clears_a_stale_departure(pre_social_login, callback_request, make_sociallogin, guild_id):
    GuildMember.objects.create(discord_id=DISCORD_ID, username="x", date_left=timezone.now())

    pre_social_login(callback_request, make_sociallogin(DISCORD_ID), [guild_id])

    assert GuildMember.objects.get(discord_id=DISCORD_ID).date_left is None


@pytest.mark.django_db
def test_a_refused_login_leaves_the_departure_in_place(pre_social_login, callback_request, make_sociallogin):
    GuildMember.objects.create(discord_id=DISCORD_ID, username="x", date_left=timezone.now())

    with pytest.raises(ImmediateHttpResponse):
        pre_social_login(callback_request, make_sociallogin(DISCORD_ID), [1])

    assert GuildMember.objects.get(discord_id=DISCORD_ID).date_left is not None


# --- end to end through allauth's callback ------------------------------------------------


def _signed_in(client):
    return "_auth_user_id" in client.session


@pytest.mark.django_db
def test_new_member_signs_up_and_is_signed_in(client, discord_login, user_model):
    response = discord_login(client, DISCORD_ID)

    assert response.status_code == 302
    user = user_model.objects.get(discord_id=DISCORD_ID)
    assert int(client.session["_auth_user_id"]) == user.pk
    assert SocialAccount.objects.get(uid=DISCORD_ID).user_id == user.pk
    response.guild_get.assert_called_once()


@pytest.mark.django_db
def test_new_non_member_gets_no_account_and_no_session(client, discord_login, user_model):
    response = discord_login(client, DISCORD_ID, guild_ids=[111])

    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    assert not _signed_in(client)
    assert not user_model.objects.filter(discord_id=DISCORD_ID).exists()
    assert not SocialAccount.objects.exists()
    assert "must be a member" in client.get(reverse("account_login")).content.decode()


@pytest.mark.django_db
def test_existing_non_member_gets_no_session_and_no_changes(client, discord_login, user_model):
    user = user_model.objects.create_user(
        username="old", email="old@example.test", discord_id=DISCORD_ID, discord_username="before"
    )
    user.set_unusable_password()
    user.save()
    account = SocialAccount.objects.create(user=user, provider="discord", uid=DISCORD_ID, extra_data={})

    response = discord_login(client, DISCORD_ID, guild_ids=[111])

    assert response["Location"] == reverse("account_login")
    assert not _signed_in(client)
    user.refresh_from_db()
    assert user.discord_username == "before"
    assert SocialAccount.objects.get().pk == account.pk


@pytest.mark.django_db
def test_existing_member_signs_in(client, discord_login, user_model):
    user = user_model.objects.create_user(username="old", email="old@example.test", discord_id=DISCORD_ID)
    user.set_unusable_password()
    user.save()
    SocialAccount.objects.create(user=user, provider="discord", uid=DISCORD_ID, extra_data={})

    discord_login(client, DISCORD_ID)

    assert int(client.session["_auth_user_id"]) == user.pk
    user.refresh_from_db()
    assert user.discord_username == f"rider{DISCORD_ID}"


@pytest.mark.django_db
def test_unset_guild_id_refuses_a_real_login(client, discord_login, user_model):
    response = discord_login(client, DISCORD_ID, configured_guild_id=0)

    assert response["Location"] == reverse("account_login")
    assert not _signed_in(client)
    assert not user_model.objects.filter(discord_id=DISCORD_ID).exists()
    response.guild_get.assert_not_called()


@pytest.mark.django_db
def test_discord_outage_refuses_a_real_login_without_a_500(client, discord_login, user_model):
    response = discord_login(client, DISCORD_ID, guilds=httpx.ReadTimeout("slow"))

    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    assert not _signed_in(client)
    assert not user_model.objects.filter(discord_id=DISCORD_ID).exists()


@pytest.mark.django_db
def test_non_json_rate_limit_refuses_a_real_login_without_a_500(client, discord_login, guilds_response):
    response = discord_login(client, DISCORD_ID, guilds=guilds_response(status=429, body=b"slow down"))

    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    assert not _signed_in(client)


@pytest.mark.django_db
def test_reconnect_through_a_real_login(client, discord_login, user_model):
    """Lost SocialAccount and a changed email: the old account is found by discord_id and kept."""
    user = user_model.objects.create_user(
        username="kept", email="kept@example.test", discord_id=DISCORD_ID, first_name="Kept"
    )
    user.set_unusable_password()
    user.save()

    discord_login(client, DISCORD_ID, email="changed@example.test")

    assert int(client.session["_auth_user_id"]) == user.pk
    assert user_model.objects.count() == 1
    assert SocialAccount.objects.get(uid=DISCORD_ID).user_id == user.pk
    user.refresh_from_db()
    assert user.first_name == "Kept"


@pytest.mark.django_db
def test_blocked_account_reached_by_email_is_refused_end_to_end(client, discord_login, user_model):
    """Email authentication in allauth resolves the blocked account; the adapter refuses it."""
    blocked = user_model.objects.create_user(username="blocked", email="shared@example.test", discord_id=BLOCKED_ID)
    blocked.set_unusable_password()
    blocked.save()
    EmailAddress.objects.create(user=blocked, email="shared@example.test", verified=True, primary=True)
    BlockedDiscordId.objects.create(discord_id=BLOCKED_ID)

    response = discord_login(client, DISCORD_ID, email="shared@example.test")

    assert response["Location"] == reverse("account_login")
    assert not _signed_in(client)
    blocked.refresh_from_db()
    assert blocked.discord_id == BLOCKED_ID
    assert not SocialAccount.objects.filter(uid=DISCORD_ID).exists()
    response.guild_get.assert_not_called()


@pytest.mark.django_db
def test_same_email_unblocked_account_is_signed_in_end_to_end(client, discord_login, user_model):
    """Control for the test above: without the block, allauth's email match does sign them in."""
    existing = user_model.objects.create_user(username="moved", email="shared@example.test", discord_id=BLOCKED_ID)
    existing.set_unusable_password()
    existing.save()
    EmailAddress.objects.create(user=existing, email="shared@example.test", verified=True, primary=True)

    discord_login(client, DISCORD_ID, email="shared@example.test")

    assert int(client.session["_auth_user_id"]) == existing.pk

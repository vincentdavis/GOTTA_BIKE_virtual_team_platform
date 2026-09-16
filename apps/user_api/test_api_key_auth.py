"""Bearer-key authentication for ``/api/user/``, including the Discord-membership rule.

A key is a second way in that never touches a session, so leaving the team's Discord server
has to end it too: ``user_can_use_api`` refuses anyone the guild sync has marked as departed
(staff and superusers exempt, riders with no ``GuildMember`` row yet unaffected), and
``UserApiKeyAuth`` re-asks it on every request.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from constance.test import override_config
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import GuildMember
from apps.user_api.models import UserApiKey
from apps.user_api.services import issue_api_key, lookup_active_key, user_can_use_api
from apps.zwiftracing.models import ZRRider

DISCORD_ID = "720000000000000001"
TEAM_ROLE = "555000000000000002"
UNKNOWN_ZWID = 1
KNOWN_ZWID = 2


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Reset the rate limits, which count in the shared LocMem cache."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_user(db, user_model):
    """Make a rider whose team_member access comes from a Discord role, holding one API key.

    Returns:
        ``(user, raw_key)``.

    """
    user = user_model.objects.create_user(
        username="api_rider",
        email="api_rider@example.test",
        discord_id=DISCORD_ID,
        discord_roles={TEAM_ROLE: "Team Member"},
    )
    _key, raw = issue_api_key(user, "script")
    return user, raw


@pytest.fixture(autouse=True)
def _team_role_grants_membership():
    with override_config(PERM_TEAM_MEMBER_ROLES=f'["{TEAM_ROLE}"]', PERM_ROLES_REQUIRED_USE_API="[]"):
        yield


def _get(client, raw_key, zwid=UNKNOWN_ZWID):
    return client.get(f"/api/user/zr_profile/{zwid}", headers={"Authorization": f"Bearer {raw_key}"})


def _left(discord_id):
    return GuildMember.objects.create(discord_id=discord_id, username="x", date_left=timezone.now())


# --- the baseline -------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_member_key_authenticates(client, api_user):
    _user, raw = api_user

    # 404 is the endpoint's own "no such rider": authentication passed.
    assert _get(client, raw).status_code == 404


@pytest.mark.django_db
def test_a_member_key_returns_a_known_rider(client, api_user):
    _user, raw = api_user
    ZRRider.objects.create(zwid=KNOWN_ZWID, name="Known Rider")

    response = _get(client, raw, zwid=KNOWN_ZWID)

    assert response.status_code == 200
    assert response.json()["zwid"] == KNOWN_ZWID


@pytest.mark.django_db
def test_a_member_key_records_its_use(client, api_user):
    user, raw = api_user

    _get(client, raw)

    assert UserApiKey.objects.get(user=user).last_used_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize("header", [None, "Bearer ", "Bearer coal_not-a-real-key", "Token something"])
def test_missing_or_unknown_keys_are_refused(client, api_user, header):
    headers = {"Authorization": header} if header is not None else {}

    assert client.get(f"/api/user/zr_profile/{UNKNOWN_ZWID}", headers=headers).status_code == 401


@pytest.mark.django_db
def test_revoked_and_expired_keys_are_refused(client, api_user):
    user, raw = api_user
    key = UserApiKey.objects.get(user=user)

    key.revoked_at = timezone.now()
    key.save()
    assert _get(client, raw).status_code == 401

    key.revoked_at = None
    key.expires_at = timezone.now() - timedelta(minutes=1)
    key.save()
    assert _get(client, raw).status_code == 401
    assert lookup_active_key(raw) is None


@pytest.mark.django_db
def test_a_deactivated_owner_is_refused(client, api_user):
    user, raw = api_user
    user.is_active = False
    user.save()

    assert _get(client, raw).status_code == 401


@pytest.mark.django_db
def test_losing_the_team_member_role_refuses_the_key(client, api_user):
    user, raw = api_user
    user.discord_roles = {}
    user.save()

    assert _get(client, raw).status_code == 401


@pytest.mark.django_db
def test_a_missing_required_role_refuses_the_key(client, api_user):
    _user, raw = api_user

    with override_config(PERM_ROLES_REQUIRED_USE_API='["999"]'):
        assert _get(client, raw).status_code == 401


# --- the guild rule -----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_departed_rider_key_is_refused(client, api_user):
    user, raw = api_user
    assert _get(client, raw).status_code == 404

    _left(DISCORD_ID)

    assert _get(client, raw).status_code == 401
    # The stale role alone would still say yes; the departure is what refuses.
    assert user.has_permission("team_member")
    assert user_can_use_api(user) is False


@pytest.mark.django_db
def test_a_rejoined_rider_key_works_again(client, api_user):
    _user, raw = api_user
    row = _left(DISCORD_ID)
    assert _get(client, raw).status_code == 401

    row.date_left = None
    row.save()

    assert _get(client, raw).status_code == 404


@pytest.mark.django_db
def test_a_rider_with_no_guild_row_keeps_their_key(client, api_user):
    user, raw = api_user
    assert not GuildMember.objects.exists()

    assert _get(client, raw).status_code == 404
    assert user_can_use_api(user) is True


@pytest.mark.django_db
@pytest.mark.parametrize("flags", [{"is_staff": True}, {"is_staff": True, "is_superuser": True}])
def test_departed_staff_keys_still_work(client, api_user, flags):
    user, raw = api_user
    for name, value in flags.items():
        setattr(user, name, value)
    user.save()
    _left(DISCORD_ID)

    assert _get(client, raw).status_code == 404
    assert user_can_use_api(user) is True


@pytest.mark.django_db
def test_the_refusal_is_logged_with_ids_and_the_reason(client, api_user):
    user, raw = api_user
    _left(DISCORD_ID)

    with patch("apps.user_api.api.logfire") as log:
        assert _get(client, raw).status_code == 401

    kwargs = log.warning.call_args.kwargs
    assert kwargs["user_id"] == user.pk
    assert kwargs["left_guild"] is True
    assert not any("auth" in name for name in kwargs)


@pytest.mark.django_db
def test_a_departed_rider_cannot_reach_the_key_page(client, api_user):
    """Through the full stack the middleware ends the session first (the gate is tested below)."""
    user, _raw = api_user
    client.force_login(user)
    assert client.get(reverse("user_api:api_keys_list")).status_code == 200

    _left(DISCORD_ID)

    response = client.get(reverse("user_api:api_keys_list"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))


@pytest.mark.django_db
def test_the_key_page_gate_refuses_a_departed_rider_on_its_own(rf, api_user):
    """Without the middleware in front, the page's ``user_can_use_api`` gate still says no."""
    from django.contrib.messages.storage.fallback import FallbackStorage

    from apps.user_api.views import api_keys_list

    user, _raw = api_user
    _left(DISCORD_ID)
    request = rf.get(reverse("user_api:api_keys_list"))
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    response = api_keys_list(request)

    assert response.status_code == 302
    assert response["Location"] == reverse("home")

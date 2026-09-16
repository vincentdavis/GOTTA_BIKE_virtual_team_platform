"""A rider the guild sync has marked as gone is signed out on their next request.

A Discord login checks guild membership live, but the session outlives it: before this, a
rider who left the team's Discord server kept every page their stored ``team_member`` role
opened until the session expired, and the role syncs skip departed riders, so that role was
never taken away. ``DepartedMemberLogoutMiddleware`` ends the session as soon as the sync has
stamped ``GuildMember.date_left``.

The edges matter as much as the rule: a rider with no ``GuildMember`` row yet (signed up
since the last sync -- the next sync writes one), a rider who has come back (``date_left``
cleared) and staff or superusers (the owner kept ``/admin/``'s password login outside the
guild rule) all keep their session.
"""

from unittest.mock import patch

import pytest
from constance.test import override_config
from django.urls import reverse
from django.utils import timezone

from apps.accounts.membership import has_left_guild, is_departed_member
from apps.accounts.middleware import DEPARTED_MESSAGE
from apps.accounts.models import GuildMember

TEAM_ROLE = "555000000000000001"
DISCORD_ID = "123450000000000001"


@pytest.fixture
def role_member(db, user_model):
    """Make a rider whose team_member access comes from a Discord role, not an override.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username="role_rider",
        email="role_rider@example.test",
        discord_id=DISCORD_ID,
        discord_roles={TEAM_ROLE: "Team Member"},
    )


@pytest.fixture(autouse=True)
def _team_role_grants_membership():
    """Map the rider's Discord role to ``team_member`` for the length of each test."""
    with override_config(PERM_TEAM_MEMBER_ROLES=f'["{TEAM_ROLE}"]'):
        yield


def _guild_row(discord_id, *, left=False, user=None):
    """Make the GuildMember row the guild sync would have written.

    Returns:
        The row.

    """
    return GuildMember.objects.create(
        discord_id=discord_id,
        username="someone",
        user=user,
        date_left=timezone.now() if left else None,
    )


def _is_signed_in(client):
    return "_auth_user_id" in client.session


@pytest.mark.django_db
def test_role_member_can_open_a_team_page(client, role_member):
    """The baseline: the role alone opens the page, so the test below proves something."""
    _guild_row(DISCORD_ID, user=role_member)
    client.force_login(role_member)

    assert client.get(reverse("team:links")).status_code == 200


@pytest.mark.django_db
def test_departed_rider_is_signed_out_on_the_next_request(client, role_member):
    client.force_login(role_member)
    assert client.get(reverse("team:links")).status_code == 200

    _guild_row(DISCORD_ID, left=True, user=role_member)
    response = client.get(reverse("team:links"))

    # Anonymous now, so the login-protected page sends them to log in.
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))
    assert not _is_signed_in(client)
    # And they stay out: the session is gone, not just this one request.
    assert client.get(reverse("team:links")).status_code == 302


@pytest.mark.django_db
def test_the_signed_out_rider_is_told_why(client, role_member):
    client.force_login(role_member)
    _guild_row(DISCORD_ID, left=True, user=role_member)

    response = client.get(reverse("team:links"), follow=True)

    body = response.content.decode()
    assert "no longer a member" in body
    assert DEPARTED_MESSAGE.split("'")[0] in body


@pytest.mark.django_db
def test_a_public_page_still_renders_for_the_signed_out_rider(client, role_member):
    client.force_login(role_member)
    _guild_row(DISCORD_ID, left=True, user=role_member)

    response = client.get(reverse("home"))

    assert response.status_code == 200
    assert not _is_signed_in(client)
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_the_row_is_found_by_discord_id_even_when_it_is_not_linked(client, role_member):
    """A rider who left before the sync ever linked their row is still signed out."""
    client.force_login(role_member)
    _guild_row(DISCORD_ID, left=True, user=None)

    assert client.get(reverse("team:links")).status_code == 302
    assert not _is_signed_in(client)


@pytest.mark.django_db
def test_an_old_departed_account_linked_to_the_user_does_not_count(client, role_member):
    """Someone who moved to a new Discord account keeps access.

    The link still points at the old, departed row (``_release_user_link`` keeps it), but the
    rule follows the account they sign in with now.
    """
    _guild_row("999990000000000009", left=True, user=role_member)
    client.force_login(role_member)

    assert client.get(reverse("team:links")).status_code == 200


@pytest.mark.django_db
def test_a_rejoined_rider_keeps_access(client, role_member):
    row = _guild_row(DISCORD_ID, left=True, user=role_member)
    row.date_left = None  # what the sync does when it sees them again
    row.save()
    client.force_login(role_member)

    assert client.get(reverse("team:links")).status_code == 200
    assert _is_signed_in(client)


@pytest.mark.django_db
def test_a_rider_with_no_guild_row_keeps_access(client, role_member):
    """New riders are not in the table until the next sync; that is not a departure.

    Only until then: a sync that does not list them records them as departed
    (``apps/accounts/test_guild_sync_safety.py``).
    """
    assert not GuildMember.objects.exists()
    client.force_login(role_member)

    assert client.get(reverse("team:links")).status_code == 200
    assert _is_signed_in(client)


@pytest.mark.django_db
def test_a_local_account_without_a_discord_id_is_not_looked_up(user_model, django_assert_num_queries):
    local = user_model.objects.create_user(username="local", email="local@example.test")

    with django_assert_num_queries(0):
        assert has_left_guild(local) is False


@pytest.mark.django_db
@pytest.mark.parametrize("flags", [{"is_staff": True}, {"is_superuser": True, "is_staff": True}])
def test_departed_staff_and_superusers_keep_access(client, user_model, flags):
    staff = user_model.objects.create_user(
        username="staffer",
        email="staffer@example.test",
        discord_id=DISCORD_ID,
        discord_roles={TEAM_ROLE: "Team Member"},
        **flags,
    )
    _guild_row(DISCORD_ID, left=True, user=staff)
    client.force_login(staff)

    assert client.get(reverse("team:links")).status_code == 200
    assert _is_signed_in(client)
    assert is_departed_member(staff) is False
    assert has_left_guild(staff) is True


@pytest.mark.django_db
def test_an_htmx_request_gets_a_client_redirect_to_login(client, role_member):
    """A swap target must not receive the login page; the browser is sent there instead."""
    client.force_login(role_member)
    _guild_row(DISCORD_ID, left=True, user=role_member)

    response = client.get(reverse("team:links"), headers={"HX-Request": "true"})

    # Not the view's 302 (which htmx would follow and swap in): an empty client redirect.
    assert response.status_code == 200
    assert response["HX-Redirect"] == reverse("account_login")
    assert not response.content
    assert not _is_signed_in(client)

    # The message waits for the page the browser is sent to.
    assert "no longer a member" in client.get(reverse("account_login")).content.decode()


@pytest.mark.django_db
def test_the_check_costs_one_query_per_signed_in_request(rf, role_member, django_assert_num_queries):
    """One indexed lookup and nothing cached, so a sync takes effect on the next request."""
    from apps.accounts.middleware import DepartedMemberLogoutMiddleware

    request = rf.get("/")
    request.user = role_member
    middleware = DepartedMemberLogoutMiddleware(lambda r: "passed")

    with django_assert_num_queries(1):
        assert middleware(request) == "passed"

    _guild_row(DISCORD_ID, left=True, user=role_member)
    assert is_departed_member(role_member) is True


@pytest.mark.django_db
def test_anonymous_requests_are_not_looked_up(client):
    _guild_row(DISCORD_ID, left=True)

    with patch("apps.accounts.middleware.is_departed_member") as check:
        client.get(reverse("account_login"))

    check.assert_not_called()


@pytest.mark.django_db
def test_a_departed_rider_trying_discord_again_is_refused_by_the_guild_check(client, role_member, discord_login):
    """The re-login path is untouched: the live guild check still turns them away.

    The middleware ends the session; the Discord callback then runs as anonymous and the
    adapter refuses them, landing on the login page with the guild message -- no loop.
    """
    from allauth.socialaccount.models import SocialAccount

    SocialAccount.objects.create(user=role_member, provider="discord", uid=DISCORD_ID, extra_data={})
    client.force_login(role_member)
    _guild_row(DISCORD_ID, left=True, user=role_member)
    assert client.get(reverse("team:links")).status_code == 302

    response = discord_login(client, DISCORD_ID, guild_ids=[1])

    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    assert not _is_signed_in(client)
    # A refused login must not clear the stamp.
    assert GuildMember.objects.get(discord_id=DISCORD_ID).date_left is not None
    login_page = client.get(reverse("account_login"))
    assert login_page.status_code == 200
    assert "must be a member" in login_page.content.decode()


@pytest.mark.django_db
def test_a_rejoined_rider_signing_in_again_is_not_signed_straight_back_out(client, role_member, discord_login):
    """The sync has not caught up yet, but Discord just confirmed they are back."""
    from allauth.socialaccount.models import SocialAccount

    role_member.set_unusable_password()
    role_member.save()
    SocialAccount.objects.create(user=role_member, provider="discord", uid=DISCORD_ID, extra_data={})
    _guild_row(DISCORD_ID, left=True, user=role_member)

    discord_login(client, DISCORD_ID)

    assert _is_signed_in(client)
    assert client.get(reverse("team:links")).status_code == 200
    assert _is_signed_in(client)

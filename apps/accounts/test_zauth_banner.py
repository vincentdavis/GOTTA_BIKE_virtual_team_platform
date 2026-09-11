"""The Zwift OAuth migration banner.

Prompts members who verified by any route other than Zwift OAuth. Distinct from
the profile-incomplete banner, which only flags never-verified members and so
leaves the legacy/admin cohort — the actual migration backlog — with no signal.
"""

import pytest
from constance.test import override_config
from django.urls import reverse

BANNER_MARKER = 'id="zauth-banner"'


def _member(user_model, username, **kwargs):
    return user_model.objects.create_user(username=username, discord_id=username, **kwargs)


@pytest.fixture
def legacy_client(client, user_model):
    """Log in a member carrying a grandfathered legacy verification.

    Returns:
        The test client, authenticated as that member.

    """
    user = _member(user_model, "legacy-user", zwid_verified=True, zwid_verification_method="legacy", zwid=1234)
    client.force_login(user)
    return client


# --- the property ------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("verified", "method", "expected"),
    [
        (True, "zauth", True),
        (True, "legacy", False),
        (True, "admin", False),
        (True, "", False),
        # What unverify_zwift leaves behind until the reconcile clears the method.
        (False, "zauth", False),
        (False, "", False),
    ],
    ids=["zauth", "legacy", "admin", "verified-no-method", "zauth-method-left-behind", "never-verified"],
)
def test_is_zauth_verified_needs_the_flag_and_the_method(user_model, verified, method, expected):
    user = _member(user_model, "u", zwid_verified=verified, zwid_verification_method=method)
    assert user.is_zauth_verified is expected


@pytest.mark.django_db
def test_removing_your_own_verification_ends_zauth_verified(client, user_model):
    """unverify_zwift clears zwid_verified but leaves the method for the hourly reconcile.

    The property must not wait for that reconcile: it aborts while the zauth service is
    down, so the method can stay "zauth" indefinitely.
    """
    user = _member(user_model, "z", zwid_verified=True, zwid_verification_method="zauth", zwid=99)
    client.force_login(user)

    client.post(reverse("accounts:unverify_zwift"))
    user.refresh_from_db()

    assert user.zwid_verification_method == "zauth"  # the leftover this guards against
    assert user.is_zauth_verified is False


# --- visibility --------------------------------------------------------------


@pytest.mark.django_db
@override_config(ZAUTH_BANNER_ENABLED=False)
def test_hidden_when_the_toggle_is_off(legacy_client):
    """Default state: nothing changes for anyone until an admin turns it on."""
    assert BANNER_MARKER not in legacy_client.get(reverse("accounts:profile")).content.decode()


@pytest.mark.django_db
@override_config(ZAUTH_BANNER_ENABLED=True)
def test_shown_to_a_legacy_verified_member(legacy_client):
    body = legacy_client.get(reverse("accounts:profile")).content.decode()

    assert BANNER_MARKER in body
    assert reverse("zwift:zauth") in body


@pytest.mark.django_db
@override_config(ZAUTH_BANNER_ENABLED=True)
def test_hidden_from_a_zauth_verified_member(client, user_model):
    user = _member(user_model, "z", zwid_verified=True, zwid_verification_method="zauth", zwid=99)
    client.force_login(user)

    assert BANNER_MARKER not in client.get(reverse("accounts:profile")).content.decode()


@pytest.mark.django_db
@override_config(ZAUTH_BANNER_ENABLED=True)
def test_shown_to_an_admin_verified_member(client, user_model):
    """Admin-verified members still need to migrate before the cutover."""
    user = _member(user_model, "a", zwid_verified=True, zwid_verification_method="admin", zwid=77)
    client.force_login(user)

    assert BANNER_MARKER in client.get(reverse("accounts:profile")).content.decode()


@pytest.mark.django_db
@override_config(ZAUTH_BANNER_ENABLED=True)
def test_hidden_from_anonymous_visitors(client):
    assert BANNER_MARKER not in client.get("/").content.decode()


@pytest.mark.django_db
@override_config(ZAUTH_BANNER_ENABLED=True)
def test_the_configured_message_is_rendered(legacy_client):
    with override_config(ZAUTH_BANNER_MESSAGE="Time to **reconnect** your account"):
        body = legacy_client.get(reverse("accounts:profile")).content.decode()

    assert "reconnect" in body
    assert "<strong>reconnect</strong>" in body  # markdown rendered, not escaped


# --- dismissal ---------------------------------------------------------------


@pytest.mark.django_db
@override_config(ZAUTH_BANNER_ENABLED=True)
def test_dismissing_hides_it_for_the_rest_of_the_session(legacy_client):
    profile = reverse("accounts:profile")
    assert BANNER_MARKER in legacy_client.get(profile).content.decode()

    resp = legacy_client.post(reverse("accounts:dismiss_zauth_banner"))

    assert resp.status_code == 200
    assert legacy_client.session["zauth_banner_dismissed"] is True
    assert BANNER_MARKER not in legacy_client.get(profile).content.decode()


@pytest.mark.django_db
@override_config(ZAUTH_BANNER_ENABLED=True)
def test_dismissal_does_not_survive_a_new_session(legacy_client, client, user_model):
    """Session-scoped, so it returns next visit until the account is connected."""
    legacy_client.post(reverse("accounts:dismiss_zauth_banner"))
    legacy_client.logout()

    user = user_model.objects.get(username="legacy-user")
    client.force_login(user)

    assert BANNER_MARKER in client.get(reverse("accounts:profile")).content.decode()


@pytest.mark.django_db
def test_dismiss_requires_login_and_post(client):
    url = reverse("accounts:dismiss_zauth_banner")

    assert client.post(url).status_code in (302, 403)  # redirected to login

"""allauth's own signup is closed: an account comes from a Discord login or not at all.

Left open, an email-only POST to ``/accounts/signup/`` created a password-less account with no
``discord_id`` and logged it straight in, skipping the block list, guild membership and
Discord's verified-email check -- all of which live in ``pre_social_login``. With
``SOCIALACCOUNT_EMAIL_AUTHENTICATION`` and ``_AUTO_CONNECT`` on, that account would then
capture the first Discord login carrying the same email address.

What must keep working: signing up with Discord, and existing accounts, including the
superuser's username-and-password login to /admin/.
"""

import secrets

import pytest
from allauth.account.adapter import get_adapter as get_account_adapter
from allauth.socialaccount.adapter import get_adapter as get_social_adapter
from django.urls import reverse


@pytest.mark.django_db
def test_local_signup_creates_nothing(client, user_model):
    """The hole itself: an email-only POST must not create an account, or a session."""
    before = user_model.objects.count()

    response = client.post(reverse("account_signup"), {"email": "outsider@example.test"})

    assert not user_model.objects.filter(email="outsider@example.test").exists()
    assert user_model.objects.count() == before
    assert "_auth_user_id" not in client.session
    assert response.status_code in {200, 302, 403}


@pytest.mark.django_db
def test_the_signup_page_offers_no_form(client):
    """A visitor sees that signup is closed rather than a form the POST would refuse."""
    body = client.get(reverse("account_signup")).content.decode().lower()

    assert 'name="email"' not in body


@pytest.mark.django_db
def test_the_account_adapter_refuses_and_the_discord_one_does_not(rf):
    """Discord signup must stay open: allauth's social adapter delegates here by default."""
    request = rf.get("/")

    assert get_account_adapter(request).is_open_for_signup(request) is False
    assert get_social_adapter(request).is_open_for_signup(request, None) is True


@pytest.mark.django_db
def test_existing_accounts_still_sign_in_with_a_password(client, user_model):
    """Closing signup only stops new local accounts -- /admin/ still uses ModelBackend."""
    secret = secrets.token_urlsafe(16)
    staff = user_model.objects.create_user(
        username="staffer", email="staffer@example.test", password=secret, is_staff=True
    )

    assert client.login(username=staff.username, password=secret)
    assert client.get(reverse("admin:index")).status_code == 200

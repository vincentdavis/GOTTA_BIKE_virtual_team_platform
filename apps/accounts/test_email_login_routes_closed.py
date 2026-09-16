"""Discord is the only way in: allauth's email- and password-based routes are closed.

With ``ACCOUNT_LOGIN_METHODS = {"email"}`` and no password field, a POST of a bare email
address to ``/accounts/login/`` started allauth's login-by-code flow: a code was emailed and
``/accounts/login/code/confirm/`` signed the holder in -- no Discord, so no block list and no
guild check. Password reset and password set would likewise hand a password to an account
meant for Discord only. Those routes now 404 (``gotta_bike_platform/urls.py``), a POST to the
login page never reaches allauth, and ``NoLocalSignupAccountAdapter.pre_login`` refuses any
allauth login that was not a Discord social login.

What must keep working: the Discord button, TOTP setup and the TOTP step after a Discord
login, and the Django admin's own username-and-password login.
"""

import re
import secrets
import time

import pytest
from allauth.account.internal.flows.login import perform_login, perform_password_login
from allauth.account.models import Login
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, generate_totp_secret, hotp_value
from allauth.socialaccount.models import SocialAccount
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse

CLOSED_PATHS = [
    "/accounts/login/code/",
    "/accounts/login/code/confirm/",
    "/accounts/password/change/",
    "/accounts/password/set/",
    "/accounts/password/reset/",
    "/accounts/password/reset/done/",
    "/accounts/password/reset/key/done/",
    "/accounts/password/reset/key/1-set-password/",
    "/accounts/password/reset/confirm/",
    "/accounts/password/reset/complete/",
    "/accounts/email/",
    "/accounts/confirm-email/",
    "/accounts/confirm-email/some-key/",
]

DISCORD_ID = "710000000000000001"


@pytest.fixture(autouse=True)
def _locmem_mail(settings):
    """Mail that would really go out is captured, so "nothing was sent" means something."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture
def rider(db, user_model):
    """Make a Discord rider: no usable password, and a verified email allauth could send a code to.

    Returns:
        The user.

    """
    from allauth.account.models import EmailAddress

    user = user_model.objects.create_user(username="rider", email="rider@example.test", discord_id=DISCORD_ID)
    user.set_unusable_password()
    user.save()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


def _signed_in(client):
    return "_auth_user_id" in client.session


# --- closed routes ------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("path", CLOSED_PATHS)
@pytest.mark.parametrize("method", ["get", "post"])
def test_closed_routes_404_for_visitors(client, rider, path, method):
    response = getattr(client, method)(path, {"email": rider.email, "login": rider.email, "code": "123456"})

    assert response.status_code == 404
    assert not mail.outbox
    assert not _signed_in(client)


@pytest.mark.django_db
@pytest.mark.parametrize("path", CLOSED_PATHS)
def test_closed_routes_404_for_signed_in_riders(client, rider, path):
    client.force_login(rider)

    assert client.get(path).status_code == 404
    assert client.post(path, {"password1": "x", "password2": "x"}).status_code == 404


@pytest.mark.django_db
def test_a_password_cannot_be_set_on_a_discord_account(client, rider):
    client.force_login(rider)
    new_password = secrets.token_urlsafe(16)

    for name in ("account_set_password", "account_change_password"):
        response = client.post(reverse(name), {"password1": new_password, "password2": new_password})
        assert response.status_code == 404

    rider.refresh_from_db()
    assert not rider.has_usable_password()


@pytest.mark.django_db
def test_a_password_reset_sends_nothing(client, rider):
    response = client.post(reverse("account_reset_password"), {"email": rider.email})

    assert response.status_code == 404
    assert not mail.outbox


@pytest.mark.django_db
def test_allauth_url_names_still_reverse(client):
    """Templates (allauth's own included) may still name these routes; only the pages are gone."""
    for name in ("account_email", "account_change_password", "account_set_password", "account_reset_password"):
        assert client.get(reverse(name)).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name",
    ["account_logout", "mfa_index", "socialaccount_connections"],
)
def test_the_routes_that_stay_are_not_shadowed(client, rider, name):
    client.force_login(rider)

    assert client.get(reverse(name)).status_code != 404


CLOSED_PREFIXES = ("/accounts/login/code/", "/accounts/password/", "/accounts/email/", "/accounts/confirm-email/")


def _links(response):
    return re.findall(r'href="([^"]*)"', response.content.decode())


def _dead_links(response):
    return [href for href in _links(response) if href.startswith(CLOSED_PREFIXES)]


@pytest.fixture
def totp_rider(rider):
    """Give the Discord rider an authenticator app.

    Returns:
        ``(rider, totp_secret)``.

    """
    secret = generate_totp_secret()
    TOTP.activate(rider, secret)
    return rider, secret


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name",
    ["mfa_reauthenticate", "account_reauthenticate", "socialaccount_connections", "mfa_index", "account_inactive"],
)
def test_pages_still_open_link_to_no_closed_route(client, totp_rider, name):
    """The allauth layout offered "Change Email" and "Change Password" -- links to a 404."""
    client.force_login(totp_rider[0])

    response = client.get(reverse(name), follow=True)

    assert response.status_code == 200
    assert _links(response)
    assert _dead_links(response) == []


@pytest.mark.django_db
def test_the_closed_signup_page_links_to_no_closed_route(client):
    response = client.get(reverse("account_signup"))

    assert response.status_code == 200
    assert _dead_links(response) == []


@pytest.mark.django_db
def test_a_passwordless_rider_is_sent_to_the_site_styled_code_check(client, totp_rider):
    client.force_login(totp_rider[0])

    response = client.get(reverse("account_reauthenticate"), follow=True)

    assert response.redirect_chain[-1][0].startswith(reverse("mfa_reauthenticate"))
    body = response.content.decode()
    assert "Confirm Access" in body
    assert f'action="{reverse("mfa_reauthenticate")}"' in body
    assert 'for="id_code"' in body


@pytest.mark.django_db
def test_the_code_check_still_confirms_access(client, totp_rider):
    rider, secret = totp_rider
    client.force_login(rider)
    code = format_hotp_value(hotp_value(secret, int(time.time()) // 30))

    response = client.post(reverse("mfa_reauthenticate"), {"code": code, "next": reverse("mfa_index")})

    assert response.status_code == 302
    assert response["Location"] == reverse("mfa_index")


@pytest.mark.django_db
def test_a_wrong_code_is_shown_as_an_error_on_the_styled_page(client, totp_rider):
    client.force_login(totp_rider[0])

    response = client.post(reverse("mfa_reauthenticate"), {"code": "000000"})

    assert response.status_code == 200
    body = response.content.decode()
    assert 'aria-invalid="true"' in body
    assert 'id="id_code_error"' in body
    assert _dead_links(response) == []


# --- the login page -----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_login_page_still_offers_discord(client):
    response = client.get(reverse("account_login"))

    assert response.status_code == 200
    body = response.content.decode()
    assert reverse("discord_login") in body
    assert "<form" not in body


@pytest.mark.django_db
def test_posting_an_email_to_the_login_page_starts_no_code_login(client, rider):
    response = client.post(reverse("account_login"), {"login": rider.email})

    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    assert not mail.outbox
    assert not _signed_in(client)
    # allauth stashes a pending login under this key when a code flow starts.
    assert "account_login" not in client.session


@pytest.mark.django_db
def test_posting_to_the_login_page_keeps_the_next_parameter(client):
    url = f"{reverse('account_login')}?next=/team/links/"

    response = client.post(url, {"login": "someone@example.test"})

    assert response["Location"] == url


@pytest.mark.django_db
def test_the_discord_button_route_is_a_different_route(client, discord_app):
    response = client.get(reverse("discord_login"))

    assert response.status_code == 302
    assert response["Location"].startswith("https://discord.com/")


# --- defence in depth at the account adapter ------------------------------------------


def _request_with_session():
    request = RequestFactory().post("/accounts/login/")
    SessionMiddleware(lambda r: None).process_request(request)
    MessageMiddleware(lambda r: None).process_request(request)
    return request


@pytest.mark.django_db
def test_the_account_adapter_refuses_a_login_that_is_not_social(rider):
    """What a login by emailed code (or by password reset) would reach once its code checked out."""
    request = _request_with_session()

    response = perform_login(request, Login(user=rider, email=rider.email))

    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    assert "_auth_user_id" not in request.session


@pytest.mark.django_db
def test_the_account_adapter_refuses_an_allauth_password_login(user_model):
    """Even a staff account with a real password cannot log in through allauth's flow."""
    secret = secrets.token_urlsafe(16)
    staff = user_model.objects.create_user(
        username="staffer", email="staffer@example.test", password=secret, is_staff=True
    )
    request = _request_with_session()

    response = perform_password_login(
        request, {"email": staff.email, "password": secret}, Login(user=staff, email=staff.email)
    )

    assert response["Location"] == reverse("account_login")
    assert "_auth_user_id" not in request.session


@pytest.mark.django_db
def test_the_account_adapter_refuses_a_non_discord_sociallogin(rider, make_sociallogin):
    request = _request_with_session()
    sociallogin = make_sociallogin(DISCORD_ID, user=rider)
    sociallogin.account.provider = "google"

    response = perform_login(request, Login(user=rider, signal_kwargs={"sociallogin": sociallogin}))

    assert response["Location"] == reverse("account_login")
    assert "_auth_user_id" not in request.session


# --- what must keep working -----------------------------------------------------------


@pytest.mark.django_db
def test_a_discord_login_with_totp_completes_after_the_code(client, discord_login, rider):
    SocialAccount.objects.create(user=rider, provider="discord", uid=DISCORD_ID, extra_data={})
    totp_secret = generate_totp_secret()
    TOTP.activate(rider, totp_secret)

    response = discord_login(client, DISCORD_ID)

    # Discord passed; the TOTP stage holds the login until the code is in.
    assert response["Location"] == reverse("mfa_authenticate")
    assert not _signed_in(client)
    assert client.get(reverse("mfa_authenticate")).status_code == 200

    code = format_hotp_value(hotp_value(totp_secret, int(time.time()) // 30))
    response = client.post(reverse("mfa_authenticate"), {"code": code})

    assert response.status_code == 302
    assert int(client.session["_auth_user_id"]) == rider.pk


@pytest.mark.django_db
def test_a_wrong_totp_code_does_not_sign_in(client, discord_login, rider):
    SocialAccount.objects.create(user=rider, provider="discord", uid=DISCORD_ID, extra_data={})
    TOTP.activate(rider, generate_totp_secret())

    discord_login(client, DISCORD_ID)
    client.post(reverse("mfa_authenticate"), {"code": "000000"})

    assert not _signed_in(client)


@pytest.mark.django_db
def test_a_discord_rider_can_still_set_up_totp(client, rider):
    client.force_login(rider)

    assert client.get(reverse("mfa_activate_totp")).status_code == 200
    secret = client.session["mfa.totp.secret"]
    code = format_hotp_value(hotp_value(secret, int(time.time()) // 30))
    response = client.post(reverse("mfa_activate_totp"), {"code": code})

    assert response.status_code == 302
    assert Authenticator.objects.filter(user=rider, type=Authenticator.Type.TOTP).exists()


@pytest.mark.django_db
def test_the_admin_login_still_takes_a_password(client, user_model):
    secret = secrets.token_urlsafe(16)
    staff = user_model.objects.create_user(
        username="staffer", email="staffer@example.test", password=secret, is_staff=True
    )

    response = client.post(
        reverse("admin:login"), {"username": staff.username, "password": secret, "next": reverse("admin:index")}
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:index")
    assert int(client.session["_auth_user_id"]) == staff.pk
    assert client.get(reverse("admin:index")).status_code == 200


@pytest.mark.django_db
def test_allauth_system_checks_stay_clean():
    call_command("check", fail_level="WARNING")

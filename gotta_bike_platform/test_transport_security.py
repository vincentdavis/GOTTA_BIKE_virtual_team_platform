"""Tests for HSTS and the CSRF origins derived from ALLOWED_HOSTS.

CSRF_TRUSTED_ORIGINS is derived rather than configured so it cannot fall out of step with
ALLOWED_HOSTS. These tests pin the derivation, including the three forms that are easy to get
wrong: a leading-dot wildcard, a bare ``*``, and loopback hosts that are not served over TLS.
"""

import os

import pytest

from gotta_bike_platform.config import Settings


def _origins(hosts: str) -> list[str]:
    return Settings(ALLOWED_HOSTS=hosts).csrf_trusted_origins


def test_production_host_becomes_an_https_origin():
    assert _origins("app.coalitionracing.com") == ["https://app.coalitionracing.com"]


def test_every_origin_carries_a_scheme():
    """Django 4.0+ rejects CSRF_TRUSTED_ORIGINS entries without one."""
    for origin in _origins("app.coalitionracing.com,.up.railway.app,localhost"):
        assert "://" in origin


def test_leading_dot_host_becomes_a_subdomain_wildcard():
    """ALLOWED_HOSTS spells a wildcard '.example.com'; an origin spells it 'https://*.example.com'."""
    assert _origins(".up.railway.app") == ["https://*.up.railway.app"]


def test_bare_wildcard_is_skipped():
    """'*' is a valid ALLOWED_HOSTS entry but has no origin form; emitting 'https://*' would be invalid."""
    assert _origins("*,app.coalitionracing.com") == ["https://app.coalitionracing.com"]


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_loopback_hosts_use_http(host):
    """Local dev is not served over TLS, so an https origin there would never match."""
    assert _origins(host) == [f"http://{host}"]


def test_hsts_defaults_to_a_year_and_is_overridable():
    assert Settings().hsts_seconds == 31536000
    assert Settings(SECURE_HSTS_SECONDS=3600).hsts_seconds == 3600


def test_production_block_sets_hsts_and_leaves_preload_off():
    """The production settings only apply when DEBUG is False, so assert them in a subprocess.

    Local dev runs with DEBUG=True, where Django's own defaults (SECURE_HSTS_SECONDS = 0) mask
    whether our block ran at all -- an in-process assertion would pass whether or not the
    settings exist. Preload is deliberately off: it covers the whole apex domain, including
    sibling subdomains this app does not own, and takes months to reverse.
    """
    import json
    import subprocess  # noqa: S404 -- fixed interpreter, literal script, no user input
    import sys

    script = (
        "import django, os;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gotta_bike_platform.settings');"
        "django.setup();"
        "from django.conf import settings;"
        "import json, sys;"
        "sys.stdout.write('@@' + json.dumps({"
        "'hsts': settings.SECURE_HSTS_SECONDS,"
        "'subdomains': settings.SECURE_HSTS_INCLUDE_SUBDOMAINS,"
        "'preload': settings.SECURE_HSTS_PRELOAD,"
        "'origins': settings.CSRF_TRUSTED_ORIGINS,"
        "'ssl_redirect': settings.SECURE_SSL_REDIRECT,"
        "}))"
    )
    env = {
        **os.environ,
        "DEBUG": "False",
        "SECRET_KEY": "t" * 60,
        "ALLOWED_HOSTS": "app.coalitionracing.com",
    }
    out = subprocess.run(  # noqa: S603 -- argv is sys.executable plus a literal script
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True
    )
    data = json.loads(out.stdout.split("@@", 1)[1])

    assert data["hsts"] == 31536000
    assert data["subdomains"] is True
    assert data["preload"] is False, "preload is a domain-level commitment, not an app setting"
    assert data["ssl_redirect"] is True
    assert data["origins"] == ["https://app.coalitionracing.com"]


def _allowed_hosts_with(private_domain: str) -> list[str]:
    """Resolve ALLOWED_HOSTS in a subprocess with RAILWAY_PRIVATE_DOMAIN set to ``private_domain``.

    ALLOWED_HOSTS is computed at import, so it cannot be re-derived by overriding a setting.

    Args:
        private_domain: Value to place in the environment; "" to leave it unset.

    Returns:
        The resolved ALLOWED_HOSTS list.

    """
    import json
    import subprocess  # noqa: S404 -- fixed interpreter, literal script, no user input
    import sys

    script = (
        "import django, os;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gotta_bike_platform.settings');"
        "django.setup();"
        "from django.conf import settings;"
        "import json, sys;"
        "sys.stdout.write('@@' + json.dumps(settings.ALLOWED_HOSTS))"
    )
    env = {
        **os.environ,
        "DEBUG": "False",
        "SECRET_KEY": "t" * 60,
        "ALLOWED_HOSTS": "app.coalitionracing.com",
        "RAILWAY_PRIVATE_DOMAIN": private_domain,
    }
    out = subprocess.run(  # noqa: S603 -- argv is sys.executable plus a literal script
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True
    )
    return json.loads(out.stdout.split("@@", 1)[1])


def test_the_railway_private_domain_is_allowed_automatically():
    """Traffic over Railway's private network arrives with the internal hostname in Host.

    Without this the bot's calls fail as DisallowedHost — a 400 that looks nothing like the
    networking change that caused it.
    """
    assert _allowed_hosts_with("coalitionapp.railway.internal") == [
        "app.coalitionracing.com",
        "coalitionapp.railway.internal",
    ]


def test_nothing_is_added_when_not_running_on_railway():
    """Local and CI runs have no private domain, and must not gain a phantom entry."""
    assert _allowed_hosts_with("") == ["app.coalitionracing.com"]


def test_the_port_on_the_internal_host_header_does_not_defeat_the_match():
    """The bot addresses the service by port, so Host is 'name:8080', not 'name'.

    Django splits the port off before matching, which is why the bare hostname is the right
    thing to allow — asserted here because getting it wrong fails only in production.
    """
    from django.http.request import split_domain_port, validate_host

    domain, port = split_domain_port("coalitionapp.railway.internal:8080")
    assert domain == "coalitionapp.railway.internal"
    assert port == "8080"
    assert validate_host(domain, ["coalitionapp.railway.internal"])


class TestPrivateNetworkSslRedirect:
    """The HTTPS redirect must skip private-network traffic and nothing else.

    Railway terminates TLS at its edge, which internal traffic bypasses, so a call to
    *.railway.internal arrives as plain HTTP. Redirecting it produces a 301 the caller cannot
    usefully follow — the symptom that broke the Discord bot.
    """

    PRIVATE = "coalitionapp.railway.internal"

    def _middleware(self):
        """Build the middleware with a trivial get_response.

        Returns:
            The configured middleware instance.

        """
        from django.http import HttpResponse

        from gotta_bike_platform.middleware import PrivateNetworkAwareSecurityMiddleware

        return PrivateNetworkAwareSecurityMiddleware(lambda _request: HttpResponse("ok"))

    def _request(self, host):
        """Build a plain-HTTP GET against a bot API path.

        Args:
            host: Value for the Host header.

        Returns:
            The request.

        """
        from django.test import RequestFactory

        return RequestFactory().get("/api/dbot/my_profile", HTTP_HOST=host)

    def test_private_host_over_http_is_not_redirected(self):
        """The bot calls over plain HTTP internally; a 301 there is what broke it."""
        from django.test import override_settings

        with override_settings(
            SECURE_SSL_REDIRECT=True,
            RAILWAY_PRIVATE_DOMAIN=self.PRIVATE,
            ALLOWED_HOSTS=["app.coalitionracing.com", self.PRIVATE],
        ):
            assert self._middleware().process_request(self._request(f"{self.PRIVATE}:8080")) is None

    def test_public_host_over_http_is_still_redirected(self):
        """The exemption must not weaken the public site.

        This is why the match is on host rather than the path-based SECURE_REDIRECT_EXEMPT,
        which would also have exempted public calls carrying the bot API key.
        """
        from django.test import override_settings

        with override_settings(
            SECURE_SSL_REDIRECT=True,
            RAILWAY_PRIVATE_DOMAIN=self.PRIVATE,
            ALLOWED_HOSTS=["app.coalitionracing.com", self.PRIVATE],
        ):
            response = self._middleware().process_request(self._request("app.coalitionracing.com"))
        assert response is not None, "public HTTP must still be redirected to HTTPS"
        assert response.status_code in (301, 302)
        assert response["Location"].startswith("https://")

    def test_nothing_is_exempt_when_not_running_on_railway(self):
        """With no private domain configured the middleware must behave exactly as Django's."""
        from django.test import override_settings

        with override_settings(
            SECURE_SSL_REDIRECT=True, RAILWAY_PRIVATE_DOMAIN="", ALLOWED_HOSTS=["*"]
        ):
            response = self._middleware().process_request(self._request(self.PRIVATE))
        assert response is not None and response.status_code in (301, 302)

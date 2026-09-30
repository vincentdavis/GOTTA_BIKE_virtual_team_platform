"""Admin-set addresses become links only when they are http(s).

The footer is on every page and put ``DISCORD_URL`` and the ``SOCIAL_*_URL`` settings
straight into ``href``, so a ``javascript:`` value was a script link for every visitor.
"""

import pytest
from constance.test import override_config
from django.urls import reverse

from gotta_bike_platform.url_utils import http_url

HOSTILE = [
    "javascript:alert(1)",
    " javascript:alert(1)",
    "JAVASCRIPT:alert(1)",
    "\tjavascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://discord.gg/coalition", "https://discord.gg/coalition"),
        ("http://example.test/x", "http://example.test/x"),
        ("  https://discord.gg/x  ", "https://discord.gg/x"),
        ("HTTPS://discord.gg/X", "HTTPS://discord.gg/X"),
        ("", ""),
        (None, ""),
        ("#", ""),
        ("/relative/path", ""),
        ("discord.gg/coalition", ""),  # no scheme: a browser would read it as a relative path
        *[(value, "") for value in HOSTILE],
    ],
)
def test_http_url_passes_only_http_addresses(value, expected):
    assert http_url(value) == expected


SOCIAL = {
    "SOCIAL_LINKEDIN_URL": "LinkedIn",
    "SOCIAL_FACEBOOK_URL": "Facebook",
    "SOCIAL_INSTAGRAM_URL": "Instagram",
    "SOCIAL_TWITTER_URL": "Twitter/X",
}


def _footer(client) -> str:
    body = client.get(reverse("login_help")).content.decode()
    return body[body.index("<footer") :]


@pytest.mark.django_db
@pytest.mark.parametrize("hostile", HOSTILE)
def test_the_footer_never_links_a_script_address(client, hostile):
    with override_config(DISCORD_URL=hostile, **dict.fromkeys(SOCIAL, hostile)):
        footer = _footer(client)

    assert "javascript:" not in footer.lower()
    assert "data:text" not in footer
    assert "vbscript:" not in footer
    for label in ("Discord", *SOCIAL.values()):
        assert f'aria-label="{label}"' not in footer  # left out, not emptied


@pytest.mark.django_db
def test_the_footer_links_real_addresses(client):
    addresses = {key: f"https://example.test/{key.lower()}" for key in SOCIAL}
    with override_config(DISCORD_URL="https://discord.gg/coalition", **addresses):
        footer = _footer(client)

    assert 'href="https://discord.gg/coalition" aria-label="Discord"' in footer
    for key, label in SOCIAL.items():
        assert f'href="{addresses[key]}" target="_blank" rel="noopener" aria-label="{label}"' in footer


@pytest.mark.django_db
def test_an_unset_discord_address_leaves_no_empty_link(client):
    """It used to render ``href=""``, a link back to the page the visitor was already on."""
    with override_config(DISCORD_URL=""):
        footer = _footer(client)

    assert 'aria-label="Discord"' not in footer
    assert 'href=""' not in footer

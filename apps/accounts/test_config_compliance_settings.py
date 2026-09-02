"""The Compliance section's settings, and the fact that they render on the Compliance page.

The retention windows and policy URLs used to sit under Site Settings, between the team name
and the announcement banner, where "how long is analytics data kept" read as site furniture.
They now sit with the erasure and blocked-login tools, which act on the same data and are what
an admin is looking at when someone asks what is kept and for how long.

Compliance is not an ordinary section: the view special-cases it and the template swaps in its
own partial, so the settings form only appears there because the partial explicitly includes
it. That is exactly the kind of wiring a configuration-only test would miss, so these render
the page and post to it.
"""

import pytest
from constance import config
from django.conf import settings
from django.urls import reverse

MOVED = ("PRIVACY_POLICY_URL", "TERMS_OF_SERVICE_URL", "ANALYTICS_ANONYMISE_DAYS", "ANALYTICS_DELETE_DAYS")


def test_the_compliance_fieldset_holds_exactly_the_moved_settings():
    """Names the intended contents, so an unrelated key landing here is a deliberate edit."""
    assert settings.CONSTANCE_CONFIG_FIELDSETS["Compliance"] == MOVED


def test_the_moved_settings_left_site_settings():
    """A key in two fieldsets renders twice and saves twice; the move has to be a move."""
    for key in MOVED:
        assert key not in settings.CONSTANCE_CONFIG_FIELDSETS["Site Settings"], f"{key} still in Site Settings"


def test_no_setting_appears_in_two_fieldsets():
    """The general form of the rule above, so the next move cannot half-happen either."""
    seen: dict[str, str] = {}
    for section, keys in settings.CONSTANCE_CONFIG_FIELDSETS.items():
        for key in keys:
            assert key not in seen, f"{key} is in both {seen.get(key)} and {section}"
            seen[key] = section


def test_every_fieldset_key_is_a_real_setting():
    """A typo in a fieldset renders nothing and fails silently rather than erroring."""
    for section, keys in settings.CONSTANCE_CONFIG_FIELDSETS.items():
        for key in keys:
            assert key in settings.CONSTANCE_CONFIG, f"{section} lists {key}, which is not a setting"


@pytest.mark.django_db
def test_the_compliance_page_renders_the_settings(admin_authed_client):
    """The point of the move: these are editable on the page that acts on the same data."""
    response = admin_authed_client.get(reverse("config_section_page", args=["compliance"]))
    assert response.status_code == 200

    body = response.content.decode()
    for key in MOVED:
        assert key in body, f"{key} is not on the Compliance page"


@pytest.mark.django_db
def test_the_compliance_page_keeps_its_tools(admin_authed_client):
    """Adding a form must not displace the erasure tool, which is why the page exists."""
    body = admin_authed_client.get(reverse("config_section_page", args=["compliance"])).content.decode()

    assert "Delete a member&#x27;s account" in body or "Delete a member's account" in body
    assert "Blocked logins" in body


@pytest.mark.django_db
def test_the_settings_form_has_something_to_swap_into(admin_authed_client):
    """The shared partial posts into #section-<key> .section-content, which this page supplies."""
    body = admin_authed_client.get(reverse("config_section_page", args=["compliance"])).content.decode()

    assert 'id="section-compliance"' in body
    assert 'class="section-content"' in body


@pytest.mark.django_db
def test_the_moved_settings_are_gone_from_the_site_settings_form(admin_authed_client):
    """Scoped to the form: the page chrome mentions plenty that is not a setting."""
    body = admin_authed_client.get(reverse("config_section_page", args=["site_settings"])).content.decode()
    start = body.index("<form hx-post")
    form = body[start : body.index("</form>", start)]

    for key in MOVED:
        assert key not in form, f"{key} still renders under Site Settings"


@pytest.mark.django_db
def test_saving_from_the_compliance_page_persists_every_setting(admin_authed_client):
    """A section the view special-cases still has to post back through the ordinary handler."""
    response = admin_authed_client.post(
        reverse("config_section_update", args=["compliance"]),
        {
            "PRIVACY_POLICY_URL": "https://example.test/privacy",
            "TERMS_OF_SERVICE_URL": "https://example.test/terms",
            "ANALYTICS_ANONYMISE_DAYS": "45",
            "ANALYTICS_DELETE_DAYS": "400",
        },
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200

    assert config.PRIVACY_POLICY_URL == "https://example.test/privacy"
    assert config.TERMS_OF_SERVICE_URL == "https://example.test/terms"
    assert config.ANALYTICS_ANONYMISE_DAYS == 45
    assert config.ANALYTICS_DELETE_DAYS == 400


@pytest.mark.django_db
def test_the_save_returns_the_form_alone_not_the_whole_compliance_page(admin_authed_client):
    """Compliance is special-cased on GET but not on POST, and that asymmetry is the point.

    The form swaps into ``#section-compliance .section-content``. If the POST handler were
    ever special-cased the way the GET is -- returning the whole compliance page -- the swap
    would nest the erasure tool and the blocked-logins table inside the settings form's own
    container, once per save.

    Asserted as a contrast between the two responses rather than as a bare absence, so the
    test cannot pass by finding nothing: the GET is checked to contain the tools in the same
    breath as the POST is checked not to.
    """
    page = admin_authed_client.get(reverse("config_section_page", args=["compliance"])).content.decode()
    fragment = admin_authed_client.post(
        reverse("config_section_update", args=["compliance"]),
        {
            "PRIVACY_POLICY_URL": "",
            "TERMS_OF_SERVICE_URL": "",
            "ANALYTICS_ANONYMISE_DAYS": "30",
            "ANALYTICS_DELETE_DAYS": "365",
        },
        headers={"hx-request": "true"},
    ).content.decode()

    # Both render the settings form...
    assert "ANALYTICS_DELETE_DAYS" in page
    assert "ANALYTICS_DELETE_DAYS" in fragment

    # ...but only the full page carries the tools that live outside the swap target.
    assert "Blocked logins" in page
    assert "Blocked logins" not in fragment
    assert 'id="section-compliance"' in page
    assert 'id="section-compliance"' not in fragment

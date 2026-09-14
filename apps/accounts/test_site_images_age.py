"""The age bracket icons are the one family that ships defaults, so the config page differs.

An empty upload slot here is the normal, working state rather than a gap, and the page has to
say so -- otherwise every bracket reads as missing on a page whose whole job is showing what
is set.
"""

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from gotta_bike_platform.models import SiteSettings


@pytest.fixture(autouse=True)
def _clear_cache():
    """SiteSettings memoises into a process-wide LocMemCache that pytest-django never resets."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_the_page_offers_an_upload_for_every_bracket(admin_authed_client):
    from apps.accounts.templatetags.accounts_tags import AGE_EMOJI_FIELDS

    body = admin_authed_client.get(reverse("config_section_page", args=["site_images"])).content.decode()

    assert "Age brackets" in body
    for field in AGE_EMOJI_FIELDS.values():
        assert f'name="{field}"' in body, f"{field} has no upload control"


@pytest.mark.django_db
def test_a_bracket_with_no_upload_shows_the_default_rather_than_an_empty_slot(admin_authed_client):
    body = admin_authed_client.get(reverse("config_section_page", args=["site_images"])).content.decode()

    assert "age-vet.svg" in body
    assert "Showing the bundled default" in body


@pytest.mark.django_db
def test_an_uploaded_icon_is_shown_with_a_way_to_restore_the_default(admin_authed_client, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    SiteSettings.get_settings().age_vet_emoji.save("mine.png", ContentFile(b"x"), save=True)

    body = admin_authed_client.get(reverse("config_section_page", args=["site_images"])).content.decode()

    assert "Custom icon uploaded" in body
    assert "delete_age_vet_emoji" in body

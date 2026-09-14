"""The team kit icon on the site images page.

Like the age brackets it ships a default, so an empty slot is the working state rather than
a gap. Unlike them there is one slot rather than eight: both statuses that mean the kit is
settled share a drawing. The upload round trip is tested here because the page's POST handler
works off a hand-maintained field list, which is exactly the kind of thing a new field is
left out of.
"""

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
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
def test_the_page_offers_the_kit_upload_showing_the_bundled_default(admin_authed_client):
    body = admin_authed_client.get(reverse("config_section_page", args=["site_images"])).content.decode()

    assert "Team kit" in body
    assert 'name="kit_emoji"' in body
    assert "accounts/kit/kit.svg" in body


@pytest.mark.django_db
def test_uploading_a_kit_icon_stores_it(admin_authed_client, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)

    admin_authed_client.post(
        reverse("config_site_images_update"),
        {"kit_emoji": SimpleUploadedFile("jersey.png", b"x", content_type="image/png")},
    )

    assert SiteSettings.get_settings().kit_emoji


@pytest.mark.django_db
def test_deleting_an_uploaded_kit_icon_puts_the_default_back(admin_authed_client, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    SiteSettings.get_settings().kit_emoji.save("jersey.png", ContentFile(b"x"), save=True)

    body = admin_authed_client.post(
        reverse("config_site_images_update"), {"delete_kit_emoji": "true"}
    ).content.decode()

    assert not SiteSettings.get_settings().kit_emoji
    # And the row goes back to offering the bundled artwork, rather than an empty frame.
    assert "accounts/kit/kit.svg" in body

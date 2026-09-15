"""A kit carries its own icon, and that is what the roster card flies.

Three sources, narrowest first: the current kit's own upload, the site-wide kit icon, then
the bundled jersey. Per kit is the point -- a new kit each year can have its own picture
while the previous one keeps the artwork riders earned it in.

Two rules ride along with it. The icon means "nothing left to chase", so it appears only for
the settled statuses; a jersey beside "Needs kit" would contradict the words next to it. And
only the CURRENT kit's icon is ever used, because that is the only kit the card reports on.
"""

import base64
import re

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.team.models import TeamKit
from conftest import _make_user

# A real 1x1 PNG, not just the signature: ImageField opens and verifies what it is given, so
# a plausible-looking byte string is rejected by the form and the upload silently does nothing
# -- which is exactly how the first draft of these tests "passed" the wrong way round.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """SiteSettings memoises into a LocMemCache pytest-django never resets between tests."""
    cache.clear()
    yield
    cache.clear()


def _card_for(body, name):
    """Return one rider's card markup.

    Returns:
        The markup of the card carrying that name.

    """
    return next(chunk for chunk in body.split('class="card bg-base-100') if name in chunk)


def _member(user_model, username, zwid):
    user = _make_user(user_model, username=username, permissions={"team_member": True})
    user.zwid = zwid
    user.zwid_verified = True
    user.zwid_verification_method = "zauth"
    user.discord_id = f"90000{zwid}"
    user.discord_username = username
    user.save()
    return user


def _kitted(user_model, zwid, status, slug="race-2027"):
    user = _member(user_model, f"r{zwid}", zwid)
    user.team_kit = {slug: status}
    user.save(update_fields=["team_kit"])
    return user


@pytest.fixture
def kit_with_icon(db, settings, tmp_path) -> TeamKit:
    """Build the current kit, carrying its own artwork.

    Returns:
        The kit.

    """
    settings.MEDIA_ROOT = str(tmp_path)
    kit = TeamKit.objects.create(name="2027 Race Kit", slug="race-2027", is_current=True)
    kit.icon.save("kit-2027.png", ContentFile(PNG), save=True)
    return kit


# --- what the card flies ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_kits_own_icon_is_what_a_settled_rider_flies(auth_client, roster_rider, user_model, kit_with_icon):
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert kit_with_icon.icon.url in card
    # Replaced, not joined: the bundled jersey must not also be drawn.
    assert "accounts/kit/kit.svg" not in card


@pytest.mark.django_db
def test_a_completed_order_flies_it_too(auth_client, roster_rider, user_model, kit_with_icon):
    """Both settled statuses, one picture -- the wording still tells them apart."""
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, 4242, "completed")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert kit_with_icon.icon.url in card
    assert 'alt="Kit: Completed by Zwift"' in card


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["need", "submitted"])
def test_a_kit_still_being_chased_flies_nothing(auth_client, roster_rider, user_model, kit_with_icon, status):
    """The icon says "sorted". Next to "Needs kit" it would say the opposite of the words."""
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, 4242, status)

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert kit_with_icon.icon.url not in card
    assert "Kit:" in re.sub(r"<[^>]+>", " ", card)


@pytest.mark.django_db
def test_only_the_current_kits_icon_is_used(auth_client, roster_rider, user_model, settings, tmp_path):
    """A rider carries a status per kit; the card is about the one the team is in now."""
    settings.MEDIA_ROOT = str(tmp_path)
    old = TeamKit.objects.create(name="2026", slug="race-2026")
    old.icon.save("old.png", ContentFile(PNG), save=True)
    current = TeamKit.objects.create(name="2027", slug="race-2027", is_current=True)
    current.icon.save("new.png", ContentFile(PNG), save=True)

    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "ada", 4242)
    user.team_kit = {"race-2026": "have", "race-2027": "have"}
    user.save(update_fields=["team_kit"])

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert current.icon.url in card
    assert old.icon.url not in card


@pytest.mark.django_db
def test_a_kit_with_no_icon_falls_back_to_the_bundled_jersey(auth_client, roster_rider, user_model):
    """Most kits will never have one, and the card must not come out blank."""
    TeamKit.objects.create(name="2027 Race Kit", slug="race-2027", is_current=True)
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert "accounts/kit/kit.svg" in card


@pytest.mark.django_db
def test_the_site_wide_icon_still_wins_over_the_bundled_one(auth_client, roster_rider, user_model, settings, tmp_path):
    """The middle rung of the ladder, which the kit's own icon must not have knocked out."""
    from gotta_bike_platform.models import SiteSettings

    settings.MEDIA_ROOT = str(tmp_path)
    site_settings = SiteSettings.get_settings()
    site_settings.kit_emoji.save("site.png", ContentFile(PNG), save=True)
    TeamKit.objects.create(name="2027 Race Kit", slug="race-2027", is_current=True)
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert site_settings.kit_emoji.url in card
    assert "accounts/kit/kit.svg" not in card


@pytest.mark.django_db
def test_the_kits_icon_outranks_the_site_wide_one(auth_client, roster_rider, user_model, kit_with_icon):
    """Narrowest wins, or uploading a kit icon would appear to do nothing."""
    from gotta_bike_platform.models import SiteSettings

    site_settings = SiteSettings.get_settings()
    site_settings.kit_emoji.save("site.png", ContentFile(PNG), save=True)
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert kit_with_icon.icon.url in card
    assert site_settings.kit_emoji.url not in card


@pytest.mark.django_db
def test_the_kit_icon_still_sits_left_of_the_flag(auth_client, roster_rider, user_model, kit_with_icon):
    """Vincent's placement, which the new source must not have moved."""
    roster_rider(zwid=4242, name="Ada Racer", country="us")
    _kitted(user_model, 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")
    badges = card.split("<ul", 1)[1].split("</ul>", 1)[0]

    assert badges.index(kit_with_icon.icon.url) < badges.index("flags")


# --- uploading one ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_icon_posted_with_a_new_kit_is_stored(client, app_admin, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    client.force_login(app_admin)

    client.post(
        reverse("team_kit_add"),
        {
            "name": "2027 Race Kit",
            "slug": "race-2027",
            "sort_order": 0,
            "icon": SimpleUploadedFile("kit.png", PNG, content_type="image/png"),
        },
    )

    assert TeamKit.objects.get(slug="race-2027").icon


@pytest.mark.django_db
def test_an_icon_can_be_added_to_an_existing_kit(client, app_admin, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    kit = TeamKit.objects.create(name="2027 Race Kit", slug="race-2027")
    client.force_login(app_admin)

    client.post(
        reverse("team_kit_edit", args=[kit.pk]),
        {
            "name": kit.name,
            "sort_order": 0,
            "icon": SimpleUploadedFile("kit.png", PNG, content_type="image/png"),
        },
    )

    kit.refresh_from_db()
    assert kit.icon


@pytest.mark.django_db
def test_an_icon_can_be_removed_again(client, app_admin, kit_with_icon):
    """Django's own ClearableFileInput name, so removal needs no route of its own."""
    client.force_login(app_admin)

    client.post(
        reverse("team_kit_edit", args=[kit_with_icon.pk]),
        {"name": kit_with_icon.name, "sort_order": 0, "icon-clear": "1"},
    )

    kit_with_icon.refresh_from_db()
    assert not kit_with_icon.icon


@pytest.mark.django_db
def test_saving_a_kit_without_touching_the_icon_keeps_it(client, app_admin, kit_with_icon):
    """An empty file input must not read as "remove it" -- editing the name would wipe it."""
    client.force_login(app_admin)

    client.post(
        reverse("team_kit_edit", args=[kit_with_icon.pk]),
        {"name": "Renamed", "sort_order": 0},
    )

    kit_with_icon.refresh_from_db()
    assert kit_with_icon.icon
    assert kit_with_icon.name == "Renamed"


@pytest.mark.django_db
def test_both_kit_forms_can_carry_a_file(client, app_admin, kit_with_icon):
    """A form without enctype posts the filename and no file, silently.

    Nothing about the page looks wrong when this is missing: the request arrives, the kit
    saves, and the icon is simply never there. Worth pinning in the markup.
    """
    client.force_login(app_admin)

    body = client.get(reverse("config_section_page", args=["team_kit"])).content.decode()
    forms_with_icon = [
        chunk for chunk in body.split("<form")[1:] if 'name="icon"' in chunk.split("</form>", 1)[0]
    ]

    assert len(forms_with_icon) == 2, "expected the add dialog and the edit form"
    for form in forms_with_icon:
        assert 'enctype="multipart/form-data"' in form.split(">", 1)[0]


# --- the two lists that have to agree ---------------------------------------------------------


def test_the_settled_statuses_match_the_icon_map():
    """Two modules decide which statuses earn a jersey; they must decide the same thing."""
    from apps.accounts.templatetags.accounts_tags import KIT_EMOJI_FIELDS
    from apps.team.rosterv2 import SETTLED_KIT_STATUSES

    assert set(KIT_EMOJI_FIELDS) == set(SETTLED_KIT_STATUSES)

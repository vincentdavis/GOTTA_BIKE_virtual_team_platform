"""What a rider's card renders, and what it must not turn into.

The index decides what MAY be shown; these pin what the page does with it. Two mistakes here
are invisible in review and wrong in opposite directions: printing an em dash over a real
zero, and printing a joined rider's identity over an unjoined rider's racing.
"""

import re

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from apps.team.views import ROSTER_PAGE_SIZE
from conftest import _make_user


def _card_for(body, name):
    """Return just one rider's card markup.

    Splitting on "</li>" does NOT work: the category badges are list items too, so it cuts
    the card off before the tiles and every assertion after that passes vacuously.

    Returns:
        The markup of the card carrying that name.

    """
    return next(chunk for chunk in body.split('class="card bg-base-100') if name in chunk)


def _text_of(markup):
    """Return the words a reader sees, with the markup between them removed.

    The figures carry their units in a nested span, so "0" and " W" are never adjacent in the
    source even though the card reads "0 W".

    Returns:
        The visible text, whitespace collapsed.

    """
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup)).strip()


def _member(user_model, username, zwid, **extra):
    user = _make_user(user_model, username=username, permissions={"team_member": True}, **extra)
    user.zwid = zwid
    user.zwid_verified = True
    user.zwid_verification_method = "zauth"
    user.discord_id = f"90000{zwid}"
    user.discord_username = username
    user.save()
    return user


@pytest.mark.django_db
def test_a_card_shows_the_riders_racing(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", velo=1642.0, ftp=286.0, wkg_20min=4.1, category_open="B")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "Ada Racer" in body
    assert "1642" in body  # vELO
    assert "286" in body  # zFTP watts
    assert "4.1" in body  # 20-minute w/kg
    assert "Cat B" in body


@pytest.mark.django_db
def test_a_joined_card_links_to_the_profile_and_names_the_rider(auth_client, roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "ada_discord", 4242)

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert reverse("accounts:public_profile", args=[user.pk]) in body
    assert "ada_discord" in body


@pytest.mark.django_db
def test_an_unjoined_card_says_so_and_links_nowhere(auth_client, roster_rider, user_model):
    """The failure this exists to catch is a card asserting a link the index refused to make."""
    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "impostor", 4242)
    user.zwid_verified = False
    user.save()

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "No account here" in body
    assert reverse("accounts:public_profile", args=[user.pk]) not in body
    assert "impostor" not in body
    assert "cdn.discordapp.com" not in body


@pytest.mark.django_db
def test_a_missing_figure_is_an_em_dash(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", velo=None, ftp=None, wkg_20min=None)

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert _text_of(_card_for(body, "Ada Racer")).count("—") == 3


@pytest.mark.django_db
def test_a_real_zero_is_printed_as_zero_not_as_missing(auth_client, roster_rider):
    """`{% if value %}` renders an em dash over a zero, and the model says a zero is data.

    `|default_if_none` cannot fix it either: floatformat turns None into "", so the default
    never fires and the em dash never appears at all. Both bugs are silent.
    """
    roster_rider(zwid=4242, name="Zero Rider", velo=0.0, ftp=0.0, wkg_20min=0.0)

    body = auth_client.get(reverse("team:roster")).content.decode()
    card = _text_of(_card_for(body, "Zero Rider"))

    assert "—" not in card
    assert "0 W" in card
    assert "0.0 w/kg" in card


@pytest.mark.django_db
def test_the_card_does_not_date_a_riders_last_race(auth_client, roster_rider):
    """Vincent's call: "Races 90d" above already answers whether somebody is riding.

    The value is still on the card object -- the "Last raced" sort and the quiet-rider filter
    read it -- so this is about what is drawn, not about what is known.
    """
    roster_rider(zwid=4242, name="Ada Racer", days_since_race=3)

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))

    assert "Last raced" not in card
    assert "No race on record" not in card


@pytest.mark.django_db
def test_a_rider_with_no_race_on_record_says_nothing_about_it(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", days_since_race=None)

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))

    assert "No race on record" not in card


@pytest.mark.django_db
def test_lifetime_distance_survives_the_date_going(auth_client, roster_rider):
    """It shared the line with the date, so removing one had to not take the other."""
    # Lifetime distance rides in the payload's totals block, in METRES -- and it is misnamed
    # distance_km upstream, which is exactly why a test passing distance_km=41234 to the
    # factory sets nothing at all.
    roster_rider(zwid=4242, name="Ada Racer", totals={"distance_km": 41_234_000})

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))

    assert "41,234 km lifetime" in card


@pytest.mark.django_db
def test_a_rider_with_no_distance_gets_no_lifetime_line(auth_client, roster_rider):
    """The line used to always render, because the date half always had something to say."""
    roster_rider(zwid=4242, name="Ada Racer", totals={"distance_km": None})

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))

    assert "km lifetime" not in card


@pytest.mark.django_db
def test_a_hidden_age_bracket_leaves_no_trace_on_the_card(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", age="-")

    body = auth_client.get(reverse("team:roster")).content.decode()

    card = _card_for(body, "Ada Racer")

    assert "/accounts/age/" not in card
    assert "Age" not in _text_of(card)


@pytest.mark.django_db
def test_a_junior_bracket_is_shown_because_that_is_the_owners_call(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", age="Jnr")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert 'alt="Age Jnr"' in card
    assert "age-jnr.svg" in card


# --- paging and the empty state -----------------------------------------------------------


@pytest.mark.django_db
def test_an_empty_roster_shows_a_sentence_not_an_empty_grid(auth_client):
    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "No riders yet" in body
    assert "Roster pages" not in body


@pytest.mark.django_db
def test_one_page_of_riders_needs_no_paging_controls(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "Roster pages" not in body


@pytest.mark.django_db
def test_a_long_roster_is_paged_rather_than_sent_whole(auth_client, roster_rider):
    """v1 sends every row: ~450 KB of HTML at 100 riders."""
    for n in range(ROSTER_PAGE_SIZE + 5):
        roster_rider(zwid=5000 + n, name=f"Rider {n:03d}")

    first = auth_client.get(reverse("team:roster")).content.decode()
    second = auth_client.get(reverse("team:roster") + "?page=2").content.decode()

    assert first.count('class="card bg-base-100') == ROSTER_PAGE_SIZE
    assert second.count('class="card bg-base-100') == 5
    assert "Page 1 of 2" in first
    assert "Page 2 of 2" in second
    # The header counts the whole roster, not the page.
    assert f"{ROSTER_PAGE_SIZE + 5} riders" in first


@pytest.mark.django_db
def test_a_nonsense_page_number_lands_on_a_real_page(auth_client, roster_rider):
    """get_page swallows both, which is what stops a hand-typed URL 500ing."""
    roster_rider(zwid=4242, name="Ada Racer")

    for query in ("?page=99", "?page=banana"):
        assert auth_client.get(reverse("team:roster") + query).status_code == 200


# --- the uploaded category / tier / phenotype icons ---------------------------------------


@pytest.mark.django_db
def test_an_icon_that_identifies_the_value_replaces_the_badge_and_the_word(
    auth_client, roster_rider, settings, tmp_path
):
    """Phenotype and open category: the artwork names the value on its own, so it stands alone.

    The word is not lost, it moves into the image. An icon carrying alt="" in place of a label
    would delete the value outright for anyone not looking at the screen (WCAG 1.1.1).
    """
    from gotta_bike_platform.models import SiteSettings

    settings.MEDIA_ROOT = str(tmp_path)
    site = SiteSettings.get_settings()
    site.zp_b_emoji.save("cat-b.png", ContentFile(b"not-a-real-png"), save=True)
    # A second kind, so a tag that ignored `kind` and read one map would fail here.
    site.phenotype_sprinter_emoji.save("sprinter.png", ContentFile(b"not-a-real-png"), save=True)

    roster_rider(zwid=4242, name="Ada Racer", category_open="B", phenotype="Sprinter")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")
    text = _text_of(card)
    tags = card.split("<ul", 1)[1].split("</ul>", 1)[0]

    assert 'alt="Category B"' in card, "the icon has to say what it replaced"
    assert 'alt="Sprinter"' in card
    # Scoped to the tag list: the avatar placeholder is legitimately decorative.
    assert 'alt=""' not in tags, "an icon standing in for a word is never decorative"
    # Sized in em, so the icons grow with the reader's text-size setting rather than
    # shrinking away at the largest step.
    assert 'class="h-[2em] w-[2em]"' in card
    assert "Cat B" not in text
    assert "Sprinter" not in text


@pytest.mark.django_db
def test_a_tier_icon_stands_alone_and_keeps_the_tier_in_its_accessible_name(
    auth_client, roster_rider, settings, tmp_path
):
    """Vincent's call, made knowing the trade-off, so the name is what has to hold.

    The ten tier files are one shape in ten colours -- diamond.svg and ruby.svg have
    byte-identical path geometry -- and the tier name drawn inside the art lands at 2.4-4.2px
    at this size. On screen the tiers are therefore told apart by colour; alt is the only
    thing that still says "Bronze" to anyone the colour does not reach, so it must not go.
    """
    from gotta_bike_platform.models import SiteSettings

    settings.MEDIA_ROOT = str(tmp_path)
    SiteSettings.get_settings().zr_bronze_emoji.save("bronze.png", ContentFile(b"x"), save=True)

    roster_rider(zwid=4242, name="Ada Racer", category_racing="Bronze")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert 'alt="Zwift Racing Bronze"' in card
    # Both halves: data-tip alone renders nothing without the tooltip class that reads it.
    assert '<li class="tooltip" data-tip="Zwift Racing Bronze">' in card
    # Not title as well, or the browser draws its own tooltip over DaisyUI's. Scoped to this
    # icon's text: the worded age badge keeps a title, which is a different situation.
    assert 'title="Zwift Racing Bronze"' not in card
    assert "ZR Bronze" not in _text_of(card)


@pytest.mark.django_db
def test_the_womens_category_icon_is_ringed_so_it_differs_from_the_open_one(
    auth_client, roster_rider, settings, tmp_path
):
    """The ring is what separates the two icons on screen.

    One icon map serves both categories, so without it a rider holding both shows two
    identical hexagons and nothing visible says which is which.
    """
    from gotta_bike_platform.models import SiteSettings

    settings.MEDIA_ROOT = str(tmp_path)
    site = SiteSettings.get_settings()
    site.zp_d_emoji.save("cat-d.png", ContentFile(b"x"), save=True)

    roster_rider(zwid=4242, name="Ada Racer", category_open="D", category_women="D")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert 'alt="Category D"' in card
    assert 'alt="Women\'s category D"' in card
    # The ring is the only thing separating the two on screen, so it is pinned.
    assert "ring-pink-400" in card
    assert card.count("ring-pink-400") == 1, "only the women's icon is ringed"


@pytest.mark.django_db
def test_a_value_with_no_icon_keeps_its_worded_badge(auth_client, roster_rider):
    """Nothing uploaded is the normal case for most values, and it must still read."""
    roster_rider(zwid=4242, name="Ada Racer", category_racing="Copper", phenotype="Sprinter", category_open="B")

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))

    assert "ZR Copper" in card
    assert "Sprinter" in card
    assert "Cat B" in card


@pytest.mark.django_db
def test_a_card_can_mix_icons_and_words(auth_client, roster_rider, settings, tmp_path):
    """One value has an icon and another does not -- both must still be readable."""
    from gotta_bike_platform.models import SiteSettings

    settings.MEDIA_ROOT = str(tmp_path)
    SiteSettings.get_settings().phenotype_climber_emoji.save("climber.png", ContentFile(b"x"), save=True)

    roster_rider(zwid=4242, name="Ada Racer", category_racing="Gold", phenotype="Climber")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert 'alt="Climber"' in card
    assert "ZR Gold" in _text_of(card)


# --- team kit status ------------------------------------------------------------------------


def _kit(slug="2026-kit", *, current=True):
    from apps.team.models import TeamKit

    return TeamKit.objects.create(name="2026 Kit", slug=slug, active=True, is_current=current)


def _kitted(user_model, username, zwid, status, slug="2026-kit"):
    user = _member(user_model, username, zwid)
    user.team_kit = {slug: status}
    user.save(update_fields=["team_kit"])
    return user


@pytest.mark.django_db
def test_a_riders_kit_status_shows_at_the_bottom_of_their_card(auth_client, roster_rider, user_model):
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "need")

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))

    assert "Kit: Needs kit" in card


@pytest.mark.django_db
def test_the_kit_badge_matches_the_colour_the_kit_page_uses(auth_client, roster_rider, user_model):
    """One status must not look like two different things in two places.

    Asked of a status that still wears a badge: the two settled ones now show the jersey
    instead, and have no colour of their own left to disagree about.
    """
    from apps.team.kits import BADGE_CLASSES

    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "need")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert BADGE_CLASSES["need"] in card


@pytest.mark.django_db
def test_a_rider_the_team_has_not_asked_shows_no_kit_status(auth_client, roster_rider, user_model):
    """"Unknown" on two thousand cards is a status nobody gave, not information."""
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "unknown")
    roster_rider(zwid=4243, name="Bo Racer")
    _member(user_model, "bo", 4243)

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "Kit:" not in _text_of(_card_for(body, "Ada Racer"))
    assert "Kit:" not in _text_of(_card_for(body, "Bo Racer"))


@pytest.mark.django_db
def test_only_the_current_kit_is_shown(auth_client, roster_rider, user_model):
    """A rider carries a status per kit; the card is about the one the team is in now."""
    _kit(slug="old-kit", current=False)
    _kit(slug="2026-kit", current=True)
    roster_rider(zwid=4242, name="Ada Racer")
    user = _kitted(user_model, "ada", 4242, "have", slug="old-kit")
    user.team_kit = {"old-kit": "have", "2026-kit": "need"}
    user.save(update_fields=["team_kit"])

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))

    assert "Kit: Needs kit" in card
    assert "I have the kit" not in card


@pytest.mark.django_db
def test_a_rider_with_no_account_shows_no_kit_status(auth_client, roster_rider):
    """Kit status lives on the account, and most of the roster has none."""
    _kit()
    roster_rider(zwid=4242, name="Scouted Rider")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "Kit:" not in _text_of(_card_for(body, "Scouted Rider"))


@pytest.mark.django_db
def test_an_unverified_rider_lends_no_kit_status(roster_rider, user_model):
    """Same gate as everything else on the account half: an unverified zwid joins nothing."""
    from apps.team.rosterv2 import build_roster_index

    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    user = _kitted(user_model, "impostor", 4242, "have")
    user.zwid_verified = False
    user.save(update_fields=["zwid_verified"])

    assert build_roster_index().rows[0].account is None


@pytest.mark.django_db
def test_no_current_kit_means_no_kit_badge_anywhere(auth_client, roster_rider, user_model):
    """Before a season's kit is made current there is nothing to report."""
    _kit(current=False)
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "have")

    assert "Kit:" not in _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))


@pytest.mark.django_db
def test_a_status_that_is_not_a_real_status_is_ignored_rather_than_raising(auth_client, roster_rider, user_model):
    """team_kit is a JSONField, so its contents are not guaranteed to be a KitStatus.

    The kits module says as much in its own docstring: a value edited by hand is treated as
    the default rather than raising. Without the membership check here, KitStatus("banana")
    raises ValueError and takes the whole roster down with it.
    """
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "banana")

    response = auth_client.get(reverse("team:roster"))

    assert response.status_code == 200
    assert "Kit:" not in _text_of(_card_for(response.content.decode(), "Ada Racer"))
    assert "banana" not in response.content.decode()


@pytest.mark.django_db
def test_the_kit_wording_is_third_person_on_someone_elses_card(auth_client, roster_rider, user_model):
    """The stored labels are written for the rider's own profile, in the first person.

    "Kit: I have the kit" on a teammate's card reads as a mistake, so the card has its own
    wording. Pinned because the obvious implementation reaches for KitStatus(...).label.
    """
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "have")

    # "have" draws the jersey, so the wording is now the icon's accessible name rather
    # than visible text -- which is exactly where a first-person label would still be wrong.
    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert 'alt="Kit: Has the kit"' in card
    assert "I have the kit" not in card


# --- country flags ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_country_shows_as_a_flag_not_an_abbreviation(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", country="fr")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert "/flags/fr.gif" in card
    assert 'alt="France"' in card
    assert '<li class="tooltip" data-tip="France">' in card
    assert "FR" not in _text_of(card)


@pytest.mark.django_db
def test_a_uk_subdivision_flies_the_parent_flag_and_keeps_its_own_name(auth_client, roster_rider):
    """ZwiftPower sends ISO 3166-2 for the UK nations; django_countries knows only 3166-1.

    Country(code).flag builds a URL without checking the country exists, so an unvalidated
    implementation links /static/flags/gb-wls.gif -- an image that is not there.
    """
    roster_rider(zwid=4242, name="Ada Racer", country="gb-wls")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert "/flags/gb.gif" in card
    assert "gb-wls.gif" not in card
    assert 'data-tip="Wales"' in card


@pytest.mark.django_db
def test_an_unknown_country_code_keeps_its_abbreviation(auth_client, roster_rider):
    """Degrade to what the card showed before, never to a broken image."""
    roster_rider(zwid=4242, name="Ada Racer", country="zzz")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert "ZZZ" in _text_of(card)
    assert "/flags/zzz.gif" not in card


@pytest.mark.django_db
def test_the_flag_lookup_is_case_insensitive(auth_client, roster_rider):
    """Upstream stores lowercase; django_countries keys on uppercase."""
    roster_rider(zwid=4242, name="Ada Racer", country="US")
    roster_rider(zwid=4243, name="Bo Racer", country="us")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "/flags/us.gif" in _card_for(body, "Ada Racer")
    assert "/flags/us.gif" in _card_for(body, "Bo Racer")


# --- age bracket icons, which ship a default set -----------------------------------------------


@pytest.mark.django_db
def test_every_shown_age_bracket_has_bundled_artwork(auth_client, roster_rider):
    """Age is the one family that ships its own icons, so no bracket falls back to text."""
    from apps.accounts.templatetags.accounts_tags import AGE_DEFAULT_ICONS
    from apps.team.rosterv2 import AGE_BRACKETS_ORDER

    assert set(AGE_DEFAULT_ICONS) == set(AGE_BRACKETS_ORDER)

    for zwid, bracket in enumerate(AGE_BRACKETS_ORDER, start=4200):
        roster_rider(zwid=zwid, name=f"Rider {bracket}", age=bracket)

    body = auth_client.get(reverse("team:roster")).content.decode()

    for bracket in AGE_BRACKETS_ORDER:
        card = _card_for(body, f"Rider {bracket}")
        assert f'alt="Age {bracket}"' in card, f"{bracket} has no icon"
        assert "badge-outline\">" + bracket not in card, f"{bracket} fell back to a text badge"
        # A served URL, not the bare storage path: skipping static() yields a relative src
        # that resolves against whatever page it is on, and breaks under hashed filenames.
        assert 'src="/static/accounts/age/' in card, f"{bracket}'s icon is not a static URL"


@pytest.mark.django_db
def test_the_bundled_files_are_really_there():
    """A missing default would render a broken image on every card carrying that bracket."""
    from django.contrib.staticfiles import finders

    from apps.accounts.templatetags.accounts_tags import AGE_DEFAULT_ICONS

    for bracket, path in AGE_DEFAULT_ICONS.items():
        assert finders.find(path), f"{bracket}: {path} is not on the static path"


@pytest.mark.django_db
def test_an_uploaded_icon_replaces_the_bundled_one(auth_client, roster_rider, settings, tmp_path):
    from gotta_bike_platform.models import SiteSettings

    settings.MEDIA_ROOT = str(tmp_path)
    SiteSettings.get_settings().age_vet_emoji.save("custom-vet.png", ContentFile(b"x"), save=True)

    roster_rider(zwid=4242, name="Ada Racer", age="Vet")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert "custom-vet" in card
    assert "age-vet.svg" not in card, "the upload must win over the bundled default"


@pytest.mark.django_db
def test_uploading_one_bracket_leaves_the_others_on_their_defaults(
    auth_client, roster_rider, settings, tmp_path
):
    """The override is per bracket, not a switch that turns the whole bundled set off."""
    from gotta_bike_platform.models import SiteSettings

    settings.MEDIA_ROOT = str(tmp_path)
    SiteSettings.get_settings().age_vet_emoji.save("custom-vet.png", ContentFile(b"x"), save=True)

    roster_rider(zwid=4242, name="Vet Rider", age="Vet")
    roster_rider(zwid=4243, name="Mas Rider", age="Mas")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "custom-vet" in _card_for(body, "Vet Rider")
    assert "age-mas.svg" in _card_for(body, "Mas Rider")


# --- the kit icon ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_rider_who_has_the_kit_shows_the_jersey_not_the_words(auth_client, roster_rider, user_model):
    """A settled kit is a glance, not a sentence: the icon replaces the badge."""
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert "accounts/kit/kit.svg" in card
    # The badge is gone, not merely joined -- otherwise this passes with both on the card.
    assert "Kit: Has the kit" not in _text_of(card)


@pytest.mark.django_db
def test_a_completed_zwift_order_shows_the_same_jersey(auth_client, roster_rider, user_model):
    """The other settled status. One drawing, its own wording."""
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "completed")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert "accounts/kit/kit.svg" in card
    assert 'alt="Kit: Completed by Zwift"' in card


@pytest.mark.django_db
@pytest.mark.parametrize(("status", "words"), [("need", "Kit: Needs kit"), ("submitted", "Kit: Submitted to Zwift")])
def test_a_kit_still_being_chased_keeps_its_words(auth_client, roster_rider, user_model, status, words):
    """The icon means done. A kit still in motion says so in words, or it reads as settled."""
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, status)

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert "accounts/kit/kit.svg" not in card
    assert words in _text_of(card)


@pytest.mark.django_db
def test_the_kit_icons_hover_text_and_alt_cannot_drift(auth_client, roster_rider, user_model):
    """Two ways of carrying the same word, so they are built from one string."""
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert 'data-tip="Kit: Has the kit"' in card
    assert 'alt="Kit: Has the kit"' in card


@pytest.mark.django_db
def test_an_uploaded_kit_icon_replaces_the_bundled_one(auth_client, roster_rider, user_model, settings, tmp_path):
    """The default is a starting point, not the only option."""
    from gotta_bike_platform.models import SiteSettings

    settings.MEDIA_ROOT = str(tmp_path)
    site_settings = SiteSettings.get_settings()
    site_settings.kit_emoji.save("teamjersey.png", ContentFile(b"x"), save=True)

    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert site_settings.kit_emoji.url in card
    assert "accounts/kit/kit.svg" not in card


@pytest.mark.django_db
def test_a_rider_the_team_has_not_asked_gets_no_jersey(auth_client, roster_rider, user_model):
    """No status is not a settled status."""
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "unknown")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert "accounts/kit/kit.svg" not in card


# --- where the card puts things -------------------------------------------------------------


def _guild_member(user, joined):
    from apps.accounts.models import GuildMember

    return GuildMember.objects.create(
        discord_id=user.discord_id, username=user.discord_username, user=user, joined_at=joined
    )


@pytest.mark.django_db
def test_membership_reads_as_joined_and_a_year(auth_client, roster_rider, user_model):
    """The month was noise: nobody reads a roster to learn somebody joined in March."""
    from datetime import UTC, datetime

    roster_rider(zwid=4242, name="Ada Racer")
    _guild_member(_member(user_model, "ada", 4242), datetime(2022, 3, 14, tzinfo=UTC))

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))

    assert "Joined 2022" in card
    assert "member since" not in card
    assert "Mar" not in card


@pytest.mark.django_db
def test_the_kit_jersey_sits_immediately_left_of_the_flag(auth_client, roster_rider, user_model):
    """Vincent's placement. Both are about who the rider is to us rather than how they race."""
    _kit()
    roster_rider(zwid=4242, name="Ada Racer", country="us")
    _kitted(user_model, "ada", 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")
    badges = card.split("<ul", 1)[1].split("</ul>", 1)[0]

    assert badges.index("accounts/kit/kit.svg") < badges.index("flags"), "the kit belongs before the flag"
    # And nothing between them: they read as one pair.
    between = badges[badges.index("accounts/kit/kit.svg") : badges.index("flags")]
    assert between.count("<img") == 1


@pytest.mark.django_db
def test_a_settled_kit_leaves_no_badge_at_the_foot_of_the_card(auth_client, roster_rider, user_model):
    """It moved up; it did not multiply."""
    _kit()
    roster_rider(zwid=4242, name="Ada Racer")
    _kitted(user_model, "ada", 4242, "have")

    card = _card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer")

    assert card.count("accounts/kit/kit.svg") == 1
    assert "Kit: Has the kit" not in _text_of(card)


@pytest.mark.django_db
def test_a_card_shows_the_riders_zwift_id(auth_client, roster_rider):
    """The id you quote when asking anyone else about this rider."""
    roster_rider(zwid=8675309, name="Ada Racer")

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Ada Racer"))

    assert "ZWID 8675309" in card


@pytest.mark.django_db
def test_a_rider_with_no_account_still_shows_their_zwift_id(auth_client, roster_rider):
    """Most of the roster never registered here, and the id is how they are identified at all."""
    roster_rider(zwid=8675309, name="Scouted Rider")

    card = _text_of(_card_for(auth_client.get(reverse("team:roster")).content.decode(), "Scouted Rider"))

    assert "ZWID 8675309" in card

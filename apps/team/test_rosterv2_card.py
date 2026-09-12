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

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "Ada Racer" in body
    assert "1642" in body  # vELO
    assert "286" in body  # zFTP watts
    assert "4.1" in body  # 20-minute w/kg
    assert "Cat B" in body


@pytest.mark.django_db
def test_a_joined_card_links_to_the_profile_and_names_the_rider(auth_client, roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "ada_discord", 4242)

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert reverse("accounts:public_profile", args=[user.pk]) in body
    assert "ada_discord" in body


@pytest.mark.django_db
def test_an_unjoined_card_says_so_and_links_nowhere(auth_client, roster_rider, user_model):
    """The failure this exists to catch is a card asserting a link the index refused to make."""
    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "impostor", 4242)
    user.zwid_verified = False
    user.save()

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "No account here" in body
    assert reverse("accounts:public_profile", args=[user.pk]) not in body
    assert "impostor" not in body
    assert "cdn.discordapp.com" not in body


@pytest.mark.django_db
def test_a_missing_figure_is_an_em_dash(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", velo=None, ftp=None, wkg_20min=None)

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert _text_of(_card_for(body, "Ada Racer")).count("—") == 3


@pytest.mark.django_db
def test_a_real_zero_is_printed_as_zero_not_as_missing(auth_client, roster_rider):
    """`{% if value %}` renders an em dash over a zero, and the model says a zero is data.

    `|default_if_none` cannot fix it either: floatformat turns None into "", so the default
    never fires and the em dash never appears at all. Both bugs are silent.
    """
    roster_rider(zwid=4242, name="Zero Rider", velo=0.0, ftp=0.0, wkg_20min=0.0)

    body = auth_client.get(reverse("team:rosterv2")).content.decode()
    card = _text_of(_card_for(body, "Zero Rider"))

    assert "—" not in card
    assert "0 W" in card
    assert "0.0 w/kg" in card


@pytest.mark.django_db
def test_a_rider_with_no_race_on_record_says_so(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", days_since_race=None)

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "No race on record" in body


@pytest.mark.django_db
def test_a_hidden_age_bracket_leaves_no_trace_on_the_card(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", age="-")

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "Age -" not in body
    assert "Age" not in _card_for(body, "Ada Racer")


@pytest.mark.django_db
def test_a_junior_bracket_is_shown_because_that_is_the_owners_call(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer", age="Jnr")

    assert "Age Jnr" in auth_client.get(reverse("team:rosterv2")).content.decode()


# --- paging and the empty state -----------------------------------------------------------


@pytest.mark.django_db
def test_an_empty_roster_shows_a_sentence_not_an_empty_grid(auth_client):
    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "No riders yet" in body
    assert "Roster pages" not in body


@pytest.mark.django_db
def test_one_page_of_riders_needs_no_paging_controls(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer")

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "Roster pages" not in body


@pytest.mark.django_db
def test_a_long_roster_is_paged_rather_than_sent_whole(auth_client, roster_rider):
    """v1 sends every row: ~450 KB of HTML at 100 riders."""
    for n in range(ROSTER_PAGE_SIZE + 5):
        roster_rider(zwid=5000 + n, name=f"Rider {n:03d}")

    first = auth_client.get(reverse("team:rosterv2")).content.decode()
    second = auth_client.get(reverse("team:rosterv2") + "?page=2").content.decode()

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
        assert auth_client.get(reverse("team:rosterv2") + query).status_code == 200


# --- the uploaded category / tier / phenotype icons ---------------------------------------


@pytest.mark.django_db
def test_an_uploaded_icon_appears_inside_the_badge_beside_its_label(auth_client, roster_rider, settings, tmp_path):
    """The icon joins the words; it never replaces them.

    An icon standing alone would leave colour and shape carrying the meaning, which is the
    thing every tag on this card is labelled in text to avoid. alt="" keeps a screen reader
    from announcing the category twice.
    """
    from gotta_bike_platform.models import SiteSettings

    settings.MEDIA_ROOT = str(tmp_path)
    site = SiteSettings.get_settings()
    site.zr_gold_emoji.save("gold.png", ContentFile(b"not-a-real-png"), save=True)

    # Two different kinds, so a tag that ignored `kind` and always read one map would fail.
    site.phenotype_sprinter_emoji.save("sprinter.png", ContentFile(b"not-a-real-png"), save=True)

    roster_rider(zwid=4242, name="Ada Racer", category_racing="Gold", phenotype="Sprinter")

    card = _card_for(auth_client.get(reverse("team:rosterv2")).content.decode(), "Ada Racer")

    # EVERY icon, not just one of them: asserting that alt="" appears somewhere passes while
    # another icon on the same card announces its label a second time.
    assert card.count("<img") == card.count('alt=""'), "every icon is decorative; the words are the label"
    assert "gold" in card
    assert "sprinter" in card
    assert "ZR Gold" in _text_of(card), "the words must survive the icon"
    assert "Sprinter" in _text_of(card)


@pytest.mark.django_db
def test_a_tier_with_no_uploaded_icon_still_reads(auth_client, roster_rider):
    """Nothing uploaded is the normal case, and it must cost the reader nothing."""
    roster_rider(zwid=4242, name="Ada Racer", category_racing="Copper", phenotype="Sprinter", category_open="B")

    card = _text_of(_card_for(auth_client.get(reverse("team:rosterv2")).content.decode(), "Ada Racer"))

    assert "ZR Copper" in card
    assert "Sprinter" in card
    assert "Cat B" in card

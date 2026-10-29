"""How the public profile groups what it shows into cards.

Three rows of two cards -- Rider and Verification, Ratings and Power, Rider type and Race
record -- then the Elsewhere strip across both columns (Vincent's call, 2026-10-29). The old
layout put most of the rider data in one card several times the height of the others; these
pin which rows went where, so a new row joins a card of its own kind rather than growing one
long card again.

Rider keeps only what the member enters. Club and country come from the rider data row, so
they sit in Race record, inside the region the Update button refreshes.
"""

import html
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.rider_data.models import RiderProfile

CARDS = ["Rider", "Verification", "Ratings", "Power", "Rider type", "Race record", "Elsewhere"]


@pytest.fixture
def rider(user_model, db):
    """Build a verified rider who has filled in her equipment.

    Returns:
        The rider.

    """
    return user_model.objects.create_user(
        username="layout-rider",
        email="layout-rider@example.test",
        first_name="Sam",
        last_name="Lane",
        gender="female",
        trainer="Smart trainer",
        zwid=7001,
        zwid_verified=True,
        permission_overrides={"team_member": True},
    )


@pytest.fixture
def profile(rider):
    """Store a cached row with something for each of the four rider data cards.

    Returns:
        The RiderProfile row.

    """
    now = timezone.now()
    return RiderProfile.objects.create(
        zwid=rider.zwid,
        country="nz",
        age="Vet",
        weight_kg=61.0,
        height_cm=168,
        ftp=230,
        category_open="B",
        category_women="B",
        velo=1480.0,
        club_name="Test Club",
        phenotype_value="Puncheur",
        payload={
            "power": {"curve_wkg": {"5": 13.4}},
            "handicaps": {"flat": 2.1},
            "totals": {"distance_km": 48200000, "climbed_m": 512000},
        },
        fetched_at=now,
        last_requested_at=now,
    )


def _body(client, viewer, target):
    """Render a public profile.

    Returns:
        The response body.

    """
    client.force_login(viewer)
    return client.get(reverse("accounts:public_profile", args=[target.pk])).content.decode()


def _text(markup):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def _cards(body):
    """Split a page at its card headings.

    Returns:
        Each heading's text mapped to the markup from it to the next heading, in page order.

    """
    found = list(re.finditer(r"<h3[^>]*>(.*?)</h3>", body, re.S))
    ends = [match.start() for match in found[1:]] + [len(body)]
    return {_text(match.group(1)): body[match.end() : end] for match, end in zip(found, ends, strict=True)}


@pytest.mark.django_db
def test_the_cards_come_in_three_pairs_then_the_strip(client, team_member, rider, profile):
    headings = list(_cards(_body(client, team_member, rider)))

    # Exactly these, with nothing in between: the long card the old layout was built around
    # is gone, not kept beside them.
    assert headings[headings.index("Rider") : headings.index("Elsewhere") + 1] == CARDS


@pytest.mark.django_db
def test_each_row_sits_in_the_card_of_its_kind(client, team_member, rider, profile):
    cards = {heading: _text(markup) for heading, markup in _cards(_body(client, team_member, rider)).items()}

    assert "Gender Female" in cards["Rider"]
    assert "Trainer Smart trainer" in cards["Rider"]
    assert "Club" not in cards["Rider"]  # rider data, so it refreshes with the rest of it
    assert "Zwift Verified Yes" in cards["Verification"]
    assert "Zwift category B · women's B" in cards["Ratings"]
    assert "vELO" in cards["Ratings"]
    assert "Weight / Height 61.0 kg · 168 cm" in cards["Power"]
    assert "Best power (Zwift Racing)" in cards["Power"]
    assert "Phenotype Puncheur" in cards["Rider type"]
    assert "Terrain handicaps" in cards["Rider type"]
    assert "Club Test Club" in cards["Race record"]
    assert "Country / Age NZ · Vet" in cards["Race record"]
    assert "Lifetime climbed 512,000 m" in cards["Race record"]
    assert "ZwiftPower" in cards["Elsewhere"]
    assert "Update" in cards["Elsewhere"]


@pytest.mark.django_db
def test_a_card_with_nothing_to_show_is_left_out(client, team_member, rider):
    """A row with only a rating fills Ratings; the empty cards close up rather than sit blank."""
    now = timezone.now()
    RiderProfile.objects.create(zwid=rider.zwid, velo=1480.0, payload={}, fetched_at=now, last_requested_at=now)

    cards = _cards(_body(client, team_member, rider))

    assert [heading for heading in cards if heading in CARDS] == ["Rider", "Verification", "Ratings", "Elsewhere"]


@pytest.mark.django_db
def test_the_members_own_links_sit_in_the_elsewhere_strip(client, team_member, rider, profile):
    rider.strava_url = "https://www.strava.com/athletes/1"
    rider.save(update_fields=["strava_url"])

    cards = _cards(_body(client, team_member, rider))

    assert "https://www.strava.com/athletes/1" in cards["Elsewhere"]
    assert "Social Links" not in cards


@pytest.mark.django_db
def test_an_unverified_member_with_links_gets_the_strip_without_the_source_links(client, team_member, user_model):
    """Their own links are theirs to share; the zwid behind the source links is unconfirmed."""
    member = user_model.objects.create_user(
        username="links-only",
        email="links-only@example.test",
        zwid=7002,
        zwid_verified=False,
        twitch_channel="https://www.twitch.tv/example",
    )

    body = _body(client, team_member, member)
    cards = _cards(body)

    assert "https://www.twitch.tv/example" in cards["Elsewhere"]
    assert "zwiftpower.com/profile.php" not in body
    assert reverse("accounts:refresh_rider_data", args=[member.pk]) not in body


@pytest.mark.django_db
def test_no_strip_for_a_member_with_nothing_to_link(client, team_member, user_model):
    member = user_model.objects.create_user(username="bare-member", email="bare-member@example.test")

    cards = _cards(_body(client, team_member, member))

    assert "Elsewhere" not in cards
    assert "Zwift account not verified." in _text(cards["Rider data"])


@pytest.mark.django_db
def test_verification_days_are_plain_text_and_an_expired_record_is_a_badge(
    client, team_member, rider, verification_factory
):
    """Status-coloured text on the card fell under 4.5:1 in the light theme."""
    verification_factory(rider, "weight_full", days_ago=20)  # 120-day window
    verification_factory(rider, "power", days_ago=400)  # 365-day window

    card = _cards(_body(client, team_member, rider))["Verification"]

    assert "Weight Full 100 days left" in _text(card)
    assert '<span class="badge badge-error badge-sm">Expired</span>' in card
    for faint in ("text-success", "text-error", "text-base-content/50"):
        assert faint not in card


@pytest.mark.django_db
def test_a_member_with_no_records_is_told_so(client, team_member, rider):
    card = _text(_cards(_body(client, team_member, rider))["Verification"])

    assert "No verified records" in card

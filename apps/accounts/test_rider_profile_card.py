"""The consolidated rider card on the public profile.

Replaces three cards that each fetched their own source -- ZwiftPower, Zwift Racing, and a
live per-render call to zauth for the official Racing Profile -- with one cached RiderProfile
row.

Two things are worth testing beyond "the values appear". First, most of what the card shows
lives in the JSON payload rather than in a column, so a change to zauth's document shape
breaks the card silently: the accessor tests below pin the paths. Second, the card must not
become a verification signal -- RiderProfile carries no verification state by design, and
`zwift_connection.status` reports whether a Zwift account exists service-wide, not whether
this rider is still linked to us.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.rider_data.models import RiderProfile

# Shaped like a real zauth ProfileFull document, with the real values for the rider used in
# the design review (zwid 6164399, name anonymised).
PAYLOAD = {
    "power": {"zmap": 265.0, "vo2max": 54.2, "cp": 178.99, "awc": 28659.03},
    "ratings": {"rating_max30": 693.94, "rating_max90": 767.61},
    "phenotype": {
        "value": "Sprinter",
        "bias": 21.62,
        "scores": {"sprinter": 62.2, "puncheur": 40.5, "pursuiter": 34.9, "climber": 24.7, "tt": 26.5},
    },
    "handicaps": {"flat": 16.39, "rolling": -58.03, "hilly": -56.48, "mountainous": -80.47},
    "totals": {"distance_km": 63988, "climbed_m": 483132},
}


@pytest.fixture
def rider(user_model, db):
    """Build a verified rider whose profile has been synced.

    Returns:
        The rider.

    """
    return user_model.objects.create_user(
        username="rider",
        email="rider@example.test",
        first_name="Alex",
        last_name="Rivera",
        zwid=6164399,
        zwid_verified=True,
        permission_overrides={"team_member": True},
    )


@pytest.fixture
def profile(rider):
    """Build the cached profile behind the card.

    Returns:
        The RiderProfile row.

    """
    now = timezone.now()
    return RiderProfile.objects.create(
        zwid=rider.zwid,
        name="Alex Rivera",
        country="fr",
        age="Vet",
        weight_kg=78.5,
        height_cm=165,
        ftp=201,
        zftp=201,
        category_open="D",
        category_racing="Bronze",
        velo=367.37,
        zp_skill=5351,
        phenotype_value="Sprinter",
        club_name="COALITION",
        payload=PAYLOAD,
        fetched_at=now,
        last_requested_at=now,
    )


def _body(client, viewer, target):
    """Render the public profile.

    Args:
        client: Test client.
        viewer: The signed-in user.
        target: Whose profile to view.

    Returns:
        The response body.

    """
    client.force_login(viewer)
    return client.get(reverse("accounts:public_profile", args=[target.pk])).content.decode()


# ---------------------------------------------------------------- payload accessors


@pytest.mark.django_db
def test_the_accessors_read_the_documented_payload_paths(profile):
    """These paths are the contract with zauth; a shape change must fail here, not in a page."""
    assert profile.handicaps == PAYLOAD["handicaps"]
    assert profile.totals == PAYLOAD["totals"]
    assert profile.phenotype_scores == PAYLOAD["phenotype"]["scores"]
    assert profile.phenotype_bias == pytest.approx(21.62)
    assert profile.power_extras == {"zmap": 265.0, "vo2max": 54.2, "cp": 178.99, "awc": 28659.03}
    assert profile.peak_ratings == {"max30": 693.94, "max90": 767.61}


@pytest.mark.django_db
def test_the_accessors_return_none_rather_than_empty_when_a_block_is_absent(rider):
    """A rider with no ZwiftRacing row has no handicaps or phenotype; that is normal, not an error."""
    now = timezone.now()
    bare = RiderProfile.objects.create(zwid=rider.zwid, payload={}, fetched_at=now, last_requested_at=now)

    assert bare.handicaps is None
    assert bare.totals is None
    assert bare.phenotype_scores is None
    assert bare.phenotype_bias is None
    assert bare.power_extras is None
    assert bare.peak_ratings is None


@pytest.mark.django_db
def test_the_accessors_survive_a_null_or_wrongly_typed_block(rider):
    """Upstream sends null for whole blocks; a None must not raise where a dict was expected."""
    now = timezone.now()
    odd = RiderProfile.objects.create(
        zwid=rider.zwid,
        payload={"handicaps": None, "phenotype": "unexpected", "power": [], "ratings": {"rating_max30": None}},
        fetched_at=now,
        last_requested_at=now,
    )

    assert odd.handicaps is None
    assert odd.phenotype_scores is None
    assert odd.phenotype_bias is None
    assert odd.power_extras is None
    assert odd.peak_ratings is None


# ---------------------------------------------------------------- what the card renders


@pytest.mark.django_db
def test_the_card_renders_the_promoted_columns(client, profile, rider, team_member):
    body = _body(client, team_member, rider)

    assert "Racing &amp; Performance" in body
    for expected in ("COALITION", "Bronze", "78.5 kg", "165 cm", "5351"):
        assert expected in body, f"{expected!r} missing from the card"


@pytest.mark.django_db
def test_the_card_renders_the_three_payload_groups_added_by_request(client, profile, rider, team_member):
    """Handicaps, lifetime distance and lifetime climbed -- none of which any old card showed."""
    body = _body(client, team_member, rider)

    assert "Terrain handicaps" in body
    assert "16.4" in body and "-58.0" in body
    # Thousands separators, not bare digits: "483132 m" is materially harder to read.
    assert "63,988 km" in body
    assert "483,132 m" in body


@pytest.mark.django_db
def test_lifetime_climbed_is_rendered_in_metres_not_kilometres(client, profile, rider, team_member):
    """climbed_m and distance_km do not share a unit; rendering both as km was the easy mistake."""
    body = _body(client, team_member, rider)

    assert "483,132 m" in body
    assert "483 km" not in body


@pytest.mark.django_db
def test_the_peak_ratings_carry_no_category_badge(client, profile, rider, team_member):
    """Peak ratings render bare: zauth carries one racing category, the current one.

    Labelling a 90-day-old peak with today's tier would state something false rather than
    leave a gap, so the numbers are shown bare.
    """
    body = _body(client, team_member, rider)
    section = body[body.index("30 / 90-day peak") : body.index("30 / 90-day peak") + 320]

    assert "694" in section and "768" in section
    assert "Bronze" not in section


# ---------------------------------------------------------------- gates


@pytest.mark.django_db
def test_an_unverified_rider_gets_no_data_even_with_a_synced_row(client, user_model, team_member):
    """The gate stayed on User.zwid_verified, exactly where the replaced cards had it."""
    now = timezone.now()
    unverified = user_model.objects.create_user(
        username="unver", email="unver@example.test", zwid=777, zwid_verified=False,
    )
    RiderProfile.objects.create(
        zwid=777, name="Should Not Show", club_name="SECRETCLUB", fetched_at=now, last_requested_at=now,
    )

    body = _body(client, team_member, unverified)

    assert "SECRETCLUB" not in body
    assert "Zwift account not verified" in body


@pytest.mark.django_db
def test_a_verified_rider_with_no_synced_row_is_told_so(client, rider, team_member):
    body = _body(client, team_member, rider)

    assert "has not been synced" in body


@pytest.mark.django_db
def test_connection_status_in_the_payload_never_gates_the_card(client, profile, rider, team_member):
    """zwift_connection.status is service-wide account existence, not "linked to us".

    The project forbids reading it as verification. Pinned here because the card is the most
    tempting place to start: the field is right there in the payload it already renders from.
    """
    profile.payload = {**PAYLOAD, "zwift_connection": {"status": "disconnected"}}
    profile.save(update_fields=["payload"])

    body = _body(client, team_member, rider)

    # Still rendered: the rider is verified on the User row, which is the only thing that counts.
    assert "COALITION" in body
    assert "Zwift account not verified" not in body


# ---------------------------------------------------------------- what the card must not claim


@pytest.mark.django_db
def test_the_card_does_not_invent_the_data_riderprofile_cannot_supply(client, profile, rider, team_member):
    """Race records, best-ever rating and ZwiftPower rank are not in ProfileFull.

    Guards against someone later wiring these back up from ZRRider/ZPTeamRiders and quietly
    reintroducing the per-source fetches the consolidation removed.
    """
    body = _body(client, team_member, rider)
    card = body[body.index("Racing &amp; Performance") :]
    card = card[: card.index("Recent Results")] if "Recent Results" in card else card

    for absent in ("Best seen", "Races", "Rank"):
        assert absent not in card, f"{absent!r} cannot come from RiderProfile"

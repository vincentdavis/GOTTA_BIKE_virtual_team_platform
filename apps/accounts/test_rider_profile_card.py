"""The consolidated rider card, on the public profile and the edit page.

Replaces three cards that each fetched their own source -- ZwiftPower, Zwift Racing, and a
live per-render call to zauth for the official Racing Profile -- with one cached RiderProfile
row.

Two things are worth testing beyond "the values appear". First, most of what the card shows
lives in the JSON payload rather than in a column, so a change to zauth's document shape
breaks the card silently: the accessor tests below pin the paths. Second, the card must not
become a verification signal -- RiderProfile carries no verification state by design, and
`zwift_connection.status` reports whether a Zwift account exists service-wide, not whether
this rider is still linked to us.

The same partial renders on /user/profile/edit/ so a rider can see what teammates see. It is
read-only there, which is worth pinning: on a page whose whole purpose is editing, a block of
data that looks editable but silently discards changes would be worse than not showing it.
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
    # Both in METRES, as upstream really sends them -- distance_km is misnamed and carries
    # metres. The earlier fixture stored 63988 here, an already-converted value that existed
    # nowhere in the real payload, which is precisely why the units bug rendered a rider's
    # lifetime distance as 37,209,725 km without failing a single test.
    "totals": {"distance_km": 63988161, "climbed_m": 483132},
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
    assert profile.totals == PAYLOAD["totals"]  # raw block, units uncorrected
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


# ---------------------------------------------------------------- sparse and empty rows


@pytest.mark.django_db
def test_height_renders_for_a_rider_who_has_no_weight_or_power(client, rider, team_member):
    """Height and weight come from different upstream blocks, so one arrives without the other.

    zauth reads height from the top level of the Zwift profile and weight from the
    competitionMetrics sub-object, which is routinely absent. The row that renders height was
    written to handle exactly this, but the group guard around it omitted height_cm, making
    that branch unreachable.
    """
    now = timezone.now()
    RiderProfile.objects.create(
        zwid=rider.zwid, height_cm=182.0, fetched_at=now, last_requested_at=now,
    )

    body = _body(client, team_member, rider)

    assert "182 cm" in body


@pytest.mark.django_db
def test_a_row_with_nothing_to_show_is_reported_as_unsynced(client, rider, team_member):
    """A model instance is always truthy, so row existence is not the same as having data.

    Without this the card renders its header, an empty body and an "Updated <date>" stamp --
    asserting freshly synced data while showing none of it.
    """
    now = timezone.now()
    RiderProfile.objects.create(zwid=rider.zwid, name="Alex Rivera", fetched_at=now, last_requested_at=now)

    body = _body(client, team_member, rider)

    assert "has not been synced" in body
    # The card stamps an ISO date; the page footer carries its own "Updated 2026/09/02"
    # deploy stamp, which a bare "Updated" check matches instead.
    assert f"Updated {timezone.localtime(now).strftime('%Y-%m-%d')}" not in body


@pytest.mark.django_db
def test_a_zero_measurement_counts_as_data(client, rider, team_member):
    """A 0.0 handicap or 0 FTP is a real measurement, not an absent one.

    has_display_data tests presence rather than truthiness precisely so a legitimate zero does
    not read as "never synced".
    """
    now = timezone.now()
    profile = RiderProfile.objects.create(
        zwid=rider.zwid, ftp=0.0, fetched_at=now, last_requested_at=now,
    )

    assert profile.has_display_data is True
    assert "has not been synced" not in _body(client, team_member, rider)


@pytest.mark.django_db
def test_name_alone_does_not_count_as_display_data(rider):
    """``name`` is promoted but never rendered -- the profile header already carries it."""
    now = timezone.now()
    profile = RiderProfile.objects.create(
        zwid=rider.zwid, name="Alex Rivera", fetched_at=now, last_requested_at=now,
    )

    assert profile.has_display_data is False


@pytest.mark.django_db
def test_payload_only_data_counts_as_display_data(rider):
    """A row with no populated columns but a usable payload still has something to show."""
    now = timezone.now()
    profile = RiderProfile.objects.create(
        zwid=rider.zwid,
        payload={"totals": {"distance_km": 63988}},
        fetched_at=now,
        last_requested_at=now,
    )

    assert profile.has_display_data is True


# ---------------------------------------------------------------- the edit page


def _edit_body(client, viewer):
    """Render the profile edit page for a signed-in user.

    Args:
        client: Test client.
        viewer: The signed-in user.

    Returns:
        The response body.

    """
    client.force_login(viewer)
    return client.get(reverse("accounts:profile_edit")).content.decode()


def _card_region(body: str) -> str:
    """Slice out the card so assertions do not accidentally match the surrounding form.

    Args:
        body: The rendered page.

    Returns:
        The markup from the card heading to the end of its section.

    """
    start = body.index("Racing &amp; Performance")
    end = body.find("Required fields", start)
    return body[start : end if end != -1 else len(body)]


@pytest.mark.django_db
def test_the_edit_page_shows_the_same_card_as_the_public_profile(client, profile, rider):
    """A rider should be able to see what the rest of the team sees about them."""
    body = _edit_body(client, rider)

    assert "Your Racing Data" in body
    for expected in ("Racing &amp; Performance", "COALITION", "Bronze", "165 cm", "Terrain handicaps"):
        assert expected in body, f"{expected!r} missing from the edit page"


@pytest.mark.django_db
def test_the_edit_page_card_carries_the_payload_groups_too(client, profile, rider):
    """Not a trimmed copy -- the whole card, including the payload-only groups."""
    body = _edit_body(client, rider)

    assert "63,988 km" in body
    assert "483,132 m" in body
    assert "16.4" in body


@pytest.mark.django_db
def test_the_card_is_read_only_on_the_edit_page(client, profile, rider):
    """Every other block on this page is editable, so this one must not look like it is.

    A nested <form> would also be silently dropped by the browser, and an input inside the
    card would post a value nothing reads -- the sync overwrites this row on its next run.
    """
    card = _card_region(_edit_body(client, rider))

    assert "<input" not in card
    assert "<form" not in card
    assert "<select" not in card
    assert "not editable here" in _edit_body(client, rider)


@pytest.mark.django_db
def test_the_edit_page_card_uses_the_same_verification_gate(client, user_model):
    """The rider's own view is gated exactly as the public one; being the owner is not a bypass."""
    now = timezone.now()
    unverified = user_model.objects.create_user(
        username="mine", email="mine@example.test", zwid=888, zwid_verified=False,
        permission_overrides={"team_member": True},
    )
    RiderProfile.objects.create(
        zwid=888, club_name="SECRETCLUB", fetched_at=now, last_requested_at=now,
    )

    body = _edit_body(client, unverified)

    assert "SECRETCLUB" not in body
    assert "Zwift account not verified" in body


# ---------------------------------------------------------------- source links

ZWIFT_LINK = "zwift.com/uk/athlete"
ZP_LINK = "zwiftpower.com/profile.php"
ZR_LINK = "zwiftracing.app/riders"


@pytest.mark.django_db
def test_all_three_source_links_render_for_a_synced_rider(client, rider, team_member):
    """Zwift, ZwiftPower and ZwiftRacing -- the three places this data is merged from."""
    now = timezone.now()
    RiderProfile.objects.create(
        zwid=rider.zwid, club_name="COALITION", zwift_user_id="41c49fb6-uuid",
        fetched_at=now, last_requested_at=now,
    )

    body = _body(client, team_member, rider)

    assert f"{ZWIFT_LINK}/41c49fb6-uuid" in body
    assert f"{ZP_LINK}?z={rider.zwid}" in body
    assert f"{ZR_LINK}/{rider.zwid}" in body


@pytest.mark.django_db
def test_the_links_survive_when_the_rider_has_not_been_synced(client, rider, team_member):
    """The state that regressed when three cards became one.

    The replaced cards each showed their link in the no-data branch as well as the data one,
    so a rider we hold nothing for could still be looked up at the source. Consolidating put
    the only link inside the data branch and turned that state into a dead end.

    The zwift.com link is the exception: it is keyed on zwift_user_id, which only the cached
    row carries, so it cannot appear before a sync.
    """
    body = _body(client, team_member, rider)

    assert "has not been synced" in body
    assert f"{ZP_LINK}?z={rider.zwid}" in body
    assert f"{ZR_LINK}/{rider.zwid}" in body
    assert ZWIFT_LINK not in body


@pytest.mark.django_db
def test_no_source_links_for_an_unverified_rider(client, user_model, team_member):
    """An unverified zwid is a number the rider typed.

    Linking it out would assert an identity nobody has confirmed, and would do it on a page
    other members read. The replaced cards drew the same line.
    """
    now = timezone.now()
    unverified = user_model.objects.create_user(
        username="unlinked", email="unlinked@example.test", zwid=999, zwid_verified=False,
    )
    RiderProfile.objects.create(
        zwid=999, zwift_user_id="should-not-link", fetched_at=now, last_requested_at=now,
    )

    body = _body(client, team_member, unverified)

    for fragment in (ZWIFT_LINK, ZP_LINK, ZR_LINK):
        assert fragment not in body, f"{fragment} linked for an unverified rider"


@pytest.mark.django_db
def test_the_zwift_link_is_omitted_when_the_account_id_is_unknown(client, rider, team_member):
    """zwift_user_id is the Zwift account UUID; without it there is no URL to build."""
    now = timezone.now()
    RiderProfile.objects.create(
        zwid=rider.zwid, club_name="COALITION", fetched_at=now, last_requested_at=now,
    )

    body = _body(client, team_member, rider)

    assert ZWIFT_LINK not in body
    assert f"{ZP_LINK}?z={rider.zwid}" in body


@pytest.mark.django_db
def test_the_edit_page_carries_the_same_three_links(client, rider):
    """A rider reaches their own source profiles from the page they already have open."""
    now = timezone.now()
    RiderProfile.objects.create(
        zwid=rider.zwid, club_name="COALITION", zwift_user_id="41c49fb6-uuid",
        fetched_at=now, last_requested_at=now,
    )

    body = _edit_body(client, rider)

    assert f"{ZWIFT_LINK}/41c49fb6-uuid" in body
    assert f"{ZP_LINK}?z={rider.zwid}" in body
    assert f"{ZR_LINK}/{rider.zwid}" in body


# ---------------------------------------------------------------- lifetime totals units


@pytest.mark.django_db
def test_lifetime_distance_is_converted_from_metres(profile):
    """``totals.distance_km`` is misnamed: zauth passes ZwiftPower's metres through unchanged.

    ``"distance_km": zp.distance`` in the zauth builder, and ZwiftPower reports metres -- our
    own ZPTeamRiders.distance help_text says "Total distance in meters".
    """
    assert profile.totals["distance_km"] == 63988161  # metres, as stored
    assert profile.lifetime_distance_km == pytest.approx(63988.161)


@pytest.mark.django_db
def test_lifetime_climbed_is_not_converted(profile):
    """Its neighbour genuinely is metres, so converting both would break this one."""
    assert profile.lifetime_climbed_m == 483132


@pytest.mark.django_db
def test_a_real_lifetime_total_renders_as_a_believable_distance(client, rider, team_member):
    """The reported bug: a real rider's total rendered as 37,209,725 km.

    Uses the value from the live site rather than a rounder one, so the test fails the way a
    person noticed it rather than in a shape only a test would produce.
    """
    now = timezone.now()
    RiderProfile.objects.create(
        zwid=rider.zwid,
        payload={"totals": {"distance_km": 37209725, "climbed_m": 400000}},
        fetched_at=now,
        last_requested_at=now,
    )

    body = _body(client, team_member, rider)

    assert "37,210 km" in body
    assert "37,209,725 km" not in body


@pytest.mark.django_db
def test_the_totals_accessors_tolerate_a_missing_or_non_numeric_block(rider):
    """Absent totals must not raise inside a division."""
    now = timezone.now()
    bare = RiderProfile.objects.create(zwid=rider.zwid, payload={}, fetched_at=now, last_requested_at=now)
    odd = RiderProfile.objects.create(
        zwid=rider.zwid + 1,
        payload={"totals": {"distance_km": "lots", "climbed_m": None}},
        fetched_at=now,
        last_requested_at=now,
    )

    assert bare.lifetime_distance_km is None
    assert bare.lifetime_climbed_m is None
    assert odd.lifetime_distance_km is None
    assert odd.lifetime_climbed_m is None

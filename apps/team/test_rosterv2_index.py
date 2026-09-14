"""What the roster index may show, and whose account it may show it under.

Two questions here are worth more than the rest of the page put together, because both fail
silently and both concern people who never signed up for any of this:

* can a rider's weight, height or exact age reach the page, by any route; and
* can one rider's racing end up under another rider's name and face.

The tests are written to fail for exactly one reason each, and several of them exist because
a plausible implementation passes the obvious version of the test while leaking.
"""

from dataclasses import FrozenInstanceError, fields
from datetime import timedelta

import pytest
from constance.test import override_config
from django.db import connection
from django.template import Context as TemplateContext
from django.template import Template, TemplateSyntaxError
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import GuildMember
from apps.rider_data.models import RiderProfile
from apps.team.rosterv2 import (
    ACCOUNT_COLUMNS,
    AGE_BRACKETS_SHOWN,
    CARD_COLUMNS,
    AccountFacts,
    RiderCard,
    RosterIndex,
    RosterRow,
    _verified_claimants,
    build_roster_index,
)
from conftest import _make_user

# --- the allow-list --------------------------------------------------------------------

EXPECTED_CARD_FIELDS = (
    "zwid",
    "name",
    "gender",
    "country",
    "age_bracket",
    "category_open",
    "category_women",
    "category_racing",
    "phenotype",
    "velo",
    "velo_max90",
    "zwift_racing_score",
    "zp_skill",
    "compound_score",
    "zftp",
    "wkg_20min",
    "wkg_1min",
    "distance_km",
    "climbed_m",
    "club_name",
    "last_race_at",
    "races_recent",
    "time_trials_recent",
    "rides_recent",
    "podiums_recent",
    "wins_recent",
)

FORBIDDEN = ("weight_kg", "height_cm", "birth_year", "email", "payload")


def test_the_card_declares_exactly_these_fields():
    """A new field on the card is a privacy decision, so it must cost a deliberate test edit."""
    assert tuple(f.name for f in fields(RiderCard)) == EXPECTED_CARD_FIELDS, (
        "The card is the page's allow-list. Adding a field here publishes it to every team "
        "member for ~2,000 riders, most of whom never registered. Decide, then edit this."
    )


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_the_forbidden_values_are_not_selected(forbidden):
    """Neither the card nor the SELECT list may name the rider's body or the raw payload."""
    assert forbidden not in {f.name for f in fields(RiderCard)}
    assert forbidden not in ACCOUNT_COLUMNS
    if forbidden != "payload":  # payload is read for W/kg, then dropped -- see the module docstring
        assert forbidden not in CARD_COLUMNS


def test_the_forbidden_names_are_real_model_fields():
    """Guards the guard: a typo'd column name would make every test above vacuously true."""
    profile_fields = {f.name for f in RiderProfile._meta.get_fields()}
    for name in ("weight_kg", "height_cm", "payload"):
        assert name in profile_fields, f"{name} is no longer a RiderProfile field; the tests above now prove nothing"


def test_every_selected_column_exists():
    """A misspelled column in the allow-list would raise at query time, not here."""
    profile_fields = {f.name for f in RiderProfile._meta.get_fields()}
    assert set(CARD_COLUMNS) <= profile_fields


@pytest.mark.django_db
def test_weight_and_height_never_reach_the_page(auth_client, roster_rider):
    """The end-to-end version: a rider whose row really does carry both."""
    roster_rider(zwid=4242, name="Ada Racer", weight_kg=91.7, height_cm=203.4)

    response = auth_client.get(reverse("team:rosterv2"))
    body = response.content.decode()
    rendered_context = repr(response.context["roster"])

    assert "91.7" not in body
    assert "203.4" not in body
    assert "91.7" not in rendered_context
    assert "203.4" not in rendered_context


@pytest.mark.django_db
def test_a_card_cannot_be_given_a_field_it_does_not_declare():
    """Frozen and slotted, so "just attach one more value for the template" is not available."""
    card = RiderCard(zwid=1, name="Ada")

    with pytest.raises((TypeError, AttributeError)):
        card.weight_kg = 91.7
    assert not hasattr(card, "__dict__")
    # slots alone refuses only an UNDECLARED name. frozen is what makes the card a value, so
    # a card handed to a template cannot be edited on the way there.
    with pytest.raises(FrozenInstanceError):
        card.name = "someone else"


# --- the search haystack: usable here, unrenderable there ---------------------------------
#
# The zwid used to live under this heading. It is shown now -- Zwift's public id for a rider,
# printed by the roster this page replaces and accepted by the search box above these cards.
# The haystack still needs hiding: it is every name a rider is known by, their legal name and
# Discord handle included, gathered so a search can match on a name the card never displays.


@pytest.mark.django_db
def test_a_template_cannot_look_the_search_haystack_up_by_name():
    row = RosterRow(card=RiderCard(zwid=8675309, name="Ada"), _search=(("ada r", "Ada R"),))

    with pytest.raises(TemplateSyntaxError):
        Template("{{ row._search }}").render(TemplateContext({"row": row}))


@pytest.mark.django_db
def test_rendering_a_row_or_a_list_of_them_never_prints_the_haystack():
    """The leading underscore alone does NOT do this, which is why repr=False is on the field.

    Django refuses attribute lookup by name, but a container renders each element's repr --
    measured: a frozen dataclass without repr=False renders [Row(_search=[...], ...)] even
    with __str__ defined, because list.__repr__ does not consult it.
    """
    card = RiderCard(zwid=8675309, name="Ada")
    row = RosterRow(
        card=card,
        account=AccountFacts(user_id=7, discord_name="ada"),
        _search=(("ada lovelace", "Ada Lovelace"),),
    )
    index = RosterIndex(rows=(row,))

    for template, context in (
        ("{{ row }}", {"row": row}),
        ("{{ rows }}", {"rows": [row]}),
        ("{{ row|pprint }}", {"row": row}),
        ("{{ index }}", {"index": index}),
        ("{{ index.rows }}", {"index": index}),
    ):
        rendered = Template(template).render(TemplateContext(context))
        assert "Lovelace" not in rendered, f"{template} leaked the haystack: {rendered}"

    # repr=False is what hides the haystack; __str__ is what makes the visible half a name
    # rather than a dump of the whole row. Both are load-bearing, so both are pinned.
    assert Template("{{ card }}").render(TemplateContext({"card": card})) == "Ada"
    assert Template("{{ row }}").render(TemplateContext({"row": row})) == "Ada"


@pytest.mark.django_db
def test_the_page_prints_the_zwid(auth_client, roster_rider):
    """Vincent's call, and consistent with the roster this page replaces, which prints it too."""
    roster_rider(zwid=8675309, name="Ada Racer")

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "8675309" in body


# --- who is on the roster -----------------------------------------------------------------


@pytest.mark.django_db
def test_a_cached_rider_who_no_longer_races_for_the_team_has_no_card(rider_profile_factory):
    """The cache is not the roster: nothing purges it, so it keeps riders who have left."""
    rider_profile_factory(zwid=1001, name="Departed Rider")

    index = build_roster_index()

    assert index.rows == ()


@pytest.mark.django_db
def test_a_rider_on_the_team_page_gets_a_card(roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")

    index = build_roster_index()

    assert [row.card.name for row in index.rows] == ["Ada Racer"]
    assert index.rider_count == 1


@pytest.mark.django_db
def test_a_team_rider_we_hold_no_stats_for_has_no_card(zp_team_rider_factory):
    """No cached row, no card -- the page is riders we have Zwift data for."""
    zp_team_rider_factory(zwid=1001)

    assert build_roster_index().rows == ()


@pytest.mark.django_db
def test_a_nameless_rider_is_not_labelled_with_their_zwid(roster_rider):
    """v1 falls back to f"Rider {zwid}", which prints the one value this page may not show."""
    roster_rider(zwid=1002, identity={"name": ""})

    card = build_roster_index().rows[0].card

    assert card.name == "Unknown rider"
    assert "1002" not in card.name


@pytest.mark.django_db
def test_the_order_is_the_same_on_every_request(roster_rider):
    """An unstable order moves page boundaries, which shows one rider twice and skips another."""
    roster_rider(zwid=1001, name="Zoe Racer")
    roster_rider(zwid=1002, name="ada racer")
    roster_rider(zwid=1003, name="Ada Racer")

    first = [row.card.name for row in build_roster_index().rows]
    second = [row.card.name for row in build_roster_index().rows]

    assert first == second
    # Folded, so "ada racer" is not exiled below "Zoe Racer" the way a byte sort would do it.
    assert first[-1] == "Zoe Racer"


# --- age ----------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("bracket", ["Snr", "U23", "Vet", "Mas", "50+", "60+", "70+", "Jnr"])
def test_a_real_racing_bracket_is_shown(roster_rider, bracket):
    """Jnr included: Vincent overruled hiding it. The rest are the upstream vocabulary."""
    roster_rider(zwid=1001, age=bracket)

    assert build_roster_index().rows[0].card.age_bracket == bracket


@pytest.mark.django_db
@pytest.mark.parametrize("bracket", ["-", "", "X99", "vet", " Vet "])
def test_anything_that_is_not_a_known_bracket_is_blanked(roster_rider, bracket):
    """An allow-list, so an unrecognised label fails closed instead of being printed raw."""
    roster_rider(zwid=1001, age=bracket)

    assert build_roster_index().rows[0].card.age_bracket == ""


def test_the_bracket_list_holds_no_exact_age():
    """A bracket is a range; a number here would be the thing the page promises not to show."""
    assert not any(bracket.isdigit() for bracket in AGE_BRACKETS_SHOWN)


# --- W/kg, and the weight it must not recover ---------------------------------------------


@pytest.mark.django_db
def test_wkg_is_read_from_the_curve_and_never_derived_from_weight(roster_rider):
    """Zftp / wkg would be the rider's weight. These numbers disagree on purpose."""
    roster_rider(zwid=1001, ftp=250.0, weight_kg=91.7, wkg_20min=4.1)

    card = build_roster_index().rows[0].card

    assert card.wkg_20min == pytest.approx(4.1)
    assert card.zftp == pytest.approx(250.0)


@pytest.mark.django_db
def test_the_watts_curve_never_reaches_a_card(roster_rider):
    """curve_w divided by curve_wkg at the same duration IS the stored weight, exactly."""
    rider = roster_rider(zwid=1001, wkg_20min=4.0, weight_kg=80.0)
    assert rider.payload["power"]["curve_w"]["1200"] == 320  # the factory built the pair

    card = build_roster_index().rows[0].card

    assert 320 not in {card.zftp, card.wkg_20min, card.wkg_1min}
    assert "320" not in repr(card)


@pytest.mark.django_db
def test_a_rider_with_no_zwiftracing_data_has_no_curve_and_no_peak(roster_rider):
    """Half the roster. None, not 0.0 and not a KeyError -- the card shows an em dash."""
    roster_rider(zwid=1001, wkg_20min=None, wkg_1min=None, velo=None)

    card = build_roster_index().rows[0].card

    assert card.wkg_20min is None
    assert card.wkg_1min is None
    assert card.velo_max90 is None


@pytest.mark.django_db
def test_wkg_is_rounded_to_one_decimal(roster_rider):
    """A second decimal narrows the weight band recoverable from zFTP divided by W/kg."""
    roster_rider(zwid=1001, wkg_20min=3.567)

    assert build_roster_index().rows[0].card.wkg_20min == pytest.approx(3.6)


@pytest.mark.django_db
def test_lifetime_distance_agrees_with_the_model_accessor(roster_rider):
    """Both convert the metres that upstream mislabels as km; they must not diverge."""
    roster_rider(zwid=1001)

    card = build_roster_index().rows[0].card

    assert card.distance_km == pytest.approx(RiderProfile.objects.get(zwid=1001).lifetime_distance_km)
    assert card.climbed_m == pytest.approx(RiderProfile.objects.get(zwid=1001).lifetime_climbed_m)


# --- whose card is whose ------------------------------------------------------------------


def _member(user_model, username, zwid, *, verified=True, method="zauth", **extra):
    user = _make_user(user_model, username=username, permissions={"team_member": True}, **extra)
    user.zwid = zwid
    user.zwid_verified = verified
    user.zwid_verification_method = method
    user.discord_id = f"90000{zwid}"
    user.discord_avatar = "abc123"
    user.discord_username = username
    user.save()
    return user


@pytest.mark.django_db
def test_a_verified_rider_joins_their_card(roster_rider, user_model):
    """The positive control: without it, a gate nobody passes would look perfect."""
    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "ada", 4242)

    row = build_roster_index().rows[0]

    assert row.account is not None
    assert row.account.user_id == user.pk
    assert row.account.discord_name == "ada"
    assert row.account.avatar_url.startswith("https://cdn.discordapp.com/avatars/")


@pytest.mark.django_db
def test_an_unverified_claim_joins_nothing(roster_rider, user_model):
    """An unverified zwid is a number somebody typed into a box."""
    roster_rider(zwid=4242, name="Ada Racer")
    _member(user_model, "impostor", 4242, verified=False)

    index = build_roster_index()

    assert index.rows[0].account is None
    assert index.joined_count == 0


@pytest.mark.django_db
def test_an_unverified_rider_lends_no_avatar_or_discord_name_to_the_page(auth_client, roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    _member(user_model, "impostor", 4242, verified=False)

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "cdn.discordapp.com" not in body
    assert "impostor" not in body


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_a_legacy_verification_stops_joining_once_zauth_is_required(roster_rider, user_model):
    """Reading the raw zwid_verified column looks identical today and ignores the cutover."""
    roster_rider(zwid=4242, name="Ada Racer")
    _member(user_model, "ada", 4242, method="legacy")

    assert build_roster_index().rows[0].account is None


@pytest.mark.django_db
def test_a_legacy_verification_still_joins_before_the_cutover(roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    _member(user_model, "ada", 4242, method="legacy")

    assert build_roster_index().rows[0].account is not None


@pytest.mark.django_db
def test_two_verified_accounts_on_one_zwid_join_neither(roster_rider, user_model):
    """Fail closed. Picking a winner puts one rider's identity over another's results."""
    roster_rider(zwid=4242, name="Ada Racer")
    _member(user_model, "ada", 4242)
    _member(user_model, "bo", 4242)

    index = build_roster_index()

    assert index.rows[0].account is None
    assert index.contested_count == 1
    assert index.joined_count == 0


@pytest.mark.django_db
def test_an_unverified_claim_cannot_dislodge_the_verified_rider(roster_rider, user_model):
    """Counting claimants BEFORE filtering by verification would let anyone blank a card."""
    roster_rider(zwid=4242, name="Ada Racer")
    real = _member(user_model, "ada", 4242)
    _member(user_model, "griefer", 4242, verified=False)

    index = build_roster_index()

    assert index.rows[0].account is not None
    assert index.rows[0].account.user_id == real.pk
    assert index.contested_count == 0


@pytest.mark.django_db
def test_members_at_the_zero_sentinel_are_not_collected_as_one_rider(user_model):
    """zwid=0 is this app's "no zwid here", so a claim on it would merge every such member.

    Asserted against the claim map rather than the page: no card can exist at zwid 0 today,
    because every arm of zwids_to_refresh() excludes it, so the roster cannot show the
    difference. The guard is defence in depth and this is where it can be seen.
    """
    _member(user_model, "nozwid_one", 0)
    _member(user_model, "nozwid_two", 0)
    _member(user_model, "ada", 4242)

    claimants = _verified_claimants(zauth_required=False)

    assert 0 not in claimants
    assert list(claimants) == [4242]


@pytest.mark.django_db
def test_a_rider_with_no_account_still_gets_their_racing_card(roster_rider):
    roster_rider(zwid=4242, name="Ada Racer")

    row = build_roster_index().rows[0]

    assert row.account is None
    assert row.card.name == "Ada Racer"
    assert row.card.velo is not None


# --- the account half ---------------------------------------------------------------------


@pytest.mark.django_db
def test_member_since_comes_from_the_current_discord_stint(roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "ada", 4242)
    joined = timezone.now() - timedelta(days=400)
    GuildMember.objects.create(discord_id=user.discord_id, username="ada", user=user, joined_at=joined)

    assert build_roster_index().rows[0].account.member_since == joined


@pytest.mark.django_db
def test_a_departed_members_tenure_is_not_shown(roster_rider, user_model):
    """Their row survives the sync, so filtering on date_left is what makes the date honest."""
    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "ada", 4242)
    GuildMember.objects.create(
        discord_id=user.discord_id,
        username="ada",
        user=user,
        joined_at=timezone.now() - timedelta(days=400),
        date_left=timezone.now() - timedelta(days=5),
    )

    assert build_roster_index().rows[0].account.member_since is None


@pytest.mark.django_db
def test_the_server_nickname_wins_over_the_username(roster_rider, user_model):
    roster_rider(zwid=4242, name="Ada Racer")
    user = _member(user_model, "ada", 4242)
    GuildMember.objects.create(
        discord_id=user.discord_id, username="ada", display_name="Ada L", nickname="Ada [COALITION]", user=user
    )

    assert build_roster_index().rows[0].account.discord_name == "Ada [COALITION]"


# --- cost, and what a page render may not do ------------------------------------------------


@pytest.mark.django_db
def test_the_index_writes_nothing(roster_rider, user_model):
    """refresh_race_ready() saves the User row, so calling it here would write on every GET."""
    roster_rider(zwid=4242, name="Ada Racer")
    _member(user_model, "ada", 4242)

    with CaptureQueriesContext(connection) as captured:
        build_roster_index()

    statements = [q["sql"].lstrip().upper() for q in captured.captured_queries]
    writes = [s for s in statements if s.startswith(("UPDATE", "INSERT", "DELETE"))]
    # Constance's database backend inserts its own default row the first time a setting is
    # read. That is its bookkeeping, not this page writing rider data.
    assert not [s for s in writes if "CONSTANCE" not in s], writes


@pytest.mark.django_db
def test_the_index_costs_the_same_however_many_riders(roster_rider, user_model):
    """Equality, not <=: one Constance read or one join moved into the loop breaks this."""
    for n in range(3):
        roster_rider(zwid=1000 + n, name=f"Rider {n}")
        _member(user_model, f"r{n}", 1000 + n)
    build_roster_index()  # Constance inserts its default row on the first read of a setting
    with CaptureQueriesContext(connection) as few:
        small = build_roster_index()

    for n in range(3, 30):
        roster_rider(zwid=1000 + n, name=f"Rider {n}")
        _member(user_model, f"r{n}", 1000 + n)
    with CaptureQueriesContext(connection) as many:
        large = build_roster_index()

    assert len(many) == len(few)
    assert large.rider_count > small.rider_count > 0


@pytest.mark.django_db
def test_the_index_returns_cards_and_not_model_instances(roster_rider):
    """A RiderProfile in the context would put .weight_kg one attribute lookup away."""
    roster_rider(zwid=1001)

    for row in build_roster_index().rows:
        assert isinstance(row, RosterRow)
        assert isinstance(row.card, RiderCard)

"""The per-category coverage rule behind every expiring/lapsed/missing warning.

The bug these pin: the captain's squad-mate warning list (then headed "Expiring verifications
in your squads", now "Verifications to chase in your squads") judged each
verify_type on its own, with no idea which types the rider's ZwiftPower category actually
demands. A category-40 rider holding a valid ``weight_light`` and an old ``weight_full`` was
listed as "expired 145 days ago" beside a green Race Verified badge -- while a rider who could
NOT race, because a required type was missing, was left off the list entirely as long as they
held one verified record of something else.

So the assertion that matters most is the last one in most of these tests:
``coverage.covered_now == user.calculate_race_ready()``. That equality is the guard against
the two rules drifting apart again.
"""

from datetime import timedelta

import pytest
from constance.test import override_config
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, Squad, SquadMember
from apps.team.services import (
    requirements_for,
    superseded_weight_types,
    verification_coverage_bulk,
)

# Categories, by ZwiftPower division, as the shipped CATEGORY_REQUIREMENTS default defines them.
DIV_B = 20  # weight_full + height -- the light one does not satisfy it
DIV_D = 40  # weight_full + weight_light + height -- EITHER weight satisfies it


@pytest.fixture(autouse=True)
def _clear_cache():
    """Drop the per-user banner caches between tests.

    Both expiring processors cache for six minutes, so without this one test's count is read
    by the next and the failure lands on whichever ran second.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def squad(db) -> Squad:
    """Build an opted-in squad on a running, visible event.

    Returns:
        The squad.

    """
    today = timezone.localdate()
    event = Event.objects.create(
        title="ZRL", start_date=today - timedelta(days=7), end_date=today + timedelta(days=30), visible=True
    )
    return Squad.objects.create(event=event, name="Affinity", notify_captain_expiring_verification=True)


@pytest.fixture
def captain(user_model, squad):
    """Build the squad's captain.

    Returns:
        The captain.

    """
    user = user_model.objects.create_user(
        username="cap",
        email="cap@example.test",
        first_name="Cap",
        last_name="Tain",
        permission_overrides={"team_member": True},
    )
    squad.captains.add(user)
    return user


@pytest.fixture
def second_squad(captain) -> Squad:
    """Build a SECOND opted-in squad, on its own event, captained by the same rider.

    A captain of several squads is the other axis ``squad_expiring_summary`` loops over, and
    the one a ZRL captain actually exercises.

    Returns:
        The squad.

    """
    today = timezone.localdate()
    event = Event.objects.create(
        title="ZRL B", start_date=today - timedelta(days=7), end_date=today + timedelta(days=30), visible=True
    )
    squad = Squad.objects.create(event=event, name="Tenacity", notify_captain_expiring_verification=True)
    squad.captains.add(captain)
    return squad


@pytest.fixture
def rider_factory(user_model, squad, zp_team_rider_factory, verification_factory):
    """Build a squad member with a ZwiftPower category and a set of verified records.

    Returns:
        ``make(name, div=..., divw=..., gender=..., records={verify_type: days_ago}, squad=)``,
        which returns the rider. ``days_ago`` counts back from today, so a weight_light
        (30-day window) at 35 days ago has lapsed by 5. ``squad`` defaults to the fixture.

    """
    counter = {"n": 0}
    default_squad = squad

    def _make(
        name: str,
        *,
        div: int | None = None,
        divw: int = 0,
        gender: str = "male",
        records=None,
        squad: Squad | None = None,
    ):
        counter["n"] += 1
        zwid = 500_000 + counter["n"]
        user = user_model.objects.create_user(
            username=name.lower(),
            email=f"{name.lower()}@example.test",
            first_name=name,
            last_name="Rider",
            gender=gender,
            zwid=zwid if div is not None else None,
        )
        if div is not None:
            zp_team_rider_factory(zwid=zwid, div=div, divw=divw)
        SquadMember.objects.create(squad=squad or default_squad, user=user, status=SquadMember.Status.MEMBER)
        for verify_type, days_ago in (records or {}).items():
            verification_factory(user, verify_type, days_ago=days_ago)
        user.refresh_race_ready()
        return user

    return _make


def _coverage(user):
    """Judge one rider through the batched helper.

    Args:
        user: The rider.

    Returns:
        Their :class:`~apps.team.services.VerificationCoverage`.

    """
    return verification_coverage_bulk([user])[user.pk]


def _banner(client, viewer) -> str:
    """Render any page and return the body, so the banner can be inspected.

    Args:
        client: Test client.
        viewer: The signed-in user.

    Returns:
        The response body.

    """
    client.force_login(viewer)
    return client.get(reverse("accounts:profile")).content.decode()


def _rows(captain, squad):
    """Return the captain's modal rows for one squad.

    Args:
        captain: The signed-in captain.
        squad: The squad whose rows to return.

    Returns:
        The row dicts, or an empty list when the squad is absent from the summary.

    """
    from apps.team.services import squad_expiring_summary

    for group in squad_expiring_summary(captain)["squads"]:
        if group["squad"].pk == squad.pk:
            return group["rows"]
    return []


# --- the reported case, both ways round ------------------------------------------------


@pytest.mark.django_db
def test_a_d_rider_with_a_valid_light_weight_is_not_flagged_for_their_lapsed_full(captain, squad, rider_factory):
    """The reported bug, verbatim: "a weight full but an expired lite" -- and its mirror.

    Category 40 accepts EITHER weight. A rider whose light weight is valid is covered, so the
    full one lapsing 145 days ago is not a thing to chase, and listing it told the captain to
    chase a rider who could race.
    """
    rider = rider_factory("Ana", div=DIV_D, records={"weight_light": 5, "weight_full": 265, "height": 400})

    coverage = _coverage(rider)

    assert coverage.covered_now is True
    assert coverage.state == "ok"
    assert coverage.covered_now == rider.calculate_race_ready()
    assert _rows(captain, squad) == []


@pytest.mark.django_db
def test_a_d_rider_with_a_valid_full_weight_is_not_flagged_for_their_lapsed_light(captain, squad, rider_factory):
    """The other half of the same sentence: a valid full weight, an expired light one."""
    rider = rider_factory("Bea", div=DIV_D, records={"weight_full": 10, "weight_light": 110, "height": 400})

    coverage = _coverage(rider)

    assert coverage.covered_now is True
    assert coverage.state == "ok"
    assert coverage.covered_now == rider.calculate_race_ready()
    assert _rows(captain, squad) == []


@pytest.mark.django_db
def test_a_b_rider_whose_required_full_weight_lapsed_is_still_flagged(captain, squad, rider_factory):
    """Category 20 does NOT accept the light one, so this rider genuinely cannot race.

    The fix must narrow the list to required coverage, not silence weight warnings.
    """
    rider = rider_factory("Cal", div=DIV_B, records={"weight_light": 5, "weight_full": 265, "height": 400})

    coverage = _coverage(rider)
    rows = _rows(captain, squad)

    assert coverage.covered_now is False
    assert coverage.state == "lapsed"
    assert coverage.days == -145
    assert coverage.requirement == "Weight (Full)"
    assert coverage.covered_now == rider.calculate_race_ready()
    assert [(r["state"], r["days"], r["verify_type"]) for r in rows] == [("lapsed", -145, "Weight (Full)")]


# --- types the category never asked for ------------------------------------------------


@pytest.mark.django_db
def test_a_lapsed_record_of_a_type_the_category_does_not_require_is_ignored(captain, squad, rider_factory):
    """A category-20 rider's 535-day-old power record costs them nothing.

    It was being flagged, and the rider it named was race ready throughout.
    """
    rider = rider_factory("Dee", div=DIV_B, records={"weight_full": 10, "height": 400, "power": 900})

    coverage = _coverage(rider)

    assert coverage.covered_now is True
    assert coverage.state == "ok"
    assert coverage.covered_now == rider.calculate_race_ready()
    assert _rows(captain, squad) == []


@pytest.mark.django_db
def test_a_rider_missing_a_required_type_is_flagged_although_they_hold_another(captain, squad, rider_factory):
    """The false negative: one verified record used to be enough to stay off the list.

    This rider holds a never-expiring height and no weight at all. They cannot race, and their
    captain could not see it.
    """
    rider = rider_factory("Eve", div=DIV_B, records={"height": 400})

    coverage = _coverage(rider)
    rows = _rows(captain, squad)

    assert coverage.covered_now is False
    assert coverage.state == "none"
    assert coverage.requirement == "Weight (Full)"
    assert coverage.holds_any is True  # they hold something, just not the required thing
    assert coverage.covered_now == rider.calculate_race_ready()
    assert [(r["state"], r["verify_type"], r["missing_all"]) for r in rows] == [("none", "Weight (Full)", False)]


@pytest.mark.django_db
def test_a_rider_holding_nothing_at_all_still_reads_as_nothing_verified(client, captain, squad, rider_factory):
    """Widening the state must not lose the wording for the rider who never started."""
    rider_factory("Fay", div=DIV_B)

    rows = _rows(captain, squad)
    client.force_login(captain)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()
    row_html = body[body.index("Fay Rider") : body.index("</li>", body.index("Fay Rider"))]

    assert rows[0]["missing_all"] is True
    assert "nothing verified" in row_html


# --- never-expiring requirements -------------------------------------------------------


@pytest.mark.django_db
def test_a_requirement_that_never_expires_does_not_bound_the_days(captain, squad, rider_factory):
    """HEIGHT_VERIFICATION_DAYS=0 means +infinity, not "unknown" and not "expired".

    If a never-expiring requirement were allowed into the minimum it would either swallow the
    real deadline or be read as a lapse; the rider's days must come from the weight record.
    """
    rider = rider_factory("Gus", div=DIV_B, records={"weight_full": 115, "height": 900})

    with override_config(HEIGHT_VERIFICATION_DAYS=0):
        coverage = _coverage(rider)

    assert coverage.days == 5
    assert coverage.requirement == "Weight (Full)"
    assert coverage.state == "expiring"
    assert coverage.covered_now is True


@pytest.mark.django_db
def test_a_rider_whose_every_requirement_never_expires_has_no_days(captain, squad, rider_factory):
    """Covered forever is "ok" with no number, not "expiring in None days"."""
    rider = rider_factory("Hal", div=DIV_B, records={"weight_full": 5, "height": 900})

    with override_config(HEIGHT_VERIFICATION_DAYS=0, WEIGHT_FULL_DAYS=0):
        coverage = _coverage(rider)

    assert coverage.state == "ok"
    assert coverage.days is None
    assert coverage.requirement is None
    assert coverage.covered_now is True
    assert _rows(captain, squad) == []


@pytest.mark.django_db
def test_a_verified_record_with_no_record_date_never_expires(captain, squad, rider_factory):
    """There is no date to count from, so it cannot be past anything.

    Built directly rather than through ``verification_factory``, whose ``record_date=None``
    means "default to today" rather than "leave it empty".
    """
    from apps.team.models import RaceReadyRecord

    rider = rider_factory("Ivy", div=DIV_B, records={"height": 400})
    RaceReadyRecord.objects.create(
        user=rider,
        verify_type="weight_full",
        media_type="link",
        url="https://example.test/evidence",
        status=RaceReadyRecord.Status.VERIFIED,
        record_date=None,
    )
    rider.refresh_race_ready()

    coverage = _coverage(rider)

    assert coverage.state == "ok"
    assert coverage.days is None
    assert coverage.covered_now is True
    assert coverage.covered_now == rider.calculate_race_ready()


# --- the rule's own edges --------------------------------------------------------------


@pytest.mark.django_db
def test_an_either_weight_category_collapses_to_one_requirement():
    """The OR rule lives in requirements_for and nowhere else."""
    either = requirements_for(["weight_full", "weight_light", "height"])
    strict = requirements_for(["weight_full", "height"])

    assert [(sorted(r.types), r.label) for r in either] == [
        (["weight_full", "weight_light"], "Weight"),
        (["height"], "Height"),
    ]
    assert [(sorted(r.types), r.label) for r in strict] == [
        (["weight_full"], "Weight (Full)"),
        (["height"], "Height"),
    ]


@pytest.mark.django_db
def test_a_female_rider_with_no_womens_division_falls_back_to_the_default(captain, squad, rider_factory):
    """Documenting today's rule, not endorsing it.

    ``ZPTeamRiders.divw`` defaults to 0, so "no women's division recorded" is indistinguishable
    from "no data" and she gets DEFAULT_VERIFICATION_TYPES -- weight_light, NOT the weight_full
    her open division would demand. Whether divw=0 should fall back to div is the owner's call;
    what must not happen is this helper answering it differently from calculate_race_ready.
    """
    rider = rider_factory("Jo", div=DIV_B, divw=0, gender="female", records={"weight_full": 10, "height": 400})

    coverage = _coverage(rider)

    assert coverage.covered_now is False
    assert coverage.state == "none"
    assert coverage.requirement == "Weight (Light)"
    assert coverage.covered_now == rider.calculate_race_ready()


@pytest.mark.django_db
def test_equal_days_supersede_nothing():
    """Both records are holding the requirement up; silencing one arbitrarily loses a warning."""
    required = ["weight_full", "weight_light", "height"]

    assert superseded_weight_types(required, {"weight_full": 5, "weight_light": 5}) == set()
    assert superseded_weight_types(required, {"weight_full": 5, "weight_light": 30}) == {"weight_full"}
    assert superseded_weight_types(required, {"weight_full": 30, "weight_light": 5}) == {"weight_light"}
    # A never-expiring record covers the requirement forever.
    assert superseded_weight_types(required, {"weight_full": None, "weight_light": 5}) == {"weight_light"}
    # A category that accepts only one weight can never supersede across the two.
    assert superseded_weight_types(["weight_full", "height"], {"weight_full": 5, "weight_light": 30}) == set()


# --- the banner, the modal and the cost ------------------------------------------------


@pytest.mark.django_db
def test_the_banner_count_equals_the_modals_distinct_riders(client, captain, squad, rider_factory):
    """Two implementations of one number is how this reads as a bug again."""
    rider_factory("Kim", div=DIV_B, records={"weight_full": 115, "height": 400})  # expiring in 5
    rider_factory("Lou", div=DIV_B, records={"height": 400})  # missing a required weight
    rider_factory("Moe", div=DIV_D, records={"weight_light": 5, "weight_full": 265, "height": 400})  # fine

    from apps.team.services import squad_expiring_summary

    summary = squad_expiring_summary(captain)
    body = _banner(client, captain)

    assert summary["rider_count"] == 2
    assert f"Remind your Squad-mates: {summary['rider_count']} Expiring" in body
    assert len({row["user"].pk for group in summary["squads"] for row in group["rows"]}) == 2


@pytest.mark.django_db
def test_the_required_types_lookup_does_not_scale_with_squad_size(captain, squad, second_squad, rider_factory):
    """The whole reason the category was not consulted before.

    ``get_user_required_verification_types`` is a ZwiftPower query plus a Constance read per
    rider, and this runs in a context processor on every authenticated render. The batch is one
    ZPTeamRiders query and one CATEGORY_REQUIREMENTS read for the whole roster, so ten times the
    riders must cost the same. EQUALITY, not ``<=``: a single reintroduced per-rider read is
    the entire regression, and these riders all HAVE a ZwiftPower row, so the batched path is
    the one being measured.

    BOTH axes: riders AND squads. ``squad_expiring_summary`` loops over the captain's squads as
    well as their members, so a Constance read or a records query moved inside
    ``for squad in squads`` would pass a riders-only check while costing a ZRL captain a query
    per squad on every render.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.team.services import squad_expiring_summary

    for i in range(2):
        rider_factory(f"Small{i}", div=DIV_D, records={"weight_light": 25, "height": 400})
    squad_expiring_summary(captain)  # warm Constance so its one-time inserts are not counted
    with CaptureQueriesContext(connection) as small:
        squad_expiring_summary(captain)

    for i in range(18):
        rider_factory(f"Big{i}", div=DIV_D, records={"weight_light": 25, "height": 400})
    # ...and the second squad, so the count is measured across squads too.
    for i in range(10):
        rider_factory(f"Other{i}", div=DIV_D, records={"weight_light": 25, "height": 400}, squad=second_squad)
    with CaptureQueriesContext(connection) as big:
        squad_expiring_summary(captain)

    assert len(big.captured_queries) == len(small.captured_queries), (
        f"{len(small.captured_queries)} queries for 2 riders in 1 squad, {len(big.captured_queries)} for 30 in 2"
    )


# --- the rider's own surfaces ----------------------------------------------------------


@pytest.mark.django_db
def test_the_riders_own_banner_does_not_nag_about_a_superseded_weight(client, rider_factory):
    """Same false alarm, aimed at the rider instead of their captain.

    Category 40 accepts either weight, so a full weight expiring in 5 days is not worth a
    banner while the light one covers that requirement for 25 more.
    """
    rider = rider_factory("Nia", div=DIV_D, records={"weight_full": 115, "weight_light": 5, "height": 400})
    rider.permission_overrides = {"team_member": True}
    rider.save(update_fields=["permission_overrides"])

    body = _banner(client, rider)

    assert "verification expires in" not in body


@pytest.mark.django_db
def test_the_riders_own_banner_still_warns_when_both_weights_are_going(client, rider_factory):
    """Superseded means "one covers the requirement for longer", not "there are two of them".

    Both of these weights have five days left, so neither supersedes the other and both are
    still holding the requirement up. Silencing one on a tie would drop a real warning.
    """
    rider = rider_factory("Oda", div=DIV_D, records={"weight_full": 115, "weight_light": 25, "height": 400})
    rider.permission_overrides = {"team_member": True}
    rider.save(update_fields=["permission_overrides"])

    body = _banner(client, rider)

    # The banner's sentence wraps across lines in the template, so match its two halves
    # rather than one string that only exists after HTML whitespace collapsing.
    assert "verification expires in" in body
    # Which of the two is named is a tie-break detail; that BOTH are counted is the point.
    assert "2 verifications expiring soon" in body


@pytest.mark.django_db
def test_the_expiry_dm_skips_a_superseded_weight_record(rider_factory):
    """A DM telling a covered rider to renew is the same lie in a different channel."""
    from apps.team.tasks import warn_expiring_verifications

    rider = rider_factory("Pia", div=DIV_D, records={"weight_full": 115, "weight_light": 5, "height": 400})
    rider.discord_id = "900000000000000001"
    rider.save(update_fields=["discord_id"])

    result = warn_expiring_verifications.func(dry_run=True)

    assert result["users_warned"] == []


@pytest.mark.django_db
def test_the_expiry_dm_still_warns_about_the_binding_weight_record(rider_factory):
    """The narrowing must not silence the record that IS holding the requirement up."""
    from apps.team.tasks import warn_expiring_verifications

    # The full weight has 5 days left, the light one 3, so the FULL one is what the
    # either-weight requirement rests on and the light one is superseded.
    rider = rider_factory("Rae", div=DIV_D, records={"weight_full": 115, "weight_light": 27, "height": 400})
    rider.discord_id = "900000000000000002"
    rider.save(update_fields=["discord_id"])

    result = warn_expiring_verifications.func(dry_run=True)

    assert len(result["users_warned"]) == 1
    assert "Weight Full" in result["users_warned"][0]


# --- what the modal actually prints ----------------------------------------------------


def _modal_row(client, captain, name: str) -> str:
    """Render the captain modal and return the one <li> for ``name``.

    Scoped to the row on purpose: the modal's intro sentence carries words like "lapsed" too,
    so an unscoped substring check passes even when no row rendered at all.

    Args:
        client: Test client.
        captain: The signed-in captain.
        name: The rider's first name.

    Returns:
        The row markup, or "" when that rider has no row.

    """
    client.force_login(captain)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()
    if name not in body:
        return ""
    start = body.index(name)
    return body[start : body.index("</li>", start)]


@pytest.mark.django_db
def test_an_either_weight_row_says_weight_not_one_of_the_two_types(client, captain, squad, rider_factory):
    """The label the whole fix exists to print, asserted in the HTML that is served.

    Category 40 is satisfied by either weight, so both being lapsed is ONE lapsed requirement.
    Naming a type here would send the captain chasing the wrong record -- and would be the
    old, per-type reading leaking back into the UI.
    """
    rider_factory("Sam", div=DIV_D, records={"weight_light": 40, "weight_full": 265, "height": 400})

    row = _modal_row(client, captain, "Sam Rider")

    # The light weight lapsed 10 days ago, the full one 145; the requirement rests on the
    # better of the two, so 10 is what the captain is told.
    assert "Weight expired 10 days ago" in row
    assert "Weight (Full)" not in row
    assert "Weight (Light)" not in row


@pytest.mark.django_db
def test_a_missing_requirement_row_names_the_requirement(client, captain, squad, rider_factory):
    """A rider holding SOMETHING but not what their category demands must be told which.

    "nothing verified" is reserved for a rider who holds no verified record at all; this one
    holds a height, so the row has to name the requirement that is absent.
    """
    rider_factory("Tia", div=DIV_B, records={"height": 400})

    row = _modal_row(client, captain, "Tia Rider")

    assert "no Weight (Full)" in row
    assert "nothing verified" not in row


@pytest.mark.django_db
def test_an_expiring_row_names_its_requirement_in_the_badge_text(client, captain, squad, rider_factory):
    """Not in a title= tooltip: a phone cannot show one, and this is the actionable half."""
    rider_factory("Uma", div=DIV_B, records={"weight_full": 115, "height": 400})

    row = _modal_row(client, captain, "Uma Rider")

    assert "Weight (Full)" in row
    assert "5 days" in row
    assert 'title="' not in row


@pytest.mark.django_db
def test_a_string_zwid_still_finds_the_riders_category(rider_factory):
    """The batched lookup keys on ints; the attribute may not hold one.

    The per-user version filtered in the ORM, which coerced whatever ``user.zwid`` held. The
    dict lookup that replaced it would miss on an in-memory instance carrying a string zwid
    (assigned from a form or an API response before save) and fall back to the default types
    -- and, because ``calculate_race_ready`` now routes through the same function, silently
    take Race Verified with it. No exception, no log: exactly the divergence worth pinning.
    """
    from apps.team.services import get_user_required_verification_types

    rider = rider_factory("Vik", div=DIV_D, records={"weight_full": 10, "height": 400})
    saved = get_user_required_verification_types(rider)

    rider.zwid = str(rider.zwid)

    assert get_user_required_verification_types(rider) == saved
    assert "weight_full" in saved  # the category's answer, not DEFAULT_VERIFICATION_TYPES
    assert rider.calculate_race_ready() is True

"""The roster's account-side filters: kit, event signup, zMAP, timezone, expiry and join dates.

Most of these read the account half of a card, so the rule that matters most is what a
rider with no account here matches: nothing, except "not signed up for". The expiry filter
is also a permission: who is about to lose Race Verified is the verification reviewers'
business, not every team member's.
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from constance.test import override_config
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import GuildMember
from apps.events.models import Event, EventSignup
from apps.team.models import RaceReadyRecord, TeamKit
from apps.team.rosterv2 import apply_filters, build_roster_index, filter_options, parse_filters
from conftest import _make_user


def _rows(**params):
    """Build the index, then narrow it the way the view does.

    Returns:
        The filtered rows.

    """
    index = build_roster_index()
    return apply_filters(list(index.rows), parse_filters(params, index.rows))


def _names(rows):
    return sorted(row.card.name for row in rows)


def _member(user_model, username, zwid, **fields):
    user = _make_user(user_model, username=username, permissions={"team_member": True})
    user.zwid = zwid
    user.zwid_verified = True
    user.zwid_verification_method = "zauth"
    user.discord_id = f"90000{zwid}"
    user.discord_username = username
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


def _event(title, *, show_signups=True, days_out=7):
    start = timezone.localdate() + timedelta(days=days_out)
    return Event.objects.create(
        title=title, start_date=start, end_date=start + timedelta(days=1), visible=True, show_signups=show_signups
    )


# --- zMAP ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_zmap_minimums_never_sweep_in_a_rider_with_no_zmap(roster_rider, user_model):
    roster_rider(zwid=4242, name="Strong")
    _member(user_model, "strong", 4242, z_map=Decimal(400), z_metrics_weight_grams=70_000)  # 5.7 W/kg
    roster_rider(zwid=4243, name="Steady")
    _member(user_model, "steady", 4243, z_map=Decimal(330), z_metrics_weight_grams=75_000)  # 4.4 W/kg
    roster_rider(zwid=4244, name="No zMAP")

    assert _names(_rows(zmap="350")) == ["Strong"]
    assert _names(_rows(zmap_wkg="5.0")) == ["Strong"]
    assert _names(_rows(zmap_wkg="4.0")) == ["Steady", "Strong"]


# --- team kit -----------------------------------------------------------------------------


@pytest.fixture
def kits(db):
    return (
        TeamKit.objects.create(name="2026 Kit", slug="kit-2026", active=True, is_current=True),
        TeamKit.objects.create(name="2025 Kit", slug="kit-2025", active=True),
    )


@pytest.mark.django_db
def test_kit_statuses_match_any_ticked_status_on_the_current_kit_by_default(kits, roster_rider, user_model):
    roster_rider(zwid=4242, name="Needs It")
    _member(user_model, "needs", 4242, team_kit={"kit-2026": "need"})
    roster_rider(zwid=4243, name="Has It")
    _member(user_model, "has", 4243, team_kit={"kit-2026": "have", "kit-2025": "need"})
    roster_rider(zwid=4244, name="Never Asked")
    _member(user_model, "never", 4244)
    roster_rider(zwid=4245, name="No Account")

    # A member the team has never asked is "Unknown", as on the kit page; no account is nothing.
    assert _names(_rows(kit_status=["need", "unknown"])) == ["Needs It", "Never Asked"]
    assert _names(_rows(kit="kit-2025", kit_status=["need"])) == ["Has It"]
    # A kit nobody offers falls back to the current one rather than narrowing to nobody.
    assert _names(_rows(kit="kit-1999", kit_status=["have"])) == ["Has It"]
    # A kit with no status ticked narrows nothing.
    assert len(_rows(kit="kit-2025")) == 4


# --- event signup -------------------------------------------------------------------------


@pytest.mark.django_db
def test_signed_up_and_not_signed_up_for_an_event(roster_rider, user_model):
    tour = _event("Tour")
    hidden = _event("Hidden", show_signups=False)
    roster_rider(zwid=4242, name="Rider In")
    rider_in = _member(user_model, "in", 4242)
    EventSignup.objects.create(event=tour, user=rider_in, status=EventSignup.Status.REGISTERED)
    roster_rider(zwid=4243, name="Hidden Rider")
    hidden_rider = _member(user_model, "hidden", 4243)
    EventSignup.objects.create(event=hidden, user=hidden_rider, status=EventSignup.Status.REGISTERED)
    roster_rider(zwid=4244, name="No Account")

    assert _names(_rows(event=str(tour.pk))) == ["Rider In"]
    assert _names(_rows(event=str(tour.pk), signup="no")) == ["Hidden Rider", "No Account"]
    assert _names(_rows(event="any")) == ["Rider In"]


@pytest.mark.django_db
def test_an_event_that_hides_its_signups_cannot_be_filtered_by(roster_rider, user_model):
    """Filtering by it would list, one rider at a time, what the event's own page withholds."""
    hidden = _event("Hidden", show_signups=False)
    roster_rider(zwid=4242, name="Hidden Rider")
    EventSignup.objects.create(
        event=hidden, user=_member(user_model, "hidden", 4242), status=EventSignup.Status.REGISTERED
    )
    roster_rider(zwid=4243, name="Someone Else")

    assert _names(_rows(event=str(hidden.pk))) == ["Hidden Rider", "Someone Else"]
    assert _names(_rows(event=str(hidden.pk), signup="no")) == ["Hidden Rider", "Someone Else"]


@pytest.mark.django_db
def test_a_finished_event_is_not_offered(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer")
    _event("Upcoming Race")
    _event("Finished Race", days_out=-10)

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "Upcoming Race" in body
    assert "Finished Race" not in body


# --- timezone -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_timezone_filter_and_its_not_set_option(roster_rider, user_model):
    roster_rider(zwid=4242, name="New Yorker")
    _member(user_model, "ny", 4242, timezone="America/New_York")
    roster_rider(zwid=4243, name="Londoner")
    _member(user_model, "ldn", 4243, timezone="Europe/London")
    roster_rider(zwid=4244, name="Unset Member")
    _member(user_model, "unset", 4244, timezone="")
    roster_rider(zwid=4245, name="No Account")

    assert _names(_rows(tz="America/New_York")) == ["New Yorker"]
    # Only members can set a zone, so a rider with no account here is not "not set".
    assert _names(_rows(tz="none")) == ["Unset Member"]
    # A zone nobody has set is not offered, so it narrows nothing.
    assert len(_rows(tz="Asia/Tokyo")) == 4


@pytest.mark.django_db
def test_timezone_options_are_grouped_west_to_east_with_counts(roster_rider, user_model):
    for zwid, zone in ((4242, "Europe/London"), (4243, "America/Los_Angeles"), (4244, "America/New_York")):
        roster_rider(zwid=zwid, name=f"Rider {zwid}")
        _member(user_model, f"r{zwid}", zwid, timezone=zone)
    roster_rider(zwid=4245, name="Second New Yorker")
    _member(user_model, "r4245", 4245, timezone="America/New_York")

    options = filter_options(build_roster_index().rows)
    groups = {name: [(zone.value, zone.count) for zone in zones] for name, zones in options["timezones"]}

    assert list(groups) == ["Americas", "Europe and Africa"]
    assert groups["Americas"] == [("America/Los_Angeles", 1), ("America/New_York", 2)]
    assert groups["Europe and Africa"] == [("Europe/London", 1)]
    assert options["timezones_unset"] == 0
    labels = [zone.label for _, zones in options["timezones"] for zone in zones]
    assert labels[0].startswith("Los Angeles (UTC-")


# --- verification expiring ----------------------------------------------------------------


def _verified(user, verify_type, days_left, validity):
    return RaceReadyRecord.objects.create(
        user=user,
        verify_type=verify_type,
        status=RaceReadyRecord.Status.VERIFIED,
        record_date=timezone.localdate() - timedelta(days=validity - days_left),
    )


@pytest.fixture
def expiring_riders(roster_rider, user_model):
    """Cat B riders (weight_full and height required), with height made to expire too."""
    soon = _member(user_model, "soon", 4242)
    roster_rider(zwid=4242, name="Weight Soon")
    _verified(soon, "weight_full", 10, 120)
    _verified(soon, "height", 300, 365)

    lapsed = _member(user_model, "lapsed", 4243)
    roster_rider(zwid=4243, name="Weight Lapsed")
    _verified(lapsed, "weight_full", -5, 120)
    _verified(lapsed, "height", 300, 365)

    height = _member(user_model, "height", 4244)
    roster_rider(zwid=4244, name="Height Soon")
    _verified(height, "weight_full", 100, 120)
    _verified(height, "height", 20, 365)

    # weight_light is not what Cat B requires, so its expiry puts nothing at stake.
    light = _member(user_model, "light", 4245)
    roster_rider(zwid=4245, name="Light Only")
    _verified(light, "weight_light", 5, 30)

    roster_rider(zwid=4246, name="No Account")


@pytest.mark.django_db
@override_config(WEIGHT_FULL_DAYS=120, WEIGHT_LIGHT_DAYS=30, HEIGHT_VERIFICATION_DAYS=365)
def test_the_expiry_filter_follows_what_the_category_requires(expiring_riders):
    assert _names(_rows(expiring="15")) == ["Weight Soon"]
    assert _names(_rows(expiring="30")) == ["Height Soon", "Weight Soon"]
    assert _names(_rows(expiring="30", vtype="height")) == ["Height Soon"]
    assert _names(_rows(expiring="30", vtype="weight")) == ["Weight Soon"]
    assert _names(_rows(expiring="expired")) == ["Weight Lapsed"]


@pytest.mark.django_db
@override_config(WEIGHT_FULL_DAYS=120, WEIGHT_LIGHT_DAYS=30, HEIGHT_VERIFICATION_DAYS=365)
def test_only_verification_reviewers_get_the_expiry_filter(client, expiring_riders, user_model):
    url = reverse("team:roster") + "?expiring=15"

    member = _make_user(user_model, username="plain", permissions={"team_member": True})
    client.force_login(member)
    body = client.get(url).content.decode()
    assert 'id="f-expiring"' not in body
    assert "Light Only" in body  # not narrowed
    assert "Verification expiring" not in body  # and no chip claims it was

    reviewer = _make_user(
        user_model, username="reviewer", permissions={"team_member": True, "approve_verification": True}
    )
    client.force_login(reviewer)
    body = client.get(url).content.decode()
    assert 'id="f-expiring"' in body
    assert "Weight Soon" in body
    assert "Light Only" not in body


# --- joined the Discord -------------------------------------------------------------------


@pytest.fixture
def joiners(roster_rider, user_model):
    for zwid, name, days in ((4242, "Recent", 10), (4243, "Middle", 100), (4244, "Veteran", 400)):
        roster_rider(zwid=zwid, name=name)
        user = _member(user_model, name.lower(), zwid)
        GuildMember.objects.create(
            discord_id=user.discord_id,
            username=user.username,
            user=user,
            joined_at=timezone.now() - timedelta(days=days),
        )
    roster_rider(zwid=4245, name="No Account")


@pytest.mark.django_db
def test_join_dates_bound_either_side(joiners):
    today = timezone.localdate()
    month_ago = (today - timedelta(days=30)).isoformat()
    half_year_ago = (today - timedelta(days=200)).isoformat()

    assert _names(_rows(joined_after=month_ago)) == ["Recent"]
    assert _names(_rows(joined_before=month_ago)) == ["Middle", "Veteran"]
    assert _names(_rows(joined_after=half_year_ago, joined_before=month_ago)) == ["Middle"]
    assert len(_rows(joined_after="not-a-date")) == 4


@pytest.mark.django_db
def test_an_old_joined_link_still_means_the_last_n_days(joiners):
    """?joined=90 predates the date inputs; it must not stop working for whoever saved it."""
    assert _names(_rows(joined="90")) == ["Recent"]


# --- the page -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_chips_describe_the_new_filters_and_remove_them_whole(auth_client, kits, roster_rider, user_model):
    tour = _event("Tour")
    roster_rider(zwid=4242, name="Ada Racer")
    _member(user_model, "ada", 4242, team_kit={"kit-2026": "need"})

    url = reverse("team:roster") + (
        f"?kit=kit-2026&kit_status=need&kit_status=unknown&event={tour.pk}&signup=no"
        "&zmap=350&joined_after=2026-09-01&tz=none"
    )
    body = auth_client.get(url).content.decode()

    for text in (
        "2026 Kit",
        "Unknown, Needs kit",
        "Not signed up for",
        "Min zMAP",
        "350 W",
        "Joined on or after",
        "1 Sep 2026",
        "Timezone",
        "Not set",
    ):
        assert text in body, text
    # The kit chip's remove link drops both halves of the kit filter.
    removals = re.findall(r'href="\?([^"]*)"', body)
    assert any("kit" not in link and "event=" in link for link in removals)


def test_the_kit_filter_names_every_stored_status_in_the_third_person():
    """Every status is offered, in order, and none in the rider's own "I have the kit"."""
    from apps.team.models import KitStatus
    from apps.team.rosterv2 import KIT_FILTER_LABELS

    assert list(KIT_FILTER_LABELS) == list(KitStatus.values)
    assert not any(label.startswith("I ") for label in KIT_FILTER_LABELS.values())

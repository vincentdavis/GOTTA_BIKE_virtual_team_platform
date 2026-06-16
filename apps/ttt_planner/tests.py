"""Tests for the TTT planner: physics, computation, roster merge, and sharing."""

import pytest
from django.urls import reverse

from apps.ttt_planner.models import PlanRider, Route, TttPlan
from apps.ttt_planner.services import physics
from apps.ttt_planner.services.compute import compute_plan
from apps.ttt_planner.services.roster import get_rider_data
from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftracing.models import ZRRider

# --------------------------------------------------------------------------- #
# Physics
# --------------------------------------------------------------------------- #


def test_power_speed_round_trip():
    """speed_for_power inverts power_for_speed."""
    for speed in (30.0, 40.0, 50.0):
        power = physics.power_for_speed(speed, weight_kg=75, height_cm=180)
        recovered = physics.speed_for_power(power, weight_kg=75, height_cm=180)
        assert recovered == pytest.approx(speed, abs=0.1)


def test_power_increases_with_speed():
    """Higher speed requires more power."""
    p30 = physics.power_for_speed(30, weight_kg=75, height_cm=180)
    p40 = physics.power_for_speed(40, weight_kg=75, height_cm=180)
    assert p40 > p30


def test_calibration_reference_rider():
    """A 75 kg / 180 cm rider needs a realistic ~250-340 W to hold 40 km/h solo."""
    power = physics.power_for_speed(40, weight_kg=75, height_cm=180)
    assert 250 < power < 340


def test_draft_savings_monotonic_then_clamped():
    """Draft savings: zero on front, increasing, then clamped past the table."""
    assert physics.draft_savings_fraction(1) == pytest.approx(0.0)
    assert physics.draft_savings_fraction(2) > 0.0
    assert physics.draft_savings_fraction(3) > physics.draft_savings_fraction(2)
    # Beyond the table length it clamps to the last value rather than erroring.
    last = physics.draft_savings_fraction(len(physics.DEFAULT_PARAMS.draft_savings))
    assert physics.draft_savings_fraction(99) == last


def test_draft_reduces_power():
    """Drafting requires less power than pulling at the same speed."""
    front = physics.power_for_speed(40, weight_kg=75, height_cm=180, draft_factor=1.0)
    draft = physics.power_for_speed(40, weight_kg=75, height_cm=180, draft_factor=0.7)
    assert draft < front


def test_estimate_cda_scales_with_size():
    """CdA grows with both height and weight."""
    base = physics.estimate_cda(180, 75)
    assert physics.estimate_cda(190, 75) > base
    assert physics.estimate_cda(180, 90) > base


def test_intensity_factor():
    """IF is power/FTP, and None without FTP."""
    assert physics.intensity_factor(300, 300) == pytest.approx(1.0)
    assert physics.intensity_factor(285, 300) == pytest.approx(0.95)
    assert physics.intensity_factor(300, None) is None
    assert physics.intensity_factor(300, 0) is None


def test_estimate_time_uphill_slower_than_flat():
    """A climbing route takes longer than a flat one of the same distance."""
    flat = physics.estimate_time_seconds(20, 0, 40)
    hilly = physics.estimate_time_seconds(20, 400, 40)
    assert hilly > flat > 0


# --------------------------------------------------------------------------- #
# Plan computation
# --------------------------------------------------------------------------- #


def test_normalized_power_constant_equals_power():
    """NP of a constant series equals that power."""
    assert physics.normalized_power([250.0] * 120) == pytest.approx(250.0, abs=0.5)


def test_normalized_power_variable_exceeds_mean():
    """NP of a variable series exceeds its simple average (weights hard efforts)."""
    series = ([150.0] * 60 + [350.0] * 60) * 5  # mean 250
    assert physics.normalized_power(series) > 250.0


def test_training_stress_score():
    """TSS is 100 for an hour at FTP, and None without FTP."""
    assert physics.training_stress_score(300, 300, 3600) == pytest.approx(100.0)
    assert physics.training_stress_score(300, None, 3600) is None
    assert physics.training_stress_score(300, 300, 0) is None


@pytest.mark.django_db
def test_compute_plan_np_tss_with_route(team_member):
    """With a route (finish time), riders get Normalized Power and TSS."""
    route = Route.objects.create(name="NP Route", distance_km=20, elevation_m=0)
    plan = TttPlan.objects.create(created_by=team_member, route=route, target_speed_kph=42)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=72, height_cm=178, ftp_w=300, pull_duration_s=45)
    PlanRider.objects.create(plan=plan, order=1, name="B", weight_kg=78, height_cm=182, ftp_w=300, pull_duration_s=45)

    result = compute_plan(plan)
    for rr in result.riders:
        assert rr.normalized_power_w is not None
        assert rr.normalized_power_w > 0
        assert rr.tss is not None
        assert rr.tss > 0


@pytest.mark.django_db
def test_compute_plan_no_np_without_route(team_member):
    """Without a route there is no finish time, so NP/TSS stay None."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=72, height_cm=178, ftp_w=300)
    result = compute_plan(plan)
    assert result.riders[0].normalized_power_w is None
    assert result.riders[0].tss is None


@pytest.mark.django_db
def test_np_tss_none_without_ftp(team_member):
    """A rider with no FTP gets NP but no TSS."""
    route = Route.objects.create(name="NP Route 2", distance_km=15, elevation_m=0)
    plan = TttPlan.objects.create(created_by=team_member, route=route, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="NoFTP", weight_kg=70, height_cm=175)
    result = compute_plan(plan)
    assert result.riders[0].normalized_power_w is not None
    assert result.riders[0].tss is None


@pytest.mark.django_db
def test_compute_plan_basic(team_member):
    """compute_plan produces per-rider pull power and a team average."""
    route = Route.objects.create(name="Test Flat", distance_km=20, elevation_m=0)
    plan = TttPlan.objects.create(created_by=team_member, route=route, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=70, height_cm=175, ftp_w=300)
    PlanRider.objects.create(plan=plan, order=1, name="B", weight_kg=80, height_cm=185, ftp_w=320)

    result = compute_plan(plan)
    assert len(result.riders) == 2
    assert all(rr.pull_power_w > 0 for rr in result.riders)
    assert result.avg_team_power_w > 0
    assert result.estimated_time_s > 0


@pytest.mark.django_db
def test_compute_plan_zero_pull_excluded_from_pull(team_member):
    """A zero-pull rider shows zero pull power but still gets an avg power."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=70, height_cm=175, ftp_w=300)
    rider = PlanRider.objects.create(
        plan=plan, order=1, name="Recovery", weight_kg=80, height_cm=185, ftp_w=250, zero_pull=True
    )

    result = compute_plan(plan)
    zero = next(rr for rr in result.riders if rr.rider.pk == rider.pk)
    assert zero.pull_power_w == 0
    assert zero.avg_power_w > 0


@pytest.mark.django_db
def test_compute_plan_missing_data_flagged(team_member):
    """A rider without weight/height is flagged and still computed via defaults."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="Guest")
    result = compute_plan(plan)
    assert result.riders[0].missing_data is True
    assert result.riders[0].pull_power_w > 0


# --------------------------------------------------------------------------- #
# Roster merge
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_roster_merge_prefers_zp_then_zr():
    """get_rider_data merges ZP and ZR, preferring ZP values when present."""
    ZPTeamRiders.objects.create(zwid=123, name="Rider ZP", weight=72.5, ftp=305, div=20)
    ZRRider.objects.create(zwid=123, name="Rider ZR", weight=99.9, zp_ftp=999, height=181, zp_category="B")

    data = get_rider_data([123])[123]
    assert data.weight_kg == pytest.approx(72.5)  # ZP wins
    assert data.ftp_w == 305  # ZP wins
    assert data.height_cm == 181  # only ZR has height
    assert data.category == "B"


@pytest.mark.django_db
def test_roster_merge_falls_back_to_zr():
    """When only ZR has data, those values are used."""
    ZRRider.objects.create(zwid=456, name="Only ZR", weight=68.0, zp_ftp=260, height=170)
    data = get_rider_data([456])[456]
    assert data.weight_kg == pytest.approx(68.0)
    assert data.ftp_w == 260


# --------------------------------------------------------------------------- #
# Sharing / permissions
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_owner_can_edit_shared_view_readonly(auth_client, team_member, app_admin):
    """Owner sees edit controls; another team member sharing the link sees read-only."""
    plan = TttPlan.objects.create(created_by=team_member, name="Mine")
    url = reverse("ttt_planner:detail", args=[plan.pk])

    owner_resp = auth_client.get(url)
    assert owner_resp.status_code == 200
    assert owner_resp.context["can_edit"] is True

    auth_client.force_login(app_admin)
    other_resp = auth_client.get(url)
    assert other_resp.status_code == 200
    assert other_resp.context["can_edit"] is False


@pytest.mark.django_db
def test_non_owner_cannot_mutate(auth_client, team_member, app_admin):
    """A non-owner cannot add riders to someone else's plan."""
    plan = TttPlan.objects.create(created_by=team_member)
    auth_client.force_login(app_admin)
    resp = auth_client.post(reverse("ttt_planner:rider_add_manual", args=[plan.pk]), {"name": "Sneaky"})
    assert resp.status_code == 403
    assert plan.riders.count() == 0


@pytest.mark.django_db
def test_add_manual_rider_flow(auth_client, team_member):
    """Posting the manual-add form creates a rider and returns the plan body."""
    plan = TttPlan.objects.create(created_by=team_member)
    resp = auth_client.post(
        reverse("ttt_planner:rider_add_manual", args=[plan.pk]),
        {"name": "Guest", "weight_kg": "70", "height_cm": "178", "ftp_w": "280"},
    )
    assert resp.status_code == 200
    rider = plan.riders.get()
    assert rider.name == "Guest"
    assert rider.ftp_w == 280


@pytest.mark.django_db
def test_plan_update_changes_speed_and_route(auth_client, team_member):
    """Posting plan settings updates the speed and route and recomputes."""
    route = Route.objects.create(name="Update Route", distance_km=15, elevation_m=0)
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    resp = auth_client.post(
        reverse("ttt_planner:update", args=[plan.pk]),
        {"name": "Renamed", "team_name": "", "target_speed_kph": "44.5", "route": str(route.pk)},
    )
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.name == "Renamed"
    assert float(plan.target_speed_kph) == pytest.approx(44.5)
    assert plan.route_id == route.pk
    # The recomputed body reflects the chosen route (finish-time block shows it).
    assert b"Update Route" in resp.content


@pytest.mark.django_db
def test_auto_speed_sets_sustainable_value(auth_client, team_member):
    """Auto-speed derives a positive target from rider FTPs."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=10)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=72, height_cm=178, ftp_w=300)
    resp = auth_client.post(reverse("ttt_planner:auto_speed", args=[plan.pk]))
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert float(plan.target_speed_kph) > 10


@pytest.mark.django_db
def test_rider_search_renders(auth_client, team_member):
    """The search endpoint finds a team rider and renders the dropdown partial."""
    ZPTeamRiders.objects.create(zwid=789, name="Searchable Sam", weight=70, ftp=280, div=20)
    plan = TttPlan.objects.create(created_by=team_member)
    resp = auth_client.get(reverse("ttt_planner:rider_search", args=[plan.pk]), {"q": "Searchable"})
    assert resp.status_code == 200
    assert b"Searchable Sam" in resp.content


@pytest.mark.django_db
def test_rider_reorder_swaps_order(auth_client, team_member):
    """Moving a rider up swaps its order with the neighbour."""
    plan = TttPlan.objects.create(created_by=team_member)
    a = PlanRider.objects.create(plan=plan, order=0, name="A")
    b = PlanRider.objects.create(plan=plan, order=1, name="B")
    resp = auth_client.post(reverse("ttt_planner:rider_reorder", args=[plan.pk, b.pk, "up"]))
    assert resp.status_code == 200
    a.refresh_from_db()
    b.refresh_from_db()
    assert b.order < a.order


# --------------------------------------------------------------------------- #
# Per-plan draft savings
# --------------------------------------------------------------------------- #


def test_parse_draft_savings_json_and_csv():
    """Both JSON arrays and comma-separated lists parse to the same tuple."""
    assert physics.parse_draft_savings("[0.0, 0.2, 0.3]") == (0.0, 0.2, 0.3)
    assert physics.parse_draft_savings("0, 0.2, 0.3") == (0.0, 0.2, 0.3)


def test_parse_draft_savings_percent_and_leading_zero():
    """Percentages are converted to fractions and a leading front 0 is prepended."""
    # Values > 1 are treated as percentages.
    assert physics.parse_draft_savings("0, 23, 30") == pytest.approx((0.0, 0.23, 0.30))
    # First value non-zero -> a front 0.0 is prepended.
    assert physics.parse_draft_savings("23, 30") == pytest.approx((0.0, 0.23, 0.30))


def test_parse_draft_savings_empty_and_bad():
    """Empty / unparseable input returns None (caller falls back to default)."""
    assert physics.parse_draft_savings("") is None
    assert physics.parse_draft_savings(None) is None
    assert physics.parse_draft_savings("not numbers") is None
    assert physics.parse_draft_savings([]) is None


def test_parse_draft_savings_accepts_list():
    """A stored JSON list (fractions) round-trips through the parser."""
    assert physics.parse_draft_savings([0.0, 0.23, 0.30]) == pytest.approx((0.0, 0.23, 0.30))
    # Percentages in a list are converted, and a leading front 0 is prepended.
    assert physics.parse_draft_savings([23, 30]) == pytest.approx((0.0, 0.23, 0.30))


@pytest.mark.django_db
def test_plan_draft_savings_overrides_default(team_member):
    """A per-plan draft list changes the computed draft table and avg power."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=72, height_cm=178, ftp_w=300)
    PlanRider.objects.create(plan=plan, order=1, name="B", weight_kg=78, height_cm=182, ftp_w=300)

    baseline = compute_plan(plan)

    plan.draft_savings = [0.0, 0.5]  # much bigger draft than the default
    plan.save(update_fields=["draft_savings"])
    boosted = compute_plan(plan)

    # Position-2 saving in the table reflects the override.
    assert boosted.draft_table[1].saving_pct == pytest.approx(50.0)
    # More draft -> lower average team power than the default.
    assert boosted.avg_team_power_w < baseline.avg_team_power_w


@pytest.mark.django_db
def test_rider_cda_computed_and_default_coef(team_member):
    """Each rider gets a positive CdA and the plan reports the default coef."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=75, height_cm=180, ftp_w=300)
    result = compute_plan(plan)
    assert result.cda_coef == pytest.approx(physics.DEFAULT_PARAMS.cda_coef)
    assert result.riders[0].cda == pytest.approx(physics.estimate_cda(180, 75), abs=0.001)


@pytest.mark.django_db
def test_plan_cda_coef_overrides_default(team_member):
    """A bigger per-plan CdA coef raises each rider's CdA and required pull power."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=75, height_cm=180, ftp_w=300)

    baseline = compute_plan(plan)

    plan.cda_coef = physics.DEFAULT_PARAMS.cda_coef * 1.2
    plan.save(update_fields=["cda_coef"])
    bigger = compute_plan(plan)

    assert bigger.cda_coef == pytest.approx(physics.DEFAULT_PARAMS.cda_coef * 1.2)
    assert bigger.riders[0].cda > baseline.riders[0].cda
    assert bigger.riders[0].pull_power_w > baseline.riders[0].pull_power_w


@pytest.mark.django_db
def test_plan_update_stores_and_clears_cda_coef(auth_client, team_member):
    """Posting a CdA coef stores it; clearing it falls back to the global default."""
    plan = TttPlan.objects.create(created_by=team_member)
    resp = auth_client.post(reverse("ttt_planner:update", args=[plan.pk]), {"cda_coef": "0.035"})
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.cda_coef == pytest.approx(0.035)

    resp = auth_client.post(reverse("ttt_planner:update", args=[plan.pk]), {"cda_coef": ""})
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.cda_coef is None


@pytest.mark.django_db
def test_draft_table_present(team_member):
    """compute_plan always returns a draft table and a pre-fill string."""
    plan = TttPlan.objects.create(created_by=team_member)
    result = compute_plan(plan)
    assert result.draft_table
    assert result.draft_table[0].position == 1
    assert result.draft_table[0].saving_pct == pytest.approx(0.0)
    assert isinstance(result.draft_savings_input, str)


@pytest.mark.django_db
def test_draft_savings_table_update_stores_fraction_list(auth_client, team_member):
    """Editing the position table (percentages for pos 2..N) stores a fraction list."""
    plan = TttPlan.objects.create(created_by=team_member)
    resp = auth_client.post(
        reverse("ttt_planner:draft_savings_update", args=[plan.pk]),
        {"saving": ["25", "35"]},
    )
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.draft_savings == pytest.approx([0.0, 0.25, 0.35])


@pytest.mark.django_db
def test_draft_savings_table_clamps_and_fronts_zero(auth_client, team_member):
    """Out-of-range percentages clamp to 95% and the front stays 0."""
    plan = TttPlan.objects.create(created_by=team_member)
    resp = auth_client.post(
        reverse("ttt_planner:draft_savings_update", args=[plan.pk]),
        {"saving": ["200", "30"]},
    )
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.draft_savings == pytest.approx([0.0, 0.95, 0.30])


@pytest.mark.django_db
def test_draft_savings_reset_uses_default(auth_client, team_member):
    """Reset clears the per-plan list back to an empty list (global default)."""
    plan = TttPlan.objects.create(created_by=team_member, draft_savings=[0.0, 0.4])
    resp = auth_client.post(reverse("ttt_planner:draft_savings_update", args=[plan.pk]), {"reset": "1"})
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.draft_savings == []


@pytest.mark.django_db
def test_draft_savings_table_non_owner_forbidden(auth_client, team_member, app_admin):
    """A non-owner cannot edit another captain's draft savings."""
    plan = TttPlan.objects.create(created_by=team_member)
    auth_client.force_login(app_admin)
    resp = auth_client.post(
        reverse("ttt_planner:draft_savings_update", args=[plan.pk]),
        {"saving": ["25"]},
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_create_with_draft_savings(auth_client, team_member):
    """The create form stores name and a draft-savings fraction list on the new plan."""
    resp = auth_client.post(
        reverse("ttt_planner:create"),
        {"name": "Created Plan", "team_name": "Coalition", "draft_savings": "0, 20, 28"},
    )
    assert resp.status_code == 302
    plan = TttPlan.objects.get(name="Created Plan")
    assert plan.team_name == "Coalition"
    assert plan.draft_savings == pytest.approx([0.0, 0.20, 0.28])

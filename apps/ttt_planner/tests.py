"""Tests for the TTT planner: physics, computation, roster merge, and sharing."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.ttt_planner.models import PlanRider, Route, RouteGpx, TttPlan
from apps.ttt_planner.services import physics, zwiftgopher
from apps.ttt_planner.services.compute import compute_auto_balance, compute_plan, sustainable_speed
from apps.ttt_planner.services.roster import get_rider_data
from apps.ttt_planner.tasks import run_zwiftgopher_optimize
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
def test_sustainable_speed_higher_if_is_faster(team_member):
    """A higher target IF yields a higher sustainable speed."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=10)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=72, height_cm=178, ftp_w=300)
    PlanRider.objects.create(plan=plan, order=1, name="B", weight_kg=78, height_cm=182, ftp_w=300)
    slow = sustainable_speed(plan, target_if=0.85)
    fast = sustainable_speed(plan, target_if=1.0)
    assert fast > slow > 0


@pytest.mark.django_db
def test_sustainable_speed_depends_on_pull_durations(team_member):
    """Giving the stronger rider longer pulls raises the sustainable speed."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=10)
    strong = PlanRider.objects.create(
        plan=plan, order=0, name="Strong", weight_kg=70, height_cm=175, ftp_w=360, pull_duration_s=60
    )
    weak = PlanRider.objects.create(
        plan=plan, order=1, name="Weak", weight_kg=80, height_cm=185, ftp_w=240, pull_duration_s=60
    )
    even = sustainable_speed(plan, target_if=0.95)

    strong.pull_duration_s = 150
    strong.save(update_fields=["pull_duration_s"])
    weak.pull_duration_s = 30
    weak.save(update_fields=["pull_duration_s"])
    skewed = sustainable_speed(plan, target_if=0.95)

    assert skewed > even


@pytest.mark.django_db
def test_auto_balance_gives_stronger_rider_longer_pull(team_member):
    """Auto-balance assigns the stronger rider a longer pull and orders them first."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=10)
    strong = PlanRider.objects.create(plan=plan, order=0, name="Strong", weight_kg=70, height_cm=175, ftp_w=360)
    weak = PlanRider.objects.create(plan=plan, order=1, name="Weak", weight_kg=80, height_cm=185, ftp_w=240)

    result = compute_auto_balance(plan, target_if=0.95)
    assert result is not None
    durations = {a.rider_pk: a.pull_duration_s for a in result.assignments}
    orders = {a.rider_pk: a.order for a in result.assignments}
    assert durations[strong.pk] > durations[weak.pk]
    assert orders[strong.pk] < orders[weak.pk]  # strongest leads
    assert result.speed_kph > 0


@pytest.mark.django_db
def test_auto_balance_view_applies(auth_client, team_member):
    """The auto-balance endpoint writes durations/order and sets the speed."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=10)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=70, height_cm=175, ftp_w=360, pull_duration_s=60)
    PlanRider.objects.create(plan=plan, order=1, name="B", weight_kg=82, height_cm=186, ftp_w=240, pull_duration_s=60)

    resp = auth_client.post(reverse("ttt_planner:auto_balance", args=[plan.pk]))
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert float(plan.target_speed_kph) > 10
    durations = sorted(plan.riders.values_list("pull_duration_s", flat=True))
    assert durations[0] != durations[1]  # no longer equal


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
def test_calculate_sets_sustainable_speed_and_if(auth_client, team_member):
    """Calculate stores the posted IF and a positive sustainable target speed."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=10)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=72, height_cm=178, ftp_w=300)
    PlanRider.objects.create(plan=plan, order=1, name="B", weight_kg=78, height_cm=182, ftp_w=300)
    resp = auth_client.post(reverse("ttt_planner:calculate_speed", args=[plan.pk]), {"target_if": "0.9"})
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.target_if == pytest.approx(0.9)
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
def test_riders_remove_selected(auth_client, team_member):
    """Bulk remove deletes only the checked riders."""
    plan = TttPlan.objects.create(created_by=team_member)
    a = PlanRider.objects.create(plan=plan, order=0, name="A")
    b = PlanRider.objects.create(plan=plan, order=1, name="B")
    c = PlanRider.objects.create(plan=plan, order=2, name="C")
    resp = auth_client.post(
        reverse("ttt_planner:riders_remove_selected", args=[plan.pk]),
        {"rider_ids": [str(a.pk), str(c.pk)]},
    )
    assert resp.status_code == 200
    assert set(plan.riders.values_list("pk", flat=True)) == {b.pk}


@pytest.mark.django_db
def test_riders_remove_selected_non_owner_forbidden(auth_client, team_member, app_admin):
    """A non-owner cannot bulk-remove riders from someone else's plan."""
    plan = TttPlan.objects.create(created_by=team_member)
    rider = PlanRider.objects.create(plan=plan, order=0, name="A")
    auth_client.force_login(app_admin)
    resp = auth_client.post(
        reverse("ttt_planner:riders_remove_selected", args=[plan.pk]),
        {"rider_ids": [str(rider.pk)]},
    )
    assert resp.status_code == 403
    assert plan.riders.count() == 1


@pytest.mark.django_db
def test_owner_can_delete_plan(auth_client, team_member):
    """The owner can delete their plan and is redirected to the list."""
    plan = TttPlan.objects.create(created_by=team_member, name="Doomed")
    resp = auth_client.post(reverse("ttt_planner:delete", args=[plan.pk]))
    assert resp.status_code == 302
    assert not TttPlan.objects.filter(pk=plan.pk).exists()


@pytest.mark.django_db
def test_non_owner_cannot_delete_plan(auth_client, team_member, app_admin):
    """A non-owner cannot delete another captain's plan."""
    plan = TttPlan.objects.create(created_by=team_member)
    auth_client.force_login(app_admin)
    resp = auth_client.post(reverse("ttt_planner:delete", args=[plan.pk]))
    assert resp.status_code == 403
    assert TttPlan.objects.filter(pk=plan.pk).exists()


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
def test_create_plan_stores_name_team_and_event(auth_client, team_member):
    """The create form stores name, team, and event; draft savings stays default (empty)."""
    resp = auth_client.post(
        reverse("ttt_planner:create"),
        {"name": "Created Plan", "team_name": "Coalition", "event_type": "zrl"},
    )
    assert resp.status_code == 302
    plan = TttPlan.objects.get(name="Created Plan")
    assert plan.team_name == "Coalition"
    assert plan.event_type == "zrl"
    assert plan.draft_savings == []


@pytest.mark.django_db
def test_create_plan_rejects_bad_event(auth_client, team_member):
    """An invalid event_type on create is stored as blank."""
    resp = auth_client.post(reverse("ttt_planner:create"), {"name": "P", "event_type": "bogus"})
    assert resp.status_code == 302
    assert TttPlan.objects.get(name="P").event_type == ""


@pytest.mark.django_db
def test_plan_update_stores_event_type(auth_client, team_member):
    """Posting a valid event_type stores it; an invalid value clears it."""
    plan = TttPlan.objects.create(created_by=team_member)
    resp = auth_client.post(reverse("ttt_planner:update", args=[plan.pk]), {"event_type": "wtrl_ttt"})
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.event_type == "wtrl_ttt"

    resp = auth_client.post(reverse("ttt_planner:update", args=[plan.pk]), {"event_type": "bogus"})
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.event_type == ""


@pytest.mark.django_db
def test_plan_list_shows_event_riders_team_and_time(auth_client, team_member):
    """The plan list shows team name, event, rider count, and an estimated time."""
    route = Route.objects.create(name="List Route", distance_km=20, elevation_m=0)
    plan = TttPlan.objects.create(
        created_by=team_member,
        name="My Plan",
        team_name="Coalition A",
        event_type=TttPlan.EventType.WTRL_TTT,
        route=route,
        target_speed_kph=42,
    )
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=72, height_cm=178, ftp_w=300)
    PlanRider.objects.create(plan=plan, order=1, name="B", weight_kg=78, height_cm=182, ftp_w=300)

    resp = auth_client.get(reverse("ttt_planner:list"))
    assert resp.status_code == 200
    rows = resp.context["plan_rows"]
    assert rows[0]["rider_count"] == 2
    assert rows[0]["finish_s"] > 0
    content = resp.content
    assert b"Coalition A" in content
    assert b"WTRL TTT" in content


# --------------------------------------------------------------------------- #
# zwiftgopher integration
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_build_optimize_request(team_member):
    """Request body sends zwid riders as IDs+overrides and complete manual riders as custom_riders."""
    plan = TttPlan.objects.create(created_by=team_member, team_name="Squad", target_speed_kph=42)
    PlanRider.objects.create(plan=plan, order=0, name="A", zwid=111, ftp_w=300, weight_kg=72, height_cm=178)
    PlanRider.objects.create(plan=plan, order=1, name="Guest", ftp_w=250, weight_kg=70, height_cm=175)
    PlanRider.objects.create(plan=plan, order=2, name="NoData")  # incomplete manual -> skipped

    payload = zwiftgopher.build_optimize_request(plan, "next_wtrl")
    assert payload["riders"] == [111]
    assert payload["rider_overrides"]["111"]["ftp"] == 300
    assert payload["rider_overrides"]["111"]["weight"] == pytest.approx(72.0)
    assert {"name": "Guest", "ftp": 250, "weight": 70.0, "height": 175} in payload["custom_riders"]
    assert all(c["name"] != "NoData" for c in payload["custom_riders"])
    assert payload["route"] == "next_wtrl"
    assert payload["target_speed"] == pytest.approx(42.0)
    assert payload["request_id"] == str(plan.pk)


@pytest.mark.django_db
def test_build_optimize_request_invalid_schedule_defaults(team_member):
    """An unknown route schedule falls back to the default."""
    plan = TttPlan.objects.create(created_by=team_member)
    payload = zwiftgopher.build_optimize_request(plan, "bogus")
    assert payload["route"] == zwiftgopher.DEFAULT_ROUTE_SCHEDULE


@pytest.mark.django_db
def test_count_optimizable_riders(team_member):
    """Counts zwid riders and complete manual riders, skipping incomplete manuals."""
    plan = TttPlan.objects.create(created_by=team_member)
    PlanRider.objects.create(plan=plan, order=0, name="A", zwid=111)
    PlanRider.objects.create(plan=plan, order=1, name="Guest", ftp_w=250, weight_kg=70, height_cm=175)
    PlanRider.objects.create(plan=plan, order=2, name="NoData")
    assert zwiftgopher.count_optimizable_riders(plan) == 2


def test_parse_optimize_response_success():
    """A successful response normalizes fields and sorts riders by pull order."""
    data = {
        "success": True,
        "data": {
            "route": "Canopies and Coastlines",
            "estimated_time_seconds": 1947,
            "estimated_time_formatted": "32:27",
            "estimated_avg_speed": 43.2,
            "team_avg_power": 285,
            "team_avg_if": 93,
            "riders": [
                {"name": "Second", "order": 2, "pull_power": 250, "if_percent": 95},
                {"name": "First", "order": 1, "pull_power": 240, "if_percent": 92},
            ],
        },
    }
    result = zwiftgopher.parse_optimize_response(200, data)
    assert result["ok"] is True
    assert result["estimated_time_seconds"] == 1947
    assert result["estimated_time_formatted"] == "32:27"
    assert result["team_avg_if"] == 93
    assert result["route"] == "Canopies and Coastlines"
    # Riders come back sorted by their suggested pull order.
    assert [r["name"] for r in result["riders"]] == ["First", "Second"]
    assert result["riders"][0]["pull_power"] == 240


def test_parse_optimize_response_rate_limited():
    """A 429 is reported as a rate-limit error."""
    result = zwiftgopher.parse_optimize_response(429, {})
    assert result["ok"] is False
    assert "rate limited" in result["error"].lower()


def test_parse_optimize_response_errors():
    """Error and transport-failure shapes surface a message."""
    result = zwiftgopher.parse_optimize_response(400, {"success": False, "message": "At least 2 riders are required"})
    assert result["ok"] is False
    assert "2 riders" in result["error"]
    transport = zwiftgopher.parse_optimize_response(0, {"error": "boom"})
    assert transport["ok"] is False
    assert transport["error"] == "boom"


@pytest.mark.django_db
def test_zwiftgopher_run_enqueues(auth_client, team_member, monkeypatch):
    """Run sets status to pending and enqueues the task with the chosen schedule."""

    class _FakeTask:
        def __init__(self):
            self.calls = []

        def enqueue(self, *args):
            self.calls.append(args)

    fake = _FakeTask()
    monkeypatch.setattr("apps.ttt_planner.views.run_zwiftgopher_optimize", fake)
    plan = TttPlan.objects.create(created_by=team_member)
    resp = auth_client.post(reverse("ttt_planner:zwiftgopher_run", args=[plan.pk]), {"route_schedule": "next_zrl"})
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.zwiftgopher_status == TttPlan.GopherStatus.PENDING
    assert fake.calls == [(str(plan.pk), "next_zrl")]


@pytest.mark.django_db
def test_zwiftgopher_run_non_owner_forbidden(auth_client, team_member, app_admin):
    """A non-owner cannot trigger a run on someone else's plan."""
    plan = TttPlan.objects.create(created_by=team_member)
    auth_client.force_login(app_admin)
    resp = auth_client.post(reverse("ttt_planner:zwiftgopher_run", args=[plan.pk]))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_run_task_stores_result(team_member, monkeypatch):
    """The task calls the client, stores the parsed result, and marks done."""
    monkeypatch.setattr("apps.ttt_planner.tasks.time.sleep", lambda *a: None)
    monkeypatch.setattr("apps.ttt_planner.tasks.zwiftgopher_client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.ttt_planner.tasks.zwiftgopher_client.optimize",
        lambda payload: (
            200,
            {
                "success": True,
                "data": {
                    "route": "Canopies",
                    "estimated_time_seconds": 1947,
                    "estimated_avg_speed": 43.2,
                    "team_avg_power": 285,
                    "riders": [{"name": "Dave", "power_300_watts": 295, "speed_index": 68}],
                },
            },
        ),
    )
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="A", zwid=111, ftp_w=300, weight_kg=72, height_cm=178)
    PlanRider.objects.create(plan=plan, order=1, name="B", zwid=222, ftp_w=310, weight_kg=78, height_cm=182)

    run_zwiftgopher_optimize.func(str(plan.pk), "next_wtrl")

    plan.refresh_from_db()
    assert plan.zwiftgopher_status == TttPlan.GopherStatus.DONE
    assert plan.zwiftgopher_result["estimated_time_seconds"] == 1947
    assert plan.zwiftgopher_fetched_at is not None
    # Raw request + response are stored for the in-panel viewers.
    assert plan.zwiftgopher_request["request_id"] == str(plan.pk)
    assert sorted(plan.zwiftgopher_request["riders"]) == [111, 222]
    assert plan.zwiftgopher_raw_response["success"] is True


@pytest.mark.django_db
def test_run_task_too_few_riders(team_member, monkeypatch):
    """The task errors out when fewer than 2 optimizable riders are present."""
    monkeypatch.setattr("apps.ttt_planner.tasks.zwiftgopher_client.is_configured", lambda: True)
    plan = TttPlan.objects.create(created_by=team_member)
    PlanRider.objects.create(plan=plan, order=0, name="A", zwid=111, ftp_w=300, weight_kg=72, height_cm=178)

    run_zwiftgopher_optimize.func(str(plan.pk))

    plan.refresh_from_db()
    assert plan.zwiftgopher_status == TttPlan.GopherStatus.ERROR
    assert "at least" in plan.zwiftgopher_error.lower()


@pytest.mark.django_db
def test_run_task_not_configured(team_member, monkeypatch):
    """The task errors when no API key is configured."""
    monkeypatch.setattr("apps.ttt_planner.tasks.zwiftgopher_client.is_configured", lambda: False)
    plan = TttPlan.objects.create(created_by=team_member)
    run_zwiftgopher_optimize.func(str(plan.pk))
    plan.refresh_from_db()
    assert plan.zwiftgopher_status == TttPlan.GopherStatus.ERROR


@pytest.mark.django_db
def test_gopher_panel_renders(auth_client, team_member):
    """The panel endpoint renders for a plan."""
    plan = TttPlan.objects.create(created_by=team_member)
    resp = auth_client.get(reverse("ttt_planner:zwiftgopher_panel", args=[plan.pk]))
    assert resp.status_code == 200
    assert b"zwiftgopher" in resp.content


@pytest.mark.django_db
def test_gopher_panel_shows_request_response_viewers(auth_client, team_member):
    """When raw request/response are stored, the panel shows the JSON viewers."""
    plan = TttPlan.objects.create(
        created_by=team_member,
        zwiftgopher_status=TttPlan.GopherStatus.DONE,
        zwiftgopher_request={"request_id": "abc", "riders": [111, 222]},
        zwiftgopher_raw_response={"success": True, "data": {"route": "Probe Route"}},
    )
    resp = auth_client.get(reverse("ttt_planner:zwiftgopher_panel", args=[plan.pk]))
    assert resp.status_code == 200
    assert b"View API request" in resp.content
    assert b"View API response" in resp.content
    assert b"Probe Route" in resp.content


def test_pretty_json_filter():
    """The pretty_json filter indents dicts and handles None."""
    from apps.ttt_planner.templatetags.ttt_extras import pretty_json

    assert pretty_json(None) == ""
    out = pretty_json({"a": 1})
    assert '"a": 1' in out


# ----- route terrain / course selector -----------------------------------------------------------


@pytest.mark.parametrize(
    ("distance_km", "elevation_m", "expected"),
    [
        (28.21, 132, "flat"),  # ~4.7 m/km
        (20.0, 200, "rolling"),  # 10 m/km
        (10.0, 200, "hilly"),  # 20 m/km
        (8.05, 236, "mountainous"),  # ~29 m/km
        (0, 100, "rolling"),  # unknown distance -> default
    ],
)
def test_derive_terrain(distance_km, elevation_m, expected):
    """derive_terrain classifies routes by climbing density."""
    from apps.ttt_planner import terrain

    assert terrain.derive_terrain(distance_km, elevation_m) == expected


@pytest.mark.django_db
def test_route_options_includes_terrain():
    """route_options exposes a derived terrain type per active route."""
    from apps.ttt_planner import terrain

    Route.objects.create(name="Flatland", distance_km=30, elevation_m=120)
    Route.objects.create(name="Climby", distance_km=10, elevation_m=300)
    by_name = {o["name"]: o for o in terrain.route_options()}
    assert by_name["Flatland"]["terrain"] == "flat"
    assert by_name["Climby"]["terrain"] == "mountainous"


@pytest.mark.django_db
def test_plan_update_sets_course_name_and_type(auth_client, team_member):
    """The plan update endpoint persists the course name and type."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    route = Route.objects.create(name="Bon Voyage", distance_km=28.21, elevation_m=132)
    resp = auth_client.post(
        reverse("ttt_planner:update", args=[plan.pk]),
        {"route": route.pk, "course_name": "Bon Voyage", "course_type": "flat"},
    )
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.route_id == route.pk
    assert plan.course_name == "Bon Voyage"
    assert plan.course_type == "flat"


@pytest.mark.django_db
def test_plan_update_rejects_invalid_course_type(auth_client, team_member):
    """An invalid course type is coerced to empty rather than stored."""
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    resp = auth_client.post(reverse("ttt_planner:update", args=[plan.pk]), {"course_type": "bogus"})
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.course_type == ""


@pytest.mark.django_db
def test_route_list_page_renders(auth_client):
    """The routes reference page lists routes with a derived terrain type."""
    Route.objects.create(name="Climby Loop", world="Watopia", distance_km=10, elevation_m=300)
    resp = auth_client.get(reverse("routes:list"))
    assert resp.status_code == 200
    assert b"Climby Loop" in resp.content
    assert b"Mountainous" in resp.content
    # Columns are sortable.
    assert b"sortRoutes" in resp.content
    assert b'data-sort=' in resp.content


# ----- route GPX uploads -------------------------------------------------------------------------

GPX_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <trk><trkseg>
    <trkpt lat="0.0" lon="0.00"><ele>0</ele></trkpt>
    <trkpt lat="0.0" lon="0.01"><ele>10</ele></trkpt>
    <trkpt lat="0.0" lon="0.02"><ele>20</ele></trkpt>
  </trkseg></trk>
</gpx>
"""


def test_parse_gpx_computes_metrics():
    """parse_gpx returns distance, elevation gain, terrain and point count."""
    from apps.ttt_planner.services.gpx import parse_gpx

    stats = parse_gpx(GPX_SAMPLE)
    assert stats.point_count == 3
    assert stats.distance_km > 0
    assert stats.elevation_m == 20
    assert stats.terrain in {"flat", "rolling", "hilly", "mountainous"}
    # Elevation profile: [distance_km, elevation_m] pairs, rising from 0 to 20 m.
    assert len(stats.profile) == 3
    assert stats.profile[0][1] == pytest.approx(0.0)
    assert stats.profile[-1][1] == pytest.approx(20.0)


def test_parse_gpx_rejects_garbage():
    """parse_gpx raises ValueError on non-GPX content."""
    from apps.ttt_planner.services.gpx import parse_gpx

    with pytest.raises(ValueError):
        parse_gpx(b"not a gpx file")


@pytest.mark.django_db
def test_whatsonzwift_url():
    """The route builds a whatsonzwift link from world + name (name-based slug)."""
    bon = Route.objects.create(name="Bon Voyage", world="France", distance_km=28.21, elevation_m=132)
    assert bon.whatsonzwift_url == "https://whatsonzwift.com/world/france/route/bon-voyage"
    # Bologna world is slugged "bologna-tt" on whatsonzwift; route slug from the name.
    bologna = Route.objects.create(name="Bologna Time Trial", world="Bologna", distance_km=8.05, elevation_m=236)
    assert bologna.whatsonzwift_url == "https://whatsonzwift.com/world/bologna-tt/route/bologna-time-trial"
    # No world -> no link.
    assert Route.objects.create(name="Nowhere", world="", distance_km=5, elevation_m=10).whatsonzwift_url == ""


@pytest.mark.django_db
def test_route_detail_renders(auth_client):
    """The route detail page renders with the upload form."""
    route = Route.objects.create(name="Test Route", distance_km=20, elevation_m=100)
    resp = auth_client.get(reverse("routes:detail", args=[route.pk]))
    assert resp.status_code == 200
    assert b"Upload a GPX file" in resp.content


@pytest.mark.django_db
def test_gpx_upload_parses_and_stores(auth_client, tmp_path, settings):
    """Uploading a GPX parses it and stores the metrics on a RouteGpx."""
    settings.MEDIA_ROOT = str(tmp_path)
    route = Route.objects.create(name="Upload Route", distance_km=20, elevation_m=100)
    upload = SimpleUploadedFile("track.gpx", GPX_SAMPLE, content_type="application/gpx+xml")

    resp = auth_client.post(
        reverse("routes:gpx_upload", args=[route.pk]),
        {"label": "Main spawn", "notes": "1km lead-in", "file": upload},
    )
    assert resp.status_code == 302
    gpx = route.gpx_files.get()
    assert gpx.label == "Main spawn"
    assert gpx.notes == "1km lead-in"
    assert gpx.distance_km > 0
    assert gpx.elevation_m == 20
    assert gpx.point_count == 3
    assert gpx.profile  # elevation profile stored for charting
    assert not gpx.parse_error


@pytest.mark.django_db
def test_gpx_upload_rejects_non_gpx(auth_client, tmp_path, settings):
    """A non-.gpx upload is rejected without creating a record."""
    settings.MEDIA_ROOT = str(tmp_path)
    route = Route.objects.create(name="Reject Route", distance_km=20, elevation_m=100)
    upload = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")

    resp = auth_client.post(reverse("routes:gpx_upload", args=[route.pk]), {"file": upload})
    assert resp.status_code == 302
    assert route.gpx_files.count() == 0


@pytest.mark.django_db
def test_gpx_delete_by_uploader(auth_client, team_member, tmp_path, settings):
    """The uploader can delete their GPX file."""
    settings.MEDIA_ROOT = str(tmp_path)
    route = Route.objects.create(name="Del Route", distance_km=20, elevation_m=100)
    gpx = RouteGpx.objects.create(
        route=route, label="x", file=SimpleUploadedFile("t.gpx", GPX_SAMPLE), uploaded_by=team_member
    )
    resp = auth_client.post(reverse("routes:gpx_delete", args=[route.pk, gpx.pk]))
    assert resp.status_code == 302
    assert route.gpx_files.count() == 0


# --------------------------------------------------------------------------- #
# Squad picker
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_plan_squad_add_adds_members(auth_client, team_member, user_model):
    """Adding a squad snapshots team data where available, else name-only; skips no-zwid."""
    from datetime import date, timedelta

    from apps.events.models import Event, Squad, SquadMember

    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True)
    squad = Squad.objects.create(event=event, name="Alpha")

    synced = user_model.objects.create(username="synced", zwid=7001, first_name="Syn")
    ZRRider.objects.create(zwid=7001, name="Synced Rider", weight=72, zp_ftp=300)
    unsynced = user_model.objects.create(username="unsynced", zwid=7002, first_name="Uns")
    no_zwid = user_model.objects.create(username="nozwid")
    for u in (synced, unsynced, no_zwid):
        SquadMember.objects.create(squad=squad, user=u, status=SquadMember.Status.MEMBER)

    resp = auth_client.post(reverse("ttt_planner:plan_squad_add", args=[plan.pk]), {"squad": squad.pk})
    assert resp.status_code == 200
    riders = plan.riders.all()
    assert set(riders.values_list("zwid", flat=True)) == {7001, 7002}  # no_zwid skipped
    synced_rider = riders.get(zwid=7001)
    assert float(synced_rider.weight_kg) == pytest.approx(72)
    assert synced_rider.ftp_w == 300
    unsynced_rider = riders.get(zwid=7002)
    assert unsynced_rider.weight_kg is None
    assert unsynced_rider.name  # name-only fallback present


@pytest.mark.django_db
def test_plan_squad_add_dedupes(auth_client, team_member, user_model):
    """Adding the same squad twice doesn't duplicate riders."""
    from datetime import date, timedelta

    from apps.events.models import Event, Squad, SquadMember

    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True)
    squad = Squad.objects.create(event=event, name="Alpha")
    u = user_model.objects.create(username="dup", zwid=7100)
    SquadMember.objects.create(squad=squad, user=u, status=SquadMember.Status.MEMBER)

    auth_client.post(reverse("ttt_planner:plan_squad_add", args=[plan.pk]), {"squad": squad.pk})
    auth_client.post(reverse("ttt_planner:plan_squad_add", args=[plan.pk]), {"squad": squad.pk})
    assert plan.riders.filter(zwid=7100).count() == 1


# ----- climb compare (per-rider climb strength) --------------------------------------------------


@pytest.mark.django_db
def test_climb_strength_per_rider_gaps(team_member):
    from apps.ttt_planner.services.compute import TTT_CLIMB_LENGTHS_M, climb_strength

    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    strong_w = {
        "power_w5": 1100, "power_w15": 880, "power_w30": 770, "power_w60": 560,
        "power_w120": 500, "power_w300": 420, "power_w1200": 360,
    }
    weak_w = {k: int(v * 0.75) for k, v in strong_w.items()}
    ZRRider.objects.create(zwid=101, name="Strong", weight=66, height=178, **strong_w)
    ZRRider.objects.create(zwid=102, name="Weak", weight=80, height=178, **weak_w)
    PlanRider.objects.create(plan=plan, order=0, name="Strong", zwid=101, weight_kg=66, height_cm=178, ftp_w=320)
    PlanRider.objects.create(plan=plan, order=1, name="Weak", zwid=102, weight_kg=80, height_cm=178, ftp_w=240)

    climb = climb_strength(plan)
    assert climb["available"] is True
    assert len(climb["lengths"]) == len(TTT_CLIMB_LENGTHS_M)
    assert len(climb["rows"]) == 2
    strong_row = next(r for r in climb["rows"] if r["name"] == "Strong")
    weak_row = next(r for r in climb["rows"] if r["name"] == "Weak")
    assert any(c["label"] == "0" for c in strong_row["cells"])  # strong+light sets the pace
    assert any(c.get("gap_s", 0) > 0 for c in weak_row["cells"])  # weak rider trails


@pytest.mark.django_db
def test_climb_strength_unavailable_without_zr(team_member):
    from apps.ttt_planner.services.compute import climb_strength

    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=40)
    PlanRider.objects.create(plan=plan, order=0, name="A", zwid=None, weight_kg=70, height_cm=175, ftp_w=300)
    assert climb_strength(plan)["available"] is False


# ----- route / segment editing (race-verified) ---------------------------------------------------


@pytest.mark.django_db
def test_route_create_gated_on_race_verified(client, user_model):
    member = user_model.objects.create_user(
        username="rv_member", permission_overrides={"team_member": True}, is_race_ready=False
    )
    client.force_login(member)
    assert client.get("/routes/new/").status_code == 403  # team member but not verified

    member.is_race_ready = True
    member.save(update_fields=["is_race_ready"])
    assert client.get("/routes/new/").status_code == 200

    resp = client.post(
        "/routes/new/",
        {
            "name": "Test Climb Route", "world": "Watopia", "distance_km": "12.5",
            "elevation_m": "180", "lead_in_distance_km": "0.3", "lead_in_elevation_m": "5",
            "recommended_laps": "3", "supports_laps": "on", "is_active": "on",
        },
    )
    assert resp.status_code == 302
    assert Route.objects.filter(name="Test Climb Route", recommended_laps=3, supports_laps=True).exists()


@pytest.mark.django_db
def test_segment_create_and_edit_race_verified(client, user_model):
    from apps.ttt_planner.models import Segment

    verified = user_model.objects.create_user(
        username="rv2", permission_overrides={"team_member": True}, is_race_ready=True
    )
    client.force_login(verified)

    # Preselects type from the query string.
    assert 'value="climb" selected' in client.get("/routes/segments/new/?type=climb").content.decode()

    resp = client.post(
        "/routes/segments/new/",
        {"segment_type": "climb", "name": "Epic KOM", "world": "Watopia", "length_m": "4100", "elevation_m": "320"},
    )
    assert resp.status_code == 302
    seg = Segment.objects.get(name="Epic KOM")
    assert seg.segment_type == "climb"

    resp = client.post(
        f"/routes/segments/{seg.pk}/edit/",
        {"segment_type": "climb", "name": "Epic KOM (rev)", "world": "Watopia",
         "length_m": "4200", "elevation_m": "350"},
    )
    assert resp.status_code == 302
    seg.refresh_from_db()
    assert seg.name == "Epic KOM (rev)" and seg.length_m == 4200


@pytest.mark.django_db
def test_segment_create_denied_for_unverified(client, user_model):
    member = user_model.objects.create_user(
        username="rv3", permission_overrides={"team_member": True}, is_race_ready=False
    )
    client.force_login(member)
    assert client.get("/routes/segments/new/").status_code == 403


# ----- segment import command --------------------------------------------------------------------


@pytest.mark.django_db
def test_import_segments_command(tmp_path):
    import json

    from django.core.management import call_command

    from apps.ttt_planner.models import Segment

    data = [
        {"name": "Test KOM", "type": "Climb", "category": "2", "direction": "Forward",
         "distance_km": 5.0, "grade_pct": 6.0,
         "url": "https://whatsonzwift.com/world/watopia/segment/test-kom/forward"},
        {"name": "Test Sprint", "type": "Sprint", "direction": "Reverse", "distance_m": 300,
         "grade_pct": None, "url": "https://whatsonzwift.com/world/london/segment/test-sprint/reverse"},
    ]
    f = tmp_path / "seg.json"
    f.write_text(json.dumps(data))

    call_command("import_segments", file=str(f))

    kom = Segment.objects.get(name="Test KOM")
    assert kom.segment_type == "climb" and kom.direction == "forward" and kom.world == "Watopia"
    assert kom.length_m == 5000 and kom.elevation_m == 300 and kom.category == "2"

    sprint = Segment.objects.get(name="Test Sprint")
    assert sprint.segment_type == "sprint" and sprint.direction == "reverse"
    assert sprint.world == "London" and sprint.length_m == 300

    call_command("import_segments", file=str(f))  # idempotent
    assert Segment.objects.filter(name="Test KOM").count() == 1


@pytest.mark.django_db
def test_admin_segment_import_button(client, superuser):
    from apps.ttt_planner.models import Segment

    client.force_login(superuser)
    resp = client.get(reverse("admin:ttt_planner_segment_import"))
    assert resp.status_code == 302  # redirects back to the changelist
    assert resp.url == reverse("admin:ttt_planner_segment_changelist")
    assert Segment.objects.count() > 100  # bundled dataset loaded


@pytest.mark.django_db
def test_segment_detail_view(auth_client):
    from apps.ttt_planner.models import Route, Segment

    seg = Segment.objects.create(
        name="Epic KOM", segment_type="climb", direction="forward", world="Watopia",
        length_m=9500, grade_pct=4, elevation_m=380, category="2",
    )
    route = Route.objects.create(name="Hilly Loop", world="Watopia", distance_km=20, elevation_m=400)
    route.segments.add(seg)

    resp = auth_client.get(reverse("routes:segment_detail", args=[seg.pk]))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Epic KOM" in body
    assert "Hilly Loop" in body  # lists routes containing the segment


@pytest.mark.django_db
def test_route_form_segments_filtered_to_world():
    from apps.ttt_planner.forms import RouteForm
    from apps.ttt_planner.models import Route, Segment

    Segment.objects.create(name="Watopia Climb", segment_type="climb", world="Watopia", length_m=1000)
    Segment.objects.create(name="London Climb", segment_type="climb", world="London", length_m=1000)
    route = Route.objects.create(name="Wato Route", world="Watopia", distance_km=10, elevation_m=0)

    offered = set(RouteForm(instance=route).fields["segments"].queryset.values_list("name", flat=True))
    assert offered == {"Watopia Climb"}  # only this route's world is offered

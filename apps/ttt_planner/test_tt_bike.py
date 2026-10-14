"""The TTT planner assumes every rider is on Zwift's TT bike.

Zwift works a rider's drag area out from their body (height and weight) and adds a fixed
amount for each piece of equipment; there is no riding position. The planner's body
formula matches Zwift's, and ``TTT_BIKE_CDA_M2`` adds the TT bike. The reference powers
come from the Zwift Speed Lab's physics, recovered from the game: a TT frame (-0.030 m²)
with a deep front wheel (-0.010) and a disc (-0.010), 8.5 kg of bike, holding 44 kph on
the flat.
"""

import pytest
from constance.test import override_config

from apps.ttt_planner.models import PlanRider, TttPlan
from apps.ttt_planner.services import compute, physics

ZWIFT_TT_AT_44_KPH = [(60, 168, 271), (75, 178, 321), (90, 190, 373)]
# The same riders on a road bike with middling wheels, by the same physics.
ZWIFT_ROAD_AT_44_KPH = [(60, 168, 318), (75, 178, 368), (90, 190, 420)]


@pytest.mark.parametrize(("kg", "cm", "watts"), ZWIFT_TT_AT_44_KPH)
@pytest.mark.django_db
def test_the_planner_matches_zwifts_tt_bike(kg, cm, watts):
    params = physics.params_from_constance()

    assert physics.power_for_speed(44, weight_kg=kg, height_cm=cm, params=params) == pytest.approx(watts, rel=0.01)


@pytest.mark.parametrize(("kg", "cm", "watts"), ZWIFT_ROAD_AT_44_KPH)
@pytest.mark.django_db
def test_without_the_bike_the_body_alone_sits_near_a_road_bike(kg, cm, watts):
    with override_config(TTT_BIKE_CDA_M2=0):
        params = physics.params_from_constance()

    assert params.bike_cda_m2 == 0
    assert physics.power_for_speed(44, weight_kg=kg, height_cm=cm, params=params) == pytest.approx(watts, rel=0.04)


@pytest.mark.django_db
def test_the_climb_compares_model_upright_road_riders_without_the_tt_bike():
    assert physics.params_from_constance(cda_coef_key="STD_CDA_COEF", bike_cda_key=None).bike_cda_m2 == 0
    assert physics.params_from_constance().bike_cda_m2 == pytest.approx(physics.TT_BIKE_CDA_M2)


@pytest.mark.django_db
def test_a_plans_pull_is_the_tt_bikes(team_member):
    plan = TttPlan.objects.create(created_by=team_member, target_speed_kph=44)
    PlanRider.objects.create(plan=plan, order=0, name="A", weight_kg=75, height_cm=178, ftp_w=320)

    assert compute.compute_plan(plan).riders[0].pull_power_w == pytest.approx(321, rel=0.01)


def test_the_drag_area_has_a_floor():
    params = physics.PhysicsParams(bike_cda_m2=-1.0)

    assert physics.estimate_cda(178, 75, params) == physics.MIN_CDA_M2

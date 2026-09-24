"""Names from the Zwift Racing API arrive with raw HTML entities (``Kr&ouml;ger``).

Stored or rendered as-is, Django escapes the ``&`` again and the page shows
``Kr&ouml;ger`` instead of ``Kröger``. These tests pin the decoding on both the
sync path (``_map_rider_to_model``) and the ladder planner path (``normalize``).
"""

import pytest

from apps.ladder_planner.services import normalize
from apps.zwiftracing.models import ZRRider
from apps.zwiftracing.tasks import _map_rider_to_model


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Louis Kr&ouml;ger", "Louis Kröger"),
        ("Jos&#233;", "José"),
        ("Tom &amp; Jerry", "Tom & Jerry"),
        ("Already Kröger", "Already Kröger"),
        ("  padded  ", "padded"),
        (None, ""),
    ],
)
def test_clean_name_decodes_entities(raw, expected) -> None:
    assert normalize.clean_name(raw) == expected


def test_sync_stores_decoded_name_and_club() -> None:
    payload = {"riderId": 7926943, "name": "Louis Kr&ouml;ger", "club": {"name": "Caf&eacute; Club"}}
    mapped = _map_rider_to_model(payload)
    assert mapped["name"] == "Louis Kröger"
    assert mapped["club_name"] == "Café Club"


def test_opponent_from_api_is_decoded() -> None:
    data = normalize.from_api({"riderId": 1, "name": "Kr&ouml;ger", "club": {"id": 2, "name": "Caf&eacute;"}})
    assert data["name"] == "Kröger"
    assert data["club_name"] == "Café"


@pytest.mark.django_db
def test_row_synced_before_the_fix_is_decoded_on_read() -> None:
    rider = ZRRider.objects.create(zwid=7926943, name="Louis Kr&ouml;ger")
    assert normalize.from_zrrider(rider)["name"] == "Louis Kröger"

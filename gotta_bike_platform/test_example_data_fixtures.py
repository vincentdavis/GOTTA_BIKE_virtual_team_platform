"""Guards for test/example_data/.

The fixtures there exist to document the shape of the ZwiftPower and
ZwiftRacing payloads, which is all the "Example data:" pointers in those apps'
model docstrings need. They are generated rather than captured, and the tests
here keep them that way: (a) small and plainly invented, and (b) faithful
enough in shape to still parse through the loaders that consume them.
"""

import json
import pathlib
from decimal import Decimal
from typing import Self

import pytest

from apps.zwiftpower.models import ZPEvent, ZPRiderResults, ZPTeamRiders
from apps.zwiftpower.tasks import update_team_results, update_team_riders
from apps.zwiftracing.models import ZRRider
from apps.zwiftracing.tasks import _map_rider_to_model

BASE = pathlib.Path(__file__).resolve().parent.parent / "test/example_data"

# Fixture riders sit well above the Zwift ID range in use (~1-8 million).
FAKE_ID_FLOOR = 9_000_000
RIDER_FILES = [
    "zwiftracing/rider_api.json",
    "zwiftracing/riders_api.json",
    "zwiftracing/club_api.json",
]


def _riders(payload: dict | list) -> list[dict]:
    """Normalise a ZwiftRacing payload to a list of rider objects.

    Args:
        payload: A club response, a rider list, or a single rider.

    Returns:
        The rider objects it contains.

    """
    if isinstance(payload, list):
        return payload
    if "riders" in payload:
        return payload["riders"]
    return [payload]


class FakeZPClient:
    """Stands in for ZPClient, returning a fixture instead of calling ZwiftPower."""

    def __init__(self, results: dict | None = None, riders: list | None = None) -> None:
        """Store the payloads this client should hand back."""
        self._results = results
        self._riders = riders

    def __enter__(self) -> Self:
        """Enter the context manager.

        Returns:
            Self.

        """
        return self

    def __exit__(self, *exc: object) -> bool:
        """Exit the context manager.

        Returns:
            False, so exceptions propagate.

        """
        return False

    def fetch_team_results(self) -> dict:
        """Return the team results fixture.

        Returns:
            The fixture payload.

        """
        return self._results

    def fetch_team_riders(self) -> list:
        """Return the team riders fixture.

        Returns:
            The fixture payload.

        """
        return self._riders


def test_names_and_ids_are_invented() -> None:
    """Every name is an invented one and every Zwift ID is in the fixture range."""
    results = json.loads((BASE / "zwiftpower/team_results.json").read_text())
    admin = json.loads((BASE / "zwiftpower/team_admin_api.json").read_text())

    names = {row["name"] for row in results["data"]} | {row["name"] for row in admin["data"]}
    zwids = {row["zwid"] for row in results["data"]} | {row["zwid"] for row in admin["data"]}

    for path in RIDER_FILES:
        for rider in _riders(json.loads((BASE / path).read_text())):
            names.add(rider["name"])
            zwids.add(rider["riderId"])

    assert all(name.startswith("Test Rider") for name in names), sorted(names)
    assert all(zwid >= FAKE_ID_FLOOR for zwid in zwids), sorted(zwids)
    # Rows are all one team, as the endpoint returns - but a placeholder one.
    assert {row["tname"] for row in results["data"]} == {"TEST TEAM"}


def test_no_fixture_is_large() -> None:
    """A fixture past a few tens of KB is a captured payload, not a shape sample."""
    oversized = {path.name: path.stat().st_size for path in BASE.rglob("*.json") if path.stat().st_size > 50_000}
    assert not oversized, oversized


@pytest.mark.django_db
def test_team_results_fixture_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The team_results fixture parses through update_team_results unchanged."""
    data = json.loads((BASE / "zwiftpower/team_results.json").read_text())
    monkeypatch.setattr("apps.zwiftpower.tasks.ZPClient", lambda: FakeZPClient(results=data))

    out = update_team_results.func()

    assert out["events_created"] == len(data["events"])
    assert out["results_created"] == len(data["data"])
    assert ZPEvent.objects.count() == len(data["events"])
    assert ZPRiderResults.objects.count() == len(data["data"])

    result = ZPRiderResults.objects.get(zwid=9000001, zid=9100001)
    assert result.name == "Test Rider 1"
    assert result.height == 178
    assert result.weight == Decimal("72.5")
    assert result.avg_power == 212
    assert result.avg_hr == 148


@pytest.mark.django_db
def test_team_admin_fixture_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The team_admin_api fixture parses through update_team_riders unchanged."""
    rows = json.loads((BASE / "zwiftpower/team_admin_api.json").read_text())["data"]
    monkeypatch.setattr("apps.zwiftpower.tasks.ZPClient", lambda: FakeZPClient(riders=rows))

    out = update_team_riders.func()

    assert out["created"] == len(rows)
    assert ZPTeamRiders.objects.count() == len(rows)

    rider = ZPTeamRiders.objects.get(zwid=9000001)
    assert rider.name == "Test Rider 1"
    assert rider.ftp == 250
    assert rider.weight == Decimal("72.5")


@pytest.mark.django_db
@pytest.mark.parametrize("path", RIDER_FILES)
def test_zwiftracing_fixtures_map_and_persist(path: str) -> None:
    """Each ZwiftRacing fixture maps onto ZRRider and round-trips through the ORM."""
    for rider in _riders(json.loads((BASE / path).read_text())):
        mapped = _map_rider_to_model(rider)

        assert mapped["height"] and mapped["weight"]
        assert mapped["club_name"] == "TEST CLUB"
        assert mapped["race_current_rating"] is not None
        assert mapped["power_w1200"] and mapped["power_wkg1200"]
        assert mapped["phenotype_value"] and mapped["phenotype_climber"] is not None

        obj, _ = ZRRider.objects.update_or_create(zwid=rider["riderId"], defaults=mapped)
        obj.refresh_from_db()
        assert obj.name == mapped["name"]


def test_club_rider_without_30_day_rating_is_represented() -> None:
    """One fixture rider keeps the sparse max30 block the live API returns."""
    club = json.loads((BASE / "zwiftracing/club_api.json").read_text())
    max30 = [rider["race"]["max30"] for rider in club["riders"]]

    sparse = [block for block in max30 if "mixed" not in block]
    assert sparse, "no rider exercises the 'no 30-day rating' shape"
    assert sparse[0]["rating"] == 0
    assert "expires" in sparse[0]

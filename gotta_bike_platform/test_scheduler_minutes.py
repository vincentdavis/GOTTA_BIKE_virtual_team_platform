"""Scheduler intervals in minutes as well as hours.

Most tasks want whole hours. One that mirrors webhook-driven data wants finer control,
so a registry entry may declare `minutes_setting` instead. Minutes is the canonical
unit the scheduler consumes, so an hours entry is converted rather than special-cased.
"""

import pytest

from gotta_bike_platform.task_registry import TASK_REGISTRY, get_scheduled_tasks, resolve_interval_minutes


@pytest.mark.django_db
def test_an_hours_entry_converts_to_minutes(settings) -> None:
    from constance import config

    config.SCHEDULER_SYNC_ZR_RIDERS_HOURS = 3

    assert resolve_interval_minutes({"hours_setting": "SCHEDULER_SYNC_ZR_RIDERS_HOURS"}) == 180


@pytest.mark.django_db
def test_a_minutes_entry_is_used_as_is() -> None:
    from constance import config

    config.SCHEDULER_REFRESH_ZWIFT_METRICS_MINUTES = 5

    assert resolve_interval_minutes(
        {"minutes_setting": "SCHEDULER_REFRESH_ZWIFT_METRICS_MINUTES"}
    ) == 5


@pytest.mark.django_db
def test_minutes_wins_when_an_entry_somehow_declares_both() -> None:
    """Entries declare one or the other; if both appear, the finer unit is authoritative."""
    from constance import config

    config.SCHEDULER_REFRESH_ZWIFT_METRICS_MINUTES = 5
    config.SCHEDULER_SYNC_ZR_RIDERS_HOURS = 3

    assert resolve_interval_minutes({
        "minutes_setting": "SCHEDULER_REFRESH_ZWIFT_METRICS_MINUTES",
        "hours_setting": "SCHEDULER_SYNC_ZR_RIDERS_HOURS",
    }) == 5


@pytest.mark.django_db
def test_the_zwift_metrics_task_is_registered_in_minutes() -> None:
    assert "minutes_setting" in TASK_REGISTRY["refresh_zwift_racing_metrics"]
    assert "hours_setting" not in TASK_REGISTRY["refresh_zwift_racing_metrics"]


@pytest.mark.django_db
def test_every_scheduled_entry_resolves_to_a_positive_interval() -> None:
    """A registry entry naming a setting that does not exist would break the scheduler."""
    jobs = get_scheduled_tasks()

    assert jobs
    for job in jobs:
        assert job["minutes"] > 0, job["id"]


@pytest.mark.django_db
def test_every_scheduled_entry_declares_exactly_one_unit() -> None:
    for task_id, info in TASK_REGISTRY.items():
        if not info.get("scheduled"):
            continue
        units = {"hours_setting", "minutes_setting"} & set(info)
        assert len(units) == 1, f"{task_id} declares {units or 'neither'}"

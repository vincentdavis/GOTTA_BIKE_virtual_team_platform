"""Availability sheets whose dates cross a daylight-saving change.

A grid stores one UTC start and end time for every day. The span is worked out from the first
local day (end = start + the local span), so a week that crosses a change keeps the builder's or
template's number of slots on every day; its later days show an hour off locally. Taking the
start from the first day and the end from the last, as before, lost a slot every day of the
spring week and added one every day of the autumn week -- on the builder's save and on the
weekly schedule's sheets alike.
"""

from datetime import date

import pytest

from apps.events.availability_schedules import grid_from_template, template_span_utc
from apps.events.models import AvailabilityGridTemplate, Event, Squad
from apps.events.tz_utils import convert_grid_to_local, convert_local_to_utc, convert_utc_to_local_config

LONDON = "Europe/London"
LA = "America/Los_Angeles"

#: (local start date, local end date, local start, local end, tz) -> stored UTC (dates, times).
CASES = [
    pytest.param(
        (date(2026, 3, 23), date(2026, 3, 29), "19:00", "21:00", LONDON),
        (date(2026, 3, 23), date(2026, 3, 29), "19:00", "21:00"),
        id="london-spring-forward-29-mar",
    ),
    pytest.param(
        (date(2026, 10, 19), date(2026, 10, 25), "19:00", "21:00", LONDON),
        (date(2026, 10, 19), date(2026, 10, 25), "18:00", "20:00"),
        id="london-fall-back-25-oct",
    ),
    pytest.param(
        (date(2026, 11, 27), date(2026, 12, 3), "19:00", "21:00", LONDON),
        (date(2026, 11, 27), date(2026, 12, 3), "19:00", "21:00"),
        id="london-ordinary-week",
    ),
    pytest.param(
        # US clocks go back 1 Nov; 20:00-23:30 PDT is 03:00-06:30 UTC the next day.
        (date(2026, 10, 26), date(2026, 11, 1), "20:00", "23:30", LA),
        (date(2026, 10, 27), date(2026, 11, 2), "03:00", "06:30"),
        id="la-fall-back-crosses-utc-midnight",
    ),
    pytest.param(
        # US clocks go forward 8 Mar; 20:00-23:30 PST is 04:00-07:30 UTC the next day.
        (date(2026, 3, 2), date(2026, 3, 8), "20:00", "23:30", LA),
        (date(2026, 3, 3), date(2026, 3, 9), "04:00", "07:30"),
        id="la-spring-forward-crosses-utc-midnight",
    ),
    pytest.param(
        # 00:30 BST is 23:30 UTC the day before, 00:30 GMT is 00:30 UTC the same day: dating the
        # end from the last day added a ninth day to an eight-day sheet.
        (date(2026, 10, 19), date(2026, 10, 26), "00:30", "02:00", LONDON),
        (date(2026, 10, 18), date(2026, 10, 25), "23:30", "01:00"),
        id="london-start-near-utc-midnight-keeps-its-day-count",
    ),
]


def _slots_per_day(utc: tuple[date, date, str, str], slot_duration: int) -> dict[str, int]:
    """Count the slots each stored UTC day of a grid produces, as riders' sheets draw them.

    Returns:
        ``{utc date: slot count}`` for every day of the grid.

    """
    start_date, end_date, start_time, end_time = utc
    days = [date.fromordinal(n).isoformat() for n in range(start_date.toordinal(), end_date.toordinal() + 1)]
    return {
        day: len(convert_grid_to_local([day], start_time, end_time, slot_duration, [], "UTC")["cell_map"])
        for day in days
    }


def _local_slots(start_time: str, end_time: str, slot_duration: int) -> int:
    """Work out how many slots the builder's local times mean, wrapping past midnight.

    Returns:
        The slot count.

    """
    minutes = int(end_time[:2]) * 60 + int(end_time[3:]) - int(start_time[:2]) * 60 - int(start_time[3:])
    return (minutes % (24 * 60)) // slot_duration


@pytest.mark.parametrize(("local", "stored"), CASES)
def test_a_range_across_a_change_keeps_the_first_days_span(local, stored) -> None:
    assert convert_local_to_utc(*local) == stored


@pytest.mark.parametrize(("local", "stored"), CASES)
def test_every_day_keeps_the_builders_number_of_slots(local, stored) -> None:
    start_date, end_date, start_time, end_time, tz = local
    span = convert_local_to_utc(start_date, end_date, start_time, end_time, tz)
    per_day = _slots_per_day(span, 30)

    assert len(per_day) == (end_date - start_date).days + 1
    assert set(per_day.values()) == {_local_slots(start_time, end_time, 30)}


@pytest.mark.parametrize(("local", "stored"), CASES)
def test_the_builder_re_edit_gets_back_what_was_entered(local, stored) -> None:
    *_, tz = local
    assert convert_utc_to_local_config(*stored, tz) == local[:4]


def _template(**fields) -> AvailabilityGridTemplate:
    defaults = {
        "name": "Evenings",
        "start_time": "19:00",
        "end_time": "21:00",
        "grid_timezone": LONDON,
        "slot_duration": 60,
        "default_length_days": 7,
    }
    return AvailabilityGridTemplate(**(defaults | fields))


@pytest.mark.parametrize(
    ("start_date", "stored"),
    [
        (date(2026, 3, 23), (date(2026, 3, 23), date(2026, 3, 29), "19:00", "21:00")),
        (date(2026, 10, 19), (date(2026, 10, 19), date(2026, 10, 25), "18:00", "20:00")),
    ],
)
def test_the_schedules_span_keeps_the_templates_slots(start_date, stored) -> None:
    template = _template()
    assert template_span_utc(template, start_date, template.start_time, template.grid_timezone) == stored


@pytest.mark.django_db
@pytest.mark.parametrize("start_date", [date(2026, 3, 23), date(2026, 10, 19)])
def test_a_scheduled_sheet_across_a_change_has_the_templates_slots_every_day(start_date) -> None:
    event = Event.objects.create(title="Ladder", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    squad = Squad.objects.create(event=event, name="Alpha")
    template = _template(squad=squad)
    template.save()

    grid = grid_from_template(template, squad, start_date)
    per_day = _slots_per_day((grid.start_date, grid.end_date, grid.start_time, grid.end_time), grid.slot_duration)

    assert len(grid.dates) == 7
    assert set(per_day.values()) == {2}

"""Course options on race-series stages: where each squad races a stage.

A stage has one course, or several named options ("Long", "Short") that each squad's captain
picks between. These tests hold the rule for which option a squad is on
(``series.squad_courses``), the two admin pages that set courses up, the captain's picker,
and every page that shows a squad's course.
"""

import importlib
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.urls import reverse

from apps.events import series
from apps.events.models import AvailabilityGrid, Event, SeriesStage, SheetStage, Squad, StageCourse
from apps.events.test_race_series import _formset, _member, _sheet, _times_post, _when
from apps.zwift_data.models import ZwiftRoute

COURSES = "courses"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the pending badge's per-user cache: SQLite reuses pks between tests."""
    cache.clear()


@pytest.fixture
def event(db) -> Event:
    """Build a visible race-series event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="Autumn Series",
        start_date=today,
        end_date=today + timedelta(days=60),
        visible=True,
        availability_type=Event.AvailabilityType.SERIES,
    )


@pytest.fixture
def volcano(db) -> ZwiftRoute:
    """Build a short route.

    Returns:
        The route.

    """
    return ZwiftRoute.objects.create(
        name="Volcano Circuit", world="Watopia", world_id=1, name_hash="volcano", distance_km=4.1, ascent_m=20
    )


@pytest.fixture
def tempus(db) -> ZwiftRoute:
    """Build a longer route.

    Returns:
        The route.

    """
    return ZwiftRoute.objects.create(
        name="Tempus Fugit", world="Watopia", world_id=1, name_hash="tempus", distance_km=17.3, ascent_m=16
    )


@pytest.fixture
def stages(event, volcano, tempus) -> list[SeriesStage]:
    """Build three stages: Long or Short, one course, then Short (on distance) or long.

    The last stage lists Short first and spells its other option "long", so a squad that
    picked Long on the first stage stays on it only by the carry-over rule.

    Returns:
        The stages, in order.

    """
    opener = SeriesStage.objects.create(event=event, order=0, name="Opener", stage_type="points")
    StageCourse.objects.create(stage=opener, order=0, name="Long", route=volcano, laps=3)
    StageCourse.objects.create(stage=opener, order=1, name="Short", route=volcano, laps=1)
    ttt = SeriesStage.objects.create(event=event, order=1, name="Team TT", stage_type="ttt")
    StageCourse.objects.create(stage=ttt, order=0, route=tempus, laps=1)
    finale = SeriesStage.objects.create(event=event, order=2, name="Finale", stage_type="points")
    StageCourse.objects.create(stage=finale, order=0, name="Short", route=tempus, distance_km=Decimal("20.0"))
    StageCourse.objects.create(stage=finale, order=1, name="long", route=tempus, laps=2)
    return [opener, ttt, finale]


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad on the series.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="A1", discord_channel_id=555)


def _option(stage: SeriesStage, name: str) -> StageCourse:
    """Find a stage's option by name.

    Returns:
        The course.

    """
    return stage.courses.get(name=name)


def _pick(grid: AvailabilityGrid, stage: SeriesStage, course: StageCourse | None) -> None:
    """Record a captain's pick of ``course`` on ``stage``."""
    SheetStage.objects.filter(grid=grid, stage=stage).update(course=course)


# --- The rules in apps.events.series --------------------------------------------------------


@pytest.mark.django_db
def test_a_course_reads_as_its_route_laps_and_distance(volcano) -> None:
    three_laps = StageCourse(route=volcano, laps=3)
    on_distance = StageCourse(name="Short", route=volcano, laps=3, distance_km=Decimal("10.0"))

    assert series.course_summary(three_laps) == "Volcano Circuit · 3 laps · 12.3 km"
    assert series.course_summary(StageCourse(route=volcano, laps=1)) == "Volcano Circuit · 4.1 km"
    # A race that finishes on distance gives the distance, and its laps say nothing.
    assert series.course_summary(on_distance) == "Volcano Circuit · 10.0 km"
    assert series.course_label(on_distance, named=True) == "Short: Volcano Circuit · 10.0 km"
    assert series.course_label(on_distance, named=False) == "Volcano Circuit · 10.0 km"
    assert series.course_summary(StageCourse(laps=1)) == ""
    assert series.course_summary(None) == ""


@pytest.mark.django_db
def test_a_stage_with_options_is_summed_up_by_their_names(stages) -> None:
    assert [series.stage_summary(stage) for stage in stages] == [
        "Long or Short",
        "Tempus Fugit · 17.3 km",
        "Short or long",
    ]


@pytest.mark.django_db
def test_a_squad_stays_on_the_option_it_picked(stages) -> None:
    opener, _, finale = stages

    def names(picks: dict) -> list[str]:
        chosen = series.squad_courses(stages, picks)
        return [chosen[stage.pk].name for stage in stages]

    # Nobody picked: every stage's first course.
    assert names({}) == ["Long", "", "Short"]
    # Long on the opener carries to the finale's "long", case aside, though it is listed second.
    assert names({opener.pk: _option(opener, "Long").pk}) == ["Long", "", "long"]
    # A pick of its own beats what is carried.
    assert names({opener.pk: _option(opener, "Long").pk, finale.pk: _option(finale, "Short").pk}) == [
        "Long",
        "",
        "Short",
    ]
    # A pick that is not one of the stage's own courses is ignored.
    assert names({finale.pk: _option(opener, "Long").pk}) == ["Long", "", "Short"]


@pytest.mark.django_db
def test_a_fallback_is_not_carried_on(event, stages) -> None:
    opener, _, finale = stages
    hills = SeriesStage.objects.create(event=event, order=1, name="Hills", stage_type="climb")
    StageCourse.objects.create(stage=hills, order=0, name="Hilly")
    StageCourse.objects.create(stage=hills, order=1, name="Flat")
    ordered = [opener, hills, finale]

    chosen = series.squad_courses(ordered, {opener.pk: _option(opener, "Long").pk})

    # Hills offers no Long, so the squad is on its first option there -- and still on Long after.
    assert [chosen[stage.pk].name for stage in ordered] == ["Long", "Hilly", "long"]


@pytest.mark.django_db
def test_a_stage_without_a_course_has_none(event) -> None:
    bare = SeriesStage.objects.create(event=event, order=0, name="Bare")

    assert series.squad_courses([bare], {}) == {bare.pk: None}
    assert series.stage_summary(bare) == ""


@pytest.mark.django_db
def test_the_migration_makes_each_stages_route_and_laps_its_first_course(event, volcano) -> None:
    """Migration 0078 copies every stage's route and laps onto a first, unnamed course.

    The stage table has no route or laps since 0079, so the old rows are stood in for.
    """
    migration = importlib.import_module("apps.events.migrations.0078_stage_courses")
    on_route = SeriesStage.objects.create(event=event, order=0, name="Opener")
    off_route = SeriesStage.objects.create(event=event, order=1, name="Team TT")
    old_rows = [
        SimpleNamespace(pk=on_route.pk, route_id=volcano.pk, laps=3),
        SimpleNamespace(pk=off_route.pk, route_id=None, laps=0),
    ]
    models = {"SeriesStage": SimpleNamespace(objects=SimpleNamespace(all=lambda: old_rows)), "StageCourse": StageCourse}

    migration.copy_stage_courses(SimpleNamespace(get_model=lambda app, name: models[name]), None)

    copied = [(c.stage_id, c.name, c.route_id, c.laps, c.order) for c in StageCourse.objects.order_by("stage__order")]
    assert copied == [(on_route.pk, "", volcano.pk, 3, 0), (off_route.pk, "", None, 1, 0)]


# --- The Stages page -----------------------------------------------------------------------------


def _stage_rows(stages: list[SeriesStage], **changes) -> dict:
    """Build the Stages page's POST for saved stages, ``changes`` keyed by stage index.

    Returns:
        The POST data.

    """
    rows = [
        {"id": str(stage.pk), "name": stage.name, "stage_type": stage.stage_type, "order": str(index)}
        for index, stage in enumerate(stages)
    ]
    for key, row_changes in changes.items():
        rows[int(key.removeprefix("row"))].update(row_changes)
    return _formset(rows, initial=len(rows))


@pytest.mark.django_db
def test_a_stage_row_edits_its_one_course(client, event, event_admin, stages, volcano) -> None:
    client.force_login(event_admin)

    response = client.post(
        reverse("events:series_stages", args=[event.pk]),
        _stage_rows(stages, row1={"route": str(volcano.pk), "laps": "", "distance_km": "25.5"}),
    )

    assert response.status_code == 302
    course = stages[1].courses.get()
    assert (course.route, course.laps, course.distance_km) == (volcano, 1, Decimal("25.5"))


@pytest.mark.django_db
def test_a_stage_row_refuses_a_lap_count_the_database_cannot_hold(client, event, event_admin, stages) -> None:
    client.force_login(event_admin)
    url = reverse("events:series_stages", args=[event.pk])

    negative = client.post(url, _stage_rows(stages, row1={"laps": "-2"}))
    huge = client.post(url, _stage_rows(stages, row1={"laps": "40000"}))

    assert negative.status_code == huge.status_code == 200
    assert stages[1].courses.get().laps == 1


@pytest.mark.django_db
def test_a_stage_with_options_lists_them_and_its_row_leaves_them_alone(
    client, event, event_admin, stages, volcano
) -> None:
    client.force_login(event_admin)
    url = reverse("events:series_stages", args=[event.pk])

    page = client.get(url).content.decode()
    # The rename saves the row, so a course posted for it would be written if it were taken.
    client.post(url, _stage_rows(stages, row0={"name": "Opening sprint", "route": "", "laps": "9"}))

    assert "Long: Volcano Circuit · 3 laps · 12.3 km" in page
    assert reverse("events:stage_courses", args=[event.pk, stages[0].pk]) in page
    assert 'name="series_stages-0-route"' not in page  # the row offers no route of its own
    assert 'name="series_stages-1-route"' in page
    assert SeriesStage.objects.get(pk=stages[0].pk).name == "Opening sprint"
    assert [(c.name, c.route, c.laps) for c in stages[0].courses.all()] == [
        ("Long", volcano, 3),
        ("Short", volcano, 1),
    ]


# --- The Course options page ---------------------------------------------------------------------


def _course_post(rows: list[dict], *, initial: int) -> dict:
    """Build the Course options page's POST.

    Returns:
        The POST data.

    """
    data = {
        f"{COURSES}-TOTAL_FORMS": str(len(rows)),
        f"{COURSES}-INITIAL_FORMS": str(initial),
        f"{COURSES}-MIN_NUM_FORMS": "0",
        f"{COURSES}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for key, value in {"laps": "1", "order": str(index), **row}.items():
            data[f"{COURSES}-{index}-{key}"] = value
    return data


def _saved(course: StageCourse, **changes) -> dict:
    """Build a POST row for a saved course.

    Returns:
        The row.

    """
    row = {
        "id": str(course.pk),
        "name": course.name,
        "route": str(course.route_id or ""),
        "laps": str(course.laps),
        "distance_km": str(course.distance_km or ""),
        "order": str(course.order),
    }
    return {**row, **changes}


@pytest.mark.django_db
def test_only_event_admins_reach_the_course_options_page(client, event, team_member, event_admin, stages) -> None:
    url = reverse("events:stage_courses", args=[event.pk, stages[1].pk])
    other = Event.objects.create(title="Other", start_date=date.today(), end_date=date.today())

    client.force_login(team_member)
    refused = client.get(url)
    client.force_login(event_admin)
    allowed = client.get(url)
    elsewhere = client.get(reverse("events:stage_courses", args=[other.pk, stages[1].pk]))

    assert refused.url == reverse("events:event_detail", args=[event.pk])
    assert allowed.status_code == 200
    assert "Stage 2: Team TT" in allowed.content.decode()
    assert elsewhere.status_code == 404


@pytest.mark.django_db
def test_options_are_added_named_and_kept_in_the_order_given(client, event, event_admin, stages, tempus) -> None:
    ttt = stages[1]
    client.force_login(event_admin)

    response = client.post(
        reverse("events:stage_courses", args=[event.pk, ttt.pk]),
        _course_post(
            [
                _saved(ttt.courses.get(), name="  Long  ", order="3"),
                {"name": "Short", "route": str(tempus.pk), "laps": "", "distance_km": "9.5", "order": "1"},
                {"name": "", "order": "7"},  # the blank "add" row, numbered by the page's script
            ],
            initial=1,
        ),
    )

    assert response.url == reverse("events:series_stages", args=[event.pk])
    assert [(c.name, c.order, c.laps, c.distance_km) for c in ttt.courses.all()] == [
        ("Short", 0, 1, Decimal("9.5")),
        ("Long", 1, 1, None),
    ]


@pytest.mark.django_db
def test_several_options_each_need_a_name_of_their_own(client, event, event_admin, stages) -> None:
    ttt = stages[1]
    client.force_login(event_admin)
    url = reverse("events:stage_courses", args=[event.pk, ttt.pk])

    unnamed = client.post(url, _course_post([_saved(ttt.courses.get()), {"name": "Short"}], initial=1))
    clash = client.post(url, _course_post([_saved(ttt.courses.get(), name="Long"), {"name": "LONG"}], initial=1))

    assert "Give every course option a name" in unnamed.content.decode()
    assert "Two course options are called LONG" in clash.content.decode()
    assert [c.name for c in ttt.courses.all()] == [""]


@pytest.mark.django_db
def test_a_stage_keeps_at_least_one_course(client, event, event_admin, stages) -> None:
    ttt = stages[1]
    client.force_login(event_admin)

    response = client.post(
        reverse("events:stage_courses", args=[event.pk, ttt.pk]),
        _course_post([_saved(ttt.courses.get(), DELETE="on")], initial=1),
    )

    assert "A stage needs a course" in response.content.decode()
    assert ttt.courses.count() == 1


@pytest.mark.django_db
def test_removing_a_picked_option_puts_its_squads_back_and_says_so(client, event, event_admin, squad, stages) -> None:
    opener = stages[0]
    grid = _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    _pick(grid, opener, _option(opener, "Short"))
    client.force_login(event_admin)

    response = client.post(
        reverse("events:stage_courses", args=[event.pk, opener.pk]),
        _course_post([_saved(_option(opener, "Long")), _saved(_option(opener, "Short"), DELETE="on")], initial=2),
    )

    assert response.status_code == 302
    assert [c.name for c in opener.courses.all()] == ["Long"]
    assert grid.sheet_stages.get(stage=opener).course is None
    assert any("1 squad had picked a removed option" in str(m) for m in get_messages(response.wsgi_request))


# --- A captain's pick, on the stage times page ----------------------------------------------------


@pytest.mark.django_db
def test_a_captain_picks_a_course_only_where_a_stage_has_options(client, event, event_admin, squad, stages) -> None:
    opener, ttt, finale = stages
    client.force_login(event_admin)
    url = reverse("events:series_sheet", args=[event.pk, squad.pk])

    form = client.get(url).context["form"]
    day = date(2027, 1, 12)
    starts = [(day, "19:30"), (day + timedelta(days=7), "19:30"), (day + timedelta(days=14), "19:30")]
    client.post(url, _times_post(stages, starts, **{f"course_{opener.pk}": str(_option(opener, "Long").pk)}))

    pickers = {name for name in form.fields if name.startswith("course_")}
    assert pickers == {f"course_{opener.pk}", f"course_{finale.pk}"}
    assert form.initial[f"course_{opener.pk}"] == _option(opener, "Long").pk
    assert form.initial[f"course_{finale.pk}"] == _option(finale, "Short").pk
    by_stage = {ss.stage_id: ss.course for ss in series.series_sheet(squad).sheet_stages.all()}
    assert by_stage == {opener.pk: _option(opener, "Long"), ttt.pk: None, finale.pk: None}
    # The finale was left to the carry-over, which now starts it on "long".
    again = client.get(url).context["form"]
    assert again.initial[f"course_{finale.pk}"] == _option(finale, "long").pk


@pytest.mark.django_db
def test_a_course_from_another_stage_is_refused(client, event, event_admin, squad, stages) -> None:
    opener, _, finale = stages
    client.force_login(event_admin)
    day = date(2027, 1, 12)
    starts = [(day, "19:30"), (day + timedelta(days=7), "19:30"), None]

    response = client.post(
        reverse("events:series_sheet", args=[event.pk, squad.pk]),
        _times_post(stages, starts, **{f"course_{opener.pk}": str(_option(finale, "Short").pk)}),
    )

    assert response.status_code == 200
    assert response.context["form"].errors[f"course_{opener.pk}"]
    assert series.series_sheet(squad) is None


@pytest.mark.django_db
def test_copying_another_squads_times_leaves_this_squads_course_alone(
    client, event, event_admin, squad, stages
) -> None:
    opener = stages[0]
    other = Squad.objects.create(event=event, name="B1")
    other_grid = _sheet(other, stages, [_when(11), _when(18), _when(25)])
    _pick(other_grid, opener, _option(opener, "Short"))
    client.force_login(event_admin)

    response = client.get(reverse("events:series_sheet", args=[event.pk, squad.pk]), {"copy_from": other.pk})

    initial = response.context["form"].initial
    assert initial[f"date_{opener.pk}"] == _when(11).date()  # the times are copied...
    assert initial[f"course_{opener.pk}"] == _option(opener, "Long").pk  # ...the course is not


# --- What riders, captains and coordinators see ---------------------------------------------------


@pytest.mark.django_db
def test_riders_see_the_course_their_squad_races(client, event, user_model, squad, stages) -> None:
    opener = stages[0]
    rider = _member(user_model, squad, "ana")
    grid = _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    _pick(grid, opener, _option(opener, "Short"))
    client.force_login(rider)

    response = client.get(reverse("events:availability_respond", args=[event.pk, squad.pk, grid.pk]))

    assert [row["route"] for row in response.context["rows"]] == [
        "Short: Volcano Circuit · 4.1 km",
        "Tempus Fugit · 17.3 km",
        "Short: Tempus Fugit · 20.0 km",
    ]


@pytest.mark.django_db
def test_a_new_race_starts_from_the_squads_course(client, event, event_admin, squad, stages) -> None:
    grid = _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    client.force_login(event_admin)

    response = client.get(reverse("events:availability_results", args=[event.pk, squad.pk, grid.pk]))

    defaults = response.context["slot_defaults_json"]
    opener = defaults["{}|{}".format(*series.slot_key(_when(11)))]
    finale = defaults["{}|{}".format(*series.slot_key(_when(25)))]
    assert (opener["laps"], opener["custom_finish_km"]) == (3, None)
    assert opener["course_url"].endswith(reverse("routes:detail", args=["volcano"]))
    # A course that finishes on distance prefills the finish, not a lap count.
    assert (finale["laps"], finale["custom_finish_km"]) == (None, 20.0)
    assert finale["course_url"].endswith(reverse("routes:detail", args=["tempus"]))


@pytest.mark.django_db
def test_the_board_is_about_stages_not_the_options_squads_picked(client, event, event_admin, squad, stages) -> None:
    """The coordinators' board stays a view of the stages (Vincent's call).

    A stage with options is described by their names; no cell says which one a squad races.
    """
    opener = stages[0]
    _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    other = Squad.objects.create(event=event, name="B1")
    other_grid = _sheet(other, stages, [_when(12), _when(19), _when(26)])
    _pick(other_grid, opener, _option(opener, "Short"))
    client.force_login(event_admin)

    response = client.get(reverse("events:series_board", args=[event.pk]))

    assert [column["route"] for column in response.context["columns"]] == [
        "Long or Short",
        "Tempus Fugit · 17.3 km",
        "Short or long",
    ]
    assert all("course" not in cell for row in response.context["rows"] for cell in row["cells"])
    assert "Course:" not in response.content.decode()


@pytest.mark.django_db
def test_the_admin_edits_a_stages_courses(admin_client, stages) -> None:
    response = admin_client.get(reverse("admin:events_seriesstage_change", args=[stages[0].pk]))

    assert response.status_code == 200
    assert "Stage Courses" in response.content.decode()

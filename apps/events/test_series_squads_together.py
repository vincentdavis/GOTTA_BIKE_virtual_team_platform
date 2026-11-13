"""Several squads' series answers on one page, "Squads together", opened from the series board.

Guards ``series_views.series_squads_view``, ``series_lineup_dialog_view`` and the board's tick
boxes: which squads can be opened and by whom, what each card shows to whom, and the lineup
dialog a card loads into the page.
"""

import json
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.events import series
from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    Event,
    SeriesStage,
    SheetStage,
    Squad,
    SquadMember,
)
from apps.events.series_views import MAX_SQUADS_TOGETHER
from conftest import _make_user

HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the per-user badge caches: SQLite reuses pks between tests."""
    cache.clear()


@pytest.fixture
def event(db) -> Event:
    """Build a visible race-series event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="Winter Series",
        start_date=today,
        end_date=today + timedelta(days=60),
        visible=True,
        availability_type=Event.AvailabilityType.SERIES,
    )


@pytest.fixture
def stages(event) -> list[SeriesStage]:
    """Build two stages.

    Returns:
        The stages, in order.

    """
    return [
        SeriesStage.objects.create(event=event, order=0, name="Opener", stage_type="points", riders_min=2),
        SeriesStage.objects.create(event=event, order=1, name="Team TT", stage_type="ttt", riders_min=2),
    ]


def _when(days: int, hour: int = 19, minute: int = 30) -> datetime:
    """Build a UTC start ``days`` from today.

    Returns:
        The datetime.

    """
    return datetime.combine(date.today() + timedelta(days=days), time(hour, minute), tzinfo=UTC)


def _squad(event, name, stages=None, *, gender="COED", tz="Europe/London", status=AvailabilityGrid.Status.PUBLISHED):
    """Build a squad, with a series sheet timing each stage when ``stages`` is given.

    Returns:
        The squad.

    """
    squad = Squad.objects.create(event=event, name=name, gender=gender, squad_timezone=tz)
    if stages is not None:
        grid = AvailabilityGrid(
            squad=squad, kind=AvailabilityGrid.Kind.SERIES, status=status, title=f"{name} stages", grid_timezone=tz
        )
        rows = [SheetStage(stage=stage, starts_at=_when(11 + 7 * i), racing=True) for i, stage in enumerate(stages)]
        series.sync_sheet_span(grid, rows)
        grid.save()
        for row in rows:
            row.grid = grid
            row.save()
    return squad


def _member(user_model, squad, username):
    """Add a team member to the squad.

    Returns:
        The user.

    """
    user = _make_user(user_model, username=username, permissions={"team_member": True})
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _answer(squad, user, stage, answer, note=""):
    """Store a rider's answer to one stage, for its current time."""
    grid = series.series_sheet(squad)
    entry = {"answer": answer, "for": series.stamp(grid.sheet_stages.get(stage=stage).starts_at)}
    if note:
        entry["note"] = note
    response, _ = AvailabilityResponse.objects.get_or_create(grid=grid, user=user)
    response.stage_answers = {**response.stage_answers, str(stage.pk): entry}
    response.save()


def _key(squad, stage) -> str:
    """Key a squad's time for a stage as the dialog does: ``<UTC date>|<HH:MM>``.

    Returns:
        The key.

    """
    utc_date, utc_time = series.slot_key(series.series_sheet(squad).sheet_stages.get(stage=stage).starts_at)
    return f"{utc_date.isoformat()}|{utc_time}"


def _box(page: str, squad) -> str:
    """Find a squad's tick box on the board.

    Returns:
        The ``<input>`` tag.

    """
    match = re.search(rf'<input type="checkbox" form="series-squad-pick" name="squad" value="{squad.pk}"[^>]*>', page)
    assert match, f"no box for {squad.name}"
    return match.group(0)


def _together(client, event, squads, **params):
    return client.get(reverse("events:series_squads", args=[event.pk]), {"squad": [s.pk for s in squads], **params})


def _dialog(client, event, squad, slot, *, headers=HX):
    return client.get(reverse("events:series_lineup_dialog", args=[event.pk, squad.pk]), {"slot": slot}, **headers)


# --- The board's tick boxes ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_board_offers_a_box_for_each_squad_whose_riders_were_asked(client, event, event_admin, stages):
    a1 = _squad(event, "A1", stages)
    draft = _squad(event, "C1", stages, status=AvailabilityGrid.Status.DRAFT)
    bare = _squad(event, "D1")
    client.force_login(event_admin)

    page = client.get(reverse("events:series_board", args=[event.pk]), {"squad": [a1.pk, draft.pk]}).content.decode()

    assert "checked" in _box(page, a1)
    assert "disabled" not in _box(page, a1)
    assert "disabled" in _box(page, draft)
    assert "checked" not in _box(page, draft)
    assert "disabled" in _box(page, bare)
    assert "Can't open yet: its times are a draft" in page
    assert "Can't open yet: no stage times" in page
    assert f'action="{reverse("events:series_squads", args=[event.pk])}"' in page
    assert "1 squad selected: A1" in page


@pytest.mark.django_db
def test_a_ticked_squad_the_gender_filter_hides_still_goes_with_the_form(client, event, event_admin, stages):
    women = _squad(event, "W1", stages, gender="Female")
    men = _squad(event, "B1", stages, gender="Male")
    client.force_login(event_admin)

    page = client.get(
        reverse("events:series_board", args=[event.pk]), {"gender": "male", "squad": [women.pk, men.pk]}
    ).content.decode()

    hidden = f'<input type="hidden" form="series-squad-pick" name="squad" value="{women.pk}" data-squad-name="W1">'
    assert hidden in page
    assert "checked" in _box(page, men)
    assert "2 squads selected: B1, W1" in page
    assert "hx-include=\"[form='series-squad-pick'][name='squad']\"" in page


@pytest.mark.django_db
def test_select_my_squads_knows_the_squads_the_viewer_leads(client, event, user_model, stages):
    a1 = _squad(event, "A1", stages)
    w1 = _squad(event, "W1", stages)
    w2 = _squad(event, "W2", stages)
    captain = _make_user(user_model, username="cap", permissions={"team_member": True})
    a1.captains.add(captain)
    w2.vice_captains.add(captain)
    client.force_login(captain)

    page = client.get(reverse("events:series_board", args=[event.pk])).content.decode()

    assert "Select my squads (2)" in page
    assert "data-mine" in _box(page, a1)
    assert "data-mine" in _box(page, w2)
    assert "data-mine" not in _box(page, w1)


# --- The page -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_together_shows_a_card_per_chosen_squad_that_can_be_opened(client, event, event_admin, stages):
    w1 = _squad(event, "W1", stages)
    a1 = _squad(event, "A1", stages)
    b1 = _squad(event, "B1", stages)
    draft = _squad(event, "C1", stages, status=AvailabilityGrid.Status.DRAFT)
    other_event = Event.objects.create(
        title="Other", start_date=date.today(), end_date=date.today(), availability_type=Event.AvailabilityType.SERIES
    )
    elsewhere = _squad(other_event, "Z1", stages)
    client.force_login(event_admin)

    response = _together(client, event, [w1, a1, draft, elsewhere])

    assert [card["squad"].name for card in response.context["cards"]] == ["A1", "W1"]
    assert [squad.name for squad in response.context["others"]] == ["B1"]
    page = response.content.decode()
    results = reverse("events:availability_results", args=[event.pk, a1.pk, series.series_sheet(a1).pk])
    assert f'href="{results}"' in page
    assert f"?squad={a1.pk}&amp;squad={w1.pk}" in page  # back to the board, still ticked
    assert b1.name in page  # offered under "Add a squad"


@pytest.mark.django_db
def test_together_is_for_the_people_who_see_the_board(client, event, team_member, stages):
    a1 = _squad(event, "A1", stages)
    client.force_login(team_member)

    response = _together(client, event, [a1])

    assert response.status_code == 302
    assert response.url == reverse("events:event_detail", args=[event.pk])


@pytest.mark.django_db
def test_a_weekly_event_has_no_squads_together(client, event, event_admin, stages):
    a1 = _squad(event, "A1", stages)
    Event.objects.filter(pk=event.pk).update(availability_type=Event.AvailabilityType.WEEKLY)
    client.force_login(event_admin)

    response = _together(client, event, [a1])

    assert response.status_code == 302


@pytest.mark.django_db
def test_only_so_many_squads_fit_on_one_page(client, event, event_admin, stages):
    squads = [_squad(event, f"S{number:02d}", stages) for number in range(MAX_SQUADS_TOGETHER + 1)]
    client.force_login(event_admin)

    response = _together(client, event, squads)

    assert len(response.context["cards"]) == MAX_SQUADS_TOGETHER
    assert f"Only {MAX_SQUADS_TOGETHER} squads fit on one page" in response.content.decode()


@pytest.mark.django_db
def test_a_captain_picks_their_own_squads_lineups_and_sees_another_read_only(client, event, user_model, stages):
    a1 = _squad(event, "A1", stages)
    w1 = _squad(event, "W1", stages)
    captain = _make_user(user_model, username="cap", permissions={"team_member": True})
    a1.captains.add(captain)
    _answer(a1, _member(user_model, a1, "ana"), stages[0], "maybe", note="Late shift that week")
    _answer(w1, _member(user_model, w1, "bea"), stages[0], "available", note="Driving there")
    client.force_login(captain)

    response = _together(client, event, [a1, w1])

    cards = {card["squad"].name: card for card in response.context["cards"]}
    assert cards["A1"]["is_event_admin"]
    assert cards["A1"]["dialog_url"] == reverse("events:series_lineup_dialog", args=[event.pk, a1.pk])
    assert [note["note"] for note in cards["A1"]["notes"]] == ["Late shift that week"]
    assert not cards["W1"]["is_event_admin"]
    assert cards["W1"]["dialog_url"] == ""
    assert cards["W1"]["notes"] == []
    page = response.content.decode()
    assert page.count('hx-target="#lineup-dialog-host"') == len(stages)  # A1's, one per stage
    assert "Pick A1's lineup for stage 1, Opener" in page
    assert "View only" in page
    assert "Driving there" not in page
    assert 'id="slot-modal"' not in page  # loaded per lineup, never with the page


@pytest.mark.django_db
def test_times_show_in_each_squads_own_zone_or_the_viewers(client, event, event_admin, stages):
    a1 = _squad(event, "A1", stages, tz="America/New_York")
    event_admin.timezone = "Europe/London"
    event_admin.save(update_fields=["timezone"])
    start = series.series_sheet(a1).sheet_stages.get(stage=stages[0]).starts_at
    client.force_login(event_admin)

    own = _together(client, event, [a1]).context["cards"][0]["columns"][0]["when"]
    mine = _together(client, event, [a1], tz="mine")

    assert own == series.when_label(start, ZoneInfo("America/New_York"))
    assert mine.context["cards"][0]["columns"][0]["when"] == series.when_label(start, ZoneInfo("Europe/London"))
    dialog_url = mine.context["cards"][0]["dialog_url"]
    assert dialog_url.endswith("?tz=mine")
    # The stage goes as its own value: tacked onto a URL that already has "?", it made "?tz=mine?slot=".
    key = _key(a1, stages[0])
    assert f'hx-get="{dialog_url}" hx-vals=\'{{"slot": "{key}"}}\'' in mine.content.decode()


# --- The lineup dialog a card loads ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_card_loads_its_squads_dialog_opened_on_the_stage(client, event, event_admin, user_model, stages):
    a1 = _squad(event, "A1", stages)
    ana = _member(user_model, a1, "ana")
    _answer(a1, ana, stages[1], "available")
    client.force_login(event_admin)

    response = _dialog(client, event, a1, _key(a1, stages[1]))

    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="slot-modal"' in page
    assert "A1 · Stage 2: Team TT · " in page
    assert 'aria-describedby="slot-modal-context"' in page
    assert 'id="lineup-dialog-slot"' in page
    assert response.context["utc_cell_answers_json"][_key(a1, stages[1])][str(ana.pk)]["state"] == "available"


@pytest.mark.django_db
def test_the_dialog_script_can_run_again(client, event, event_admin, stages):
    """A page of cards loads the dialog once per lineup, into one host, so its script runs again."""
    a1 = _squad(event, "A1", stages)
    client.force_login(event_admin)

    page = _dialog(client, event, a1, _key(a1, stages[0])).content.decode()

    assert "(function () {\n  const utcCellUsers" in page
    assert "window.openSlotModal = openSlotModal;" in page
    assert "if (window.__slotModalWired) return;" in page


@pytest.mark.django_db
def test_the_dialog_is_refused_to_who_cannot_pick_the_squads_lineups(client, event, user_model, stages):
    a1 = _squad(event, "A1", stages)
    w1 = _squad(event, "W1", stages)
    captain = _make_user(user_model, username="cap", permissions={"team_member": True})
    a1.captains.add(captain)
    client.force_login(captain)

    response = _dialog(client, event, w1, _key(w1, stages[0]))

    assert response.status_code == 204
    assert "showToast" in json.loads(response["HX-Trigger"])


@pytest.mark.django_db
def test_a_stage_whose_time_moved_asks_for_a_reload(client, event, event_admin, stages):
    a1 = _squad(event, "A1", stages)
    client.force_login(event_admin)

    response = _dialog(client, event, a1, "2001-01-01|19:30")

    assert response.status_code == 204
    assert "Reload the page" in json.loads(response["HX-Trigger"])["showToast"][0]["message"]


@pytest.mark.django_db
def test_the_dialog_outside_htmx_goes_to_the_squads_results_page(client, event, event_admin, stages):
    a1 = _squad(event, "A1", stages)
    client.force_login(event_admin)

    response = _dialog(client, event, a1, _key(a1, stages[0]), headers={})

    assert response.status_code == 302
    assert response.url == reverse("events:availability_results", args=[event.pk, a1.pk, series.series_sheet(a1).pk])


@pytest.mark.django_db
def test_the_results_page_names_the_squad_on_its_lineup_buttons(client, event, event_admin, stages):
    a1 = _squad(event, "A1", stages)
    client.force_login(event_admin)

    page = client.get(
        reverse("events:availability_results", args=[event.pk, a1.pk, series.series_sheet(a1).pk])
    ).content.decode()

    assert "Pick A1's lineup for stage 2, Team TT" in page
    assert "openSlotModal(" in page
    assert 'hx-target="#lineup-dialog-host"' not in page

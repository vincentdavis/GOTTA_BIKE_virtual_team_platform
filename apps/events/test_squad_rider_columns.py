"""The By Squad view offers the signup list's rider columns.

An expanded squad's rider table and the signups table render the same cells, from the same
partials (``events/_rider_cells.html`` and ``_rider_cells_more.html``), so a column added to one
reaches the other. The By Squad table keys them with ``sq_`` and has its own menu; columns new to
it start hidden. The columns only the full signup table shows -- the signup's own timezone and
notes, zFTP and zMAP, and the 53 Zwift Racing extras -- go to the same viewers there as on the
signups table, and to nobody else.
"""

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad, SquadMember


@pytest.fixture
def event(db):
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True, signups_open=True
    )


@pytest.fixture
def signup(event, user_model):
    """Sign a rider up and put them in a squad.

    Returns:
        The rider's signup.

    """
    rider = user_model.objects.create_user(
        username="rider", email="rider@example.test", first_name="Ana", last_name="R", zwid=4242
    )
    squad = Squad.objects.create(event=event, name="A")
    SquadMember.objects.create(squad=squad, user=rider, status=SquadMember.Status.MEMBER)
    return EventSignup.objects.create(
        event=event,
        user=rider,
        status=EventSignup.Status.REGISTERED,
        signup_timezone=["EU Central"],
        notes="Can only race after work",
    )


def _page(client, viewer, event):
    client.force_login(viewer)
    return client.get(reverse("events:event_detail", args=[event.pk])).content.decode()


def _header_keys(body, table_marker):
    table = body[body.index(table_marker) :]
    head = table[table.index("<thead>") : table.index("</thead>")]
    return re.findall(r'<th data-col="([a-z0-9_]+)"', head)


def _member_cell(body, signup, column):
    row = body[body.index(f'data-member-signup="{signup.pk}"') :]
    cell = row[row.index(f'<td data-col="{column}"') :]
    return cell[: cell.index("</td>")]


@pytest.mark.django_db
def test_an_expanded_squad_offers_every_column_of_the_signup_list(client, event, signup, event_admin):
    """In the same order, less the Squad column, which a squad's own table has no use for."""
    body = _page(client, event_admin, event)

    signup_columns = [key for key in _header_keys(body, 'data-signup-table="detail"') if key != "squad"]
    squad_columns = [key.removeprefix("sq_") for key in _header_keys(body, 'class="table table-xs squad-member-table"')]

    assert squad_columns == signup_columns
    assert {"zwid", "timezone", "zftp", "zmap", "zauth", "notes"} <= set(squad_columns)
    # Every column has its box in the By Squad menu, and so do the 53 Zwift Racing extras.
    boxes = re.findall(r'squad-col-toggle" data-col="sq_([a-z0-9_]+)"', body)
    assert set(squad_columns) <= set(boxes)
    assert body.count('class="checkbox checkbox-xs squad-stat-col-toggle"') == 53


@pytest.mark.django_db
def test_a_riders_row_carries_their_signup(client, event, signup, event_admin):
    """The timezone and notes are the signup's, and the row names it for the Zwift Racing columns."""
    body = _page(client, event_admin, event)

    assert "EU Central" in _member_cell(body, signup, "sq_timezone")
    assert 'data-tip="Can only race after work"' in _member_cell(body, signup, "sq_notes")
    assert '<script id="signup-stat-values" type="application/json">' in body


@pytest.mark.django_db
@pytest.mark.parametrize("show_signups", [False, True])
def test_a_member_without_the_signup_table_gets_only_the_squad_columns_it_had(
    client, event, signup, team_member, show_signups
):
    """Names-only or no list at all: none of what only the full table shows reaches the page."""
    event.show_signups = show_signups
    event.save(update_fields=["show_signups"])

    body = _page(client, team_member, event)

    assert _header_keys(body, 'class="table table-xs squad-member-table"') == [
        "sq_name", "sq_gender", "sq_category", "sq_rating", "sq_ftp", "sq_wkg", "sq_verified",
        "sq_zr_age", "sq_zr_cur_rating", "sq_zr_cur_cat", "sq_zr_max30_rating", "sq_zr_max30_cat",
        "sq_zr_max90_rating", "sq_zr_max90_cat", "sq_zr_phenotype",
    ]  # fmt: skip
    assert "data-member-signup=" not in body
    assert "Can only race after work" not in body
    assert "squad-stat-col-toggle" not in body
    assert 'id="signup-stat-values"' not in body


def test_new_squad_columns_start_hidden():
    """An expanded squad looks as it did until somebody picks one."""
    template = (Path(__file__).resolve().parents[2] / "templates/events/event_detail.html").read_text()
    defaults = template[template.index("var STORAGE_KEY = 'event_squad_cols';") :]
    defaults = defaults[: defaults.index("};")]
    for key in ("zwid", "timezone", "squad_gender", "zftp", "zmap", "zauth", "event_role", "notes", "custom_answers"):
        assert f"sq_{key}: false" in defaults, key

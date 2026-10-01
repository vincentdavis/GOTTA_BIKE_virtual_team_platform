"""Any team member may edit a planner document; only its owners delete it or pick its squad.

Ladder matchups and TTT plans share one rule (``gotta_bike_platform/planner_access.py``),
so every test here runs against both. Someone who neither created the document nor is on
its edit squad opens it read-only and confirms ("Edit anyway") before the controls show;
the server takes their changes either way and names them as its last editor.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, Squad, SquadMember
from apps.ladder_planner.models import LadderMatchup, LadderRider, Side
from apps.ttt_planner.models import PlanRider, TttPlan

PLANNERS = {
    "ladder": {"ns": "ladder_planner", "noun": "matchup", "dialog": "ladder-edit-dialog"},
    "ttt": {"ns": "ttt_planner", "noun": "plan", "dialog": "ttt-edit-dialog"},
}


@pytest.fixture(params=sorted(PLANNERS))
def kind(request):
    return request.param


@pytest.fixture
def owner(user_model):
    return user_model.objects.create_user(
        username="planner-owner", discord_nickname="Olive", permission_overrides={"team_member": True}
    )


def _create(kind, owner, **kwargs):
    if kind == "ladder":
        return LadderMatchup.objects.create(created_by=owner, our_team_name="Us", opponent_team_name="Them", **kwargs)
    return TttPlan.objects.create(created_by=owner, target_speed_kph=40, **kwargs)


def _add_rider(kind, doc):
    if kind == "ladder":
        return LadderRider.objects.create(
            matchup=doc, side=Side.OURS, order=0, zwid=101, name="Ana", zr_data={"name": "Ana", "zwid": 101}
        )
    return PlanRider.objects.create(plan=doc, order=0, name="Ana", weight_kg=70, height_cm=175, ftp_w=250)


def _url(kind, name, doc, *args, query=""):
    return reverse(f"{PLANNERS[kind]['ns']}:{name}", args=[doc.pk, *args]) + query


def _squad(*members):
    today = timezone.now().date()
    event = Event.objects.create(
        title="Series", start_date=today - timedelta(days=1), end_date=today + timedelta(days=7), visible=True
    )
    squad = Squad.objects.create(event=event, name="Alpha")
    for member in members:
        SquadMember.objects.create(squad=squad, user=member, status=SquadMember.Status.MEMBER)
    return squad


def _reloaded(doc):
    return type(doc).objects.get(pk=doc.pk)


# --- what each person sees -----------------------------------------------------------------------


@pytest.mark.django_db
def test_an_outsider_opens_it_read_only_with_the_edit_anyway_step(client, kind, owner, team_member):
    doc = _create(kind, owner)
    client.force_login(team_member)

    resp = client.get(_url(kind, "detail", doc))
    body = resp.content.decode()

    assert resp.status_code == 200
    assert resp.context["can_edit"] is False
    assert resp.context["can_manage"] is False
    assert f'id="{PLANNERS[kind]["dialog"]}"' in body
    assert "Edit anyway" in body
    assert 'name="name"' not in body  # no settings form
    assert _url(kind, "delete", doc) not in body


@pytest.mark.django_db
def test_confirming_shows_the_edit_controls_but_not_delete_or_the_squad_picker(client, kind, owner, team_member):
    doc = _create(kind, owner)
    client.force_login(team_member)

    resp = client.get(_url(kind, "detail", doc, query="?edit=1"))
    body = resp.content.decode()

    assert resp.context["can_edit"] is True
    assert resp.context["can_manage"] is False
    assert 'name="name"' in body
    assert "Done editing" in body
    assert "Edit anyway" not in body
    assert 'name="edit_squad"' not in body
    assert _url(kind, "delete", doc) not in body


@pytest.mark.django_db
def test_the_creator_and_the_squad_are_never_asked(client, kind, owner, team_member):
    doc = _create(kind, owner, edit_squad=_squad(team_member))

    for user in (owner, team_member):
        client.force_login(user)
        resp = client.get(_url(kind, "detail", doc))
        body = resp.content.decode()

        assert resp.context["can_manage"] is True
        assert "Edit anyway" not in body
        assert "Done editing" not in body
        assert 'name="edit_squad"' in body
        assert _url(kind, "delete", doc) in body


@pytest.mark.django_db
def test_the_dialog_names_the_squad_and_the_creator(client, kind, owner, team_member):
    doc = _create(kind, owner, edit_squad=_squad())
    client.force_login(team_member)

    body = client.get(_url(kind, "detail", doc)).content.decode()

    assert f"You are not a member of this {PLANNERS[kind]['noun']}&rsquo;s squad" in body
    assert "Series &mdash; Alpha" in body
    assert "(Olive did)" in body


@pytest.mark.django_db
def test_without_a_squad_the_dialog_only_names_the_creator(client, kind, owner, team_member):
    doc = _create(kind, owner)
    client.force_login(team_member)

    body = client.get(_url(kind, "detail", doc)).content.decode()

    assert f"You did not create this {PLANNERS[kind]['noun']} (Olive did)." in body
    assert "not a member" not in body


@pytest.mark.django_db
def test_the_last_editor_shows_on_the_page(client, kind, owner, team_member, user_model):
    doc = _create(kind, owner)
    client.force_login(owner)
    assert "last edited by" not in client.get(_url(kind, "detail", doc)).content.decode()  # nobody yet

    sam = user_model.objects.create_user(
        username="sam", discord_nickname="Sam", permission_overrides={"team_member": True}
    )
    type(doc).objects.filter(pk=doc.pk).update(updated_by=sam)

    assert "last edited by Sam" in client.get(_url(kind, "detail", doc)).content.decode()
    client.force_login(sam)
    assert "last edited by you" in client.get(_url(kind, "detail", doc)).content.decode()


# --- what the server takes -----------------------------------------------------------------------


@pytest.mark.django_db
def test_an_outsiders_change_lands_and_names_them(client, kind, owner, team_member):
    doc = _create(kind, owner, name="Before")
    client.force_login(team_member)

    resp = client.post(_url(kind, "update", doc), {"name": "After"}, HTTP_HX_REQUEST="true")

    doc = _reloaded(doc)
    assert resp.status_code == 200
    assert doc.name == "After"
    assert doc.updated_by == team_member


@pytest.mark.django_db
def test_a_rider_change_also_names_the_editor_and_moves_updated_at(client, kind, owner, team_member):
    """Rider rows used to change without touching the document, so its "Updated" date stood still."""
    doc = _create(kind, owner)
    rider = _add_rider(kind, doc)
    before = _reloaded(doc).updated_at
    client.force_login(team_member)

    resp = client.post(_url(kind, "rider_remove", doc, rider.pk), HTTP_HX_REQUEST="true")

    doc = _reloaded(doc)
    assert resp.status_code == 200
    assert not type(rider).objects.filter(pk=rider.pk).exists()
    assert doc.updated_by == team_member
    assert doc.updated_at > before


@pytest.mark.django_db
def test_an_outsider_cannot_delete_it(client, kind, owner, team_member):
    doc = _create(kind, owner)
    client.force_login(team_member)

    resp = client.post(_url(kind, "delete", doc))

    assert resp.status_code == 403
    assert type(doc).objects.filter(pk=doc.pk).exists()


@pytest.mark.django_db
def test_an_outsider_cannot_choose_its_squad(client, kind, owner, team_member):
    """Refused before anything saves, so the name posted alongside is not applied either."""
    squad = _squad()
    doc = _create(kind, owner, name="Kept")
    client.force_login(team_member)

    resp = client.post(
        _url(kind, "update", doc), {"name": "Changed", "edit_squad": str(squad.pk)}, HTTP_HX_REQUEST="true"
    )

    doc = _reloaded(doc)
    assert resp.status_code == 403
    assert doc.edit_squad_id is None
    assert doc.name == "Kept"
    assert doc.updated_by is None


@pytest.mark.django_db
def test_the_squad_can_still_delete_it(client, kind, owner, team_member):
    doc = _create(kind, owner, edit_squad=_squad(team_member))
    client.force_login(team_member)

    resp = client.post(_url(kind, "delete", doc))

    assert resp.status_code == 302
    assert not type(doc).objects.filter(pk=doc.pk).exists()


# --- the lazy panels follow the page's mode ------------------------------------------------------

# Each planner has one panel loaded after the page, with an edit control inside it.
PANELS = {
    "ladder": {"url": "climb", "control": 'name="cda_coef"'},
    "ttt": {"url": "zwiftgopher_panel", "control": 'name="route_schedule"'},
}


@pytest.mark.django_db
def test_the_lazy_panel_shows_its_control_only_in_edit_mode(client, kind, owner, team_member):
    doc = _create(kind, owner)
    panel = PANELS[kind]
    client.force_login(team_member)

    with patch("apps.ttt_planner.views.zwiftgopher_client.is_configured", return_value=True):
        read_only = client.get(_url(kind, panel["url"], doc)).content.decode()
        editing = client.get(_url(kind, panel["url"], doc, query="?edit=1")).content.decode()

    assert panel["control"] not in read_only
    assert panel["control"] in editing


@pytest.mark.django_db
def test_the_page_asks_for_the_panel_in_its_own_mode(client, kind, owner, team_member):
    doc = _create(kind, owner)
    panel_url = _url(kind, PANELS[kind]["url"], doc)
    client.force_login(team_member)

    read_only = client.get(_url(kind, "detail", doc)).content.decode()
    editing = client.get(_url(kind, "detail", doc, query="?edit=1")).content.decode()

    assert f'hx-get="{panel_url}"' in read_only
    assert f'hx-get="{panel_url}?edit=1"' in editing


@pytest.mark.django_db
def test_entering_edit_mode_logs_ids_only(client, kind, owner, team_member):
    doc = _create(kind, owner, name="Secret plan name")
    client.force_login(team_member)

    with patch(f"apps.{PLANNERS[kind]['ns']}.views.logfire") as log:
        client.get(_url(kind, "detail", doc, query="?edit=1"))

    (message,), fields = log.info.call_args
    assert "opened for editing by a non-owner" in message
    assert fields["user_id"] == team_member.id
    assert str(doc.pk) in fields.values()
    assert "Secret plan name" not in str(fields)

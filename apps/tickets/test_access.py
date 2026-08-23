"""Who can see which tickets.

Before the ``ticket_admin`` permission existed, every team member could read and edit
every ticket -- including the membership tickets the guild sync files when someone
leaves. Access is now the queue for admins, and your own tickets for everyone else.
"""

import pytest
from django.urls import reverse

from apps.tickets.models import Ticket


@pytest.fixture
def ticket_admin(db, user_model):
    """Build a member granted ticket_admin via override rather than a Discord role.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username="ticketadmin", email="ta@example.test",
        permission_overrides={"team_member": True, "ticket_admin": True},
    )


@pytest.fixture
def other_member(db, user_model):
    """Build a plain team member with no ticket involvement.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username="other", email="other@example.test",
        permission_overrides={"team_member": True},
    )


def _ticket(submitter, title="Someone left", **extra) -> Ticket:
    """Create a ticket.

    Returns:
        The ticket.

    """
    return Ticket.objects.create(title=title, details="d", submitted_by=submitter, **extra)


@pytest.mark.django_db
def test_a_member_sees_only_their_own_tickets(client, team_member, other_member) -> None:
    mine = _ticket(team_member, title="My own thing")
    theirs = _ticket(other_member, title="Not my business")
    client.force_login(team_member)

    body = client.get(reverse("tickets:ticket_list")).content.decode()

    assert mine.title in body
    assert theirs.title not in body


@pytest.mark.django_db
def test_a_member_cannot_open_someone_elses_ticket(client, team_member, other_member) -> None:
    """404, not 403 -- a 403 confirms the ticket exists."""
    theirs = _ticket(other_member)
    client.force_login(team_member)

    assert client.get(reverse("tickets:ticket_detail", args=[theirs.pk])).status_code == 404


@pytest.mark.django_db
def test_a_member_cannot_edit_someone_elses_ticket(client, team_member, other_member) -> None:
    theirs = _ticket(other_member)
    client.force_login(team_member)

    assert client.get(reverse("tickets:ticket_edit", args=[theirs.pk])).status_code == 404
    resp = client.post(
        reverse("tickets:ticket_edit", args=[theirs.pk]),
        data={"title": "hijacked", "details": "d", "category": theirs.category,
              "priority": theirs.priority, "status": theirs.status, "resolution": ""},
    )
    assert resp.status_code == 404
    theirs.refresh_from_db()
    assert theirs.title != "hijacked"


@pytest.mark.django_db
def test_the_assignee_can_see_it_even_though_they_did_not_submit_it(client, team_member, other_member) -> None:
    """The assignee picker offers every user, so an assignee must be able to open it."""
    assigned = _ticket(other_member, title="Please handle this", assigned_to=team_member)
    client.force_login(team_member)

    assert client.get(reverse("tickets:ticket_detail", args=[assigned.pk])).status_code == 200
    assert assigned.title in client.get(reverse("tickets:ticket_list")).content.decode()


@pytest.mark.django_db
def test_a_ticket_admin_sees_the_whole_queue(client, ticket_admin, other_member) -> None:
    theirs = _ticket(other_member, title="Not my business")
    client.force_login(ticket_admin)

    assert theirs.title in client.get(reverse("tickets:ticket_list")).content.decode()
    assert client.get(reverse("tickets:ticket_detail", args=[theirs.pk])).status_code == 200
    assert client.get(reverse("tickets:ticket_edit", args=[theirs.pk])).status_code == 200


@pytest.mark.django_db
def test_a_superuser_sees_the_whole_queue(client, superuser, other_member) -> None:
    """Superusers short-circuit has_permission, so this needs no role setup."""
    theirs = _ticket(other_member, title="Not my business")
    client.force_login(superuser)

    assert theirs.title in client.get(reverse("tickets:ticket_list")).content.decode()


@pytest.mark.django_db
def test_anyone_can_still_file_a_ticket(client, team_member) -> None:
    """Restricting reads must not stop members raising tickets in the first place."""
    client.force_login(team_member)

    assert client.get(reverse("tickets:ticket_create")).status_code == 200


@pytest.mark.django_db
def test_the_mine_filter_still_narrows_within_what_is_visible(client, ticket_admin, other_member) -> None:
    """`mine=1` is a convenience toggle, not the access control."""
    own = _ticket(ticket_admin, title="Raised by the admin")
    theirs = _ticket(other_member, title="Raised by someone else")
    client.force_login(ticket_admin)

    body = client.get(reverse("tickets:ticket_list") + "?mine=1").content.decode()

    assert own.title in body
    assert theirs.title not in body


@pytest.mark.django_db
def test_system_generated_tickets_reach_admins_only(client, team_member, ticket_admin) -> None:
    """The guild sync files these with submitted_by=None, so they belong to nobody.

    They are also the most sensitive rows in the queue -- they record who left the
    team -- so "belongs to nobody" must mean admins only, never everybody.
    """
    system = Ticket.objects.create(
        title="Member left the server", details="Auto-generated.", submitted_by=None,
    )

    client.force_login(team_member)
    assert system.title not in client.get(reverse("tickets:ticket_list")).content.decode()
    assert client.get(reverse("tickets:ticket_detail", args=[system.pk])).status_code == 404

    client.force_login(ticket_admin)
    assert system.title in client.get(reverse("tickets:ticket_list")).content.decode()


@pytest.mark.django_db
def test_the_assignee_dropdown_lists_only_team_members(client, ticket_admin, user_model) -> None:
    """The picker used to offer every row in the user table, team member or not."""
    member = user_model.objects.create_user(
        username="amember", email="am@example.test", first_name="Ateam", last_name="Member",
        permission_overrides={"team_member": True},
    )
    outsider = user_model.objects.create_user(
        username="anoutsider", email="ao@example.test", first_name="Nota", last_name="Member",
    )
    inactive = user_model.objects.create_user(
        username="aninactive", email="ai@example.test", first_name="Gone", last_name="Away",
        permission_overrides={"team_member": True}, is_active=False,
    )
    ticket = _ticket(ticket_admin)
    client.force_login(ticket_admin)

    resp = client.get(reverse("tickets:ticket_edit", args=[ticket.pk]))
    choices = {u.pk for u in resp.context["form"].fields["assigned_to"].queryset}

    assert member.pk in choices
    assert outsider.pk not in choices
    assert inactive.pk not in choices


@pytest.mark.django_db
def test_assigning_to_a_non_member_is_rejected_on_post(client, ticket_admin, user_model) -> None:
    """Trimming the dropdown is presentation; the queryset is what actually validates."""
    outsider = user_model.objects.create_user(username="out2", email="o2@example.test")
    ticket = _ticket(ticket_admin)
    client.force_login(ticket_admin)

    resp = client.post(
        reverse("tickets:ticket_edit", args=[ticket.pk]),
        data={"title": ticket.title, "details": "d", "category": ticket.category,
              "priority": ticket.priority, "status": ticket.status,
              "assigned_to": outsider.pk, "resolution": ""},
    )

    assert resp.status_code == 200                      # redisplayed with errors
    assert "assigned_to" in resp.context["form"].errors
    ticket.refresh_from_db()
    assert ticket.assigned_to_id is None


@pytest.mark.django_db
def test_an_assignee_who_left_the_team_does_not_block_editing(client, ticket_admin, user_model) -> None:
    """Dropping them from the queryset would fail validation on an unrelated edit."""
    former = user_model.objects.create_user(
        username="former", email="f@example.test", first_name="Former", last_name="Member",
    )
    ticket = _ticket(ticket_admin, assigned_to=former)
    client.force_login(ticket_admin)

    resp = client.post(
        reverse("tickets:ticket_edit", args=[ticket.pk]),
        data={"title": "Retitled", "details": "d", "category": ticket.category,
              "priority": ticket.priority, "status": ticket.status,
              "assigned_to": former.pk, "resolution": ""},
    )

    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.title == "Retitled"


@pytest.mark.django_db
def test_anonymous_visitors_are_sent_to_log_in(client) -> None:
    for name, args in (
        ("tickets:ticket_list", []), ("tickets:ticket_create", []),
        ("tickets:ticket_detail", [1]), ("tickets:ticket_edit", [1]),
    ):
        resp = client.get(reverse(name, args=args))
        assert resp.status_code == 302, name
        assert "/accounts/login/" in resp["Location"], name


@pytest.mark.django_db
def test_a_logged_in_non_team_member_gets_nowhere(client, user) -> None:
    """`user` has no permissions at all -- a Discord login without the team role."""
    for name, args in (
        ("tickets:ticket_list", []), ("tickets:ticket_create", []),
        ("tickets:ticket_detail", [1]), ("tickets:ticket_edit", [1]),
    ):
        client.force_login(user)
        resp = client.get(reverse(name, args=args))
        assert resp.status_code in (302, 403), name
        assert resp.status_code != 200, name

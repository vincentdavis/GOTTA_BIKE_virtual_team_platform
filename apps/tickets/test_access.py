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

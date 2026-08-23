"""Tests for the tickets app views."""

import pytest
from django.urls import reverse

from apps.tickets.models import Ticket


@pytest.fixture
def admin_client_(db, user_model, client):
    """Build a logged-in holder of ``ticket_admin``.

    System-generated tickets have ``submitted_by=None``, so they belong to nobody and
    only the whole-queue permission reaches them. These tests are about the template
    surviving a NULL submitter, so they need a client that can see one.

    Returns:
        The logged-in test client.

    """
    user = user_model.objects.create_user(
        username="tickets_tpl_admin", email="tta@example.test",
        permission_overrides={"team_member": True, "ticket_admin": True},
    )
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_ticket_list_renders_with_system_generated_ticket(admin_client_):
    """A ticket with no submitted_by (system-generated) must not break the list."""
    Ticket.objects.create(
        title="Member left the server",
        details="Auto-generated cleanup ticket.",
        submitted_by=None,
    )
    response = admin_client_.get(reverse("tickets:ticket_list"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Member left the server" in body
    assert "System" in body


@pytest.mark.django_db
def test_ticket_detail_renders_with_system_generated_ticket(admin_client_):
    ticket = Ticket.objects.create(
        title="Member left the server",
        details="Auto-generated cleanup ticket.",
        submitted_by=None,
    )
    response = admin_client_.get(reverse("tickets:ticket_detail", args=[ticket.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "System" in body

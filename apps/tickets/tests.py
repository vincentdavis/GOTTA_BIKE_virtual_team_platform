"""Tests for the tickets app views."""

import pytest
from django.urls import reverse

from apps.tickets.models import Ticket


@pytest.mark.django_db
def test_ticket_list_renders_with_system_generated_ticket(auth_client):
    """A ticket with no submitted_by (system-generated) must not break the list."""
    Ticket.objects.create(
        title="Member left the server",
        details="Auto-generated cleanup ticket.",
        submitted_by=None,
    )
    response = auth_client.get(reverse("tickets:ticket_list"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Member left the server" in body
    assert "System" in body


@pytest.mark.django_db
def test_ticket_detail_renders_with_system_generated_ticket(auth_client):
    ticket = Ticket.objects.create(
        title="Member left the server",
        details="Auto-generated cleanup ticket.",
        submitted_by=None,
    )
    response = auth_client.get(reverse("tickets:ticket_detail", args=[ticket.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "System" in body

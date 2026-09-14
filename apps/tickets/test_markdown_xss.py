"""A ticket is written by an ordinary rider and read by an admin.

Any team member can file a ticket, and ticket admins -- who may be superusers -- open
it in their own browser. The detail page renders ``details`` and ``resolution`` as
markdown, so before sanitising, a rider could store script that ran with an admin's
session. This pins the real page, not just the filter.
"""

import pytest
from django.urls import reverse
from justhtml import JustHTML

from apps.tickets.models import Ticket

PAYLOAD = (
    "Steps to reproduce:\n\n"
    '<img src=x onerror="alert(1)">\n'
    "<script>alert(document.cookie)</script>\n"
    "[click](javascript:alert(1))\n"
    '<a href="https://example.test" onmouseover="alert(1)">hover</a>\n'
    '<iframe src="https://evil.test"></iframe>\n'
    '<p style="position:fixed">overlay</p>\n'
)


def _rendered_markdown(page_html: str) -> str:
    """Pull just the rendered-markdown blocks out of the page.

    The surrounding layout has its own scripts and inline styles, so asserting
    against the whole page would be meaningless.

    Returns:
        The `.prose` blocks' HTML, lowercased.

    """
    doc = JustHTML(page_html, sanitize=False)
    blocks = [node.to_html(pretty=False) for node in doc.query(".prose")]
    assert blocks, "no rendered-markdown block found on the page"
    return "\n".join(blocks).lower()


@pytest.fixture
def ticket_admin(db, user_model):
    """Build a member who can see the whole ticket queue.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username="xssadmin",
        email="xssadmin@example.test",
        permission_overrides={"team_member": True, "ticket_admin": True},
    )


@pytest.mark.django_db
def test_ticket_detail_renders_rider_markdown_inert(client, ticket_admin, team_member) -> None:
    """A rider's payload reaches the admin's page as inert markup."""
    ticket = Ticket.objects.create(
        title="Cannot upload weight photo",
        details=PAYLOAD,
        resolution=PAYLOAD,
        submitted_by=team_member,
    )
    client.force_login(ticket_admin)

    response = client.get(reverse("tickets:ticket_detail", kwargs={"pk": ticket.pk}))

    # Assert the page actually rendered: on a redirect the body is empty, and every
    # "payload is absent" check below would pass without proving anything.
    assert response.status_code == 200
    body = _rendered_markdown(response.content.decode())

    assert "<script" not in body
    assert "<iframe" not in body
    assert "onerror" not in body
    assert "onmouseover" not in body
    assert "javascript:" not in body
    assert 'style="position:fixed"' not in body
    # The rider's actual words still reach the admin.
    assert "steps to reproduce" in body


@pytest.mark.django_db
def test_ticket_detail_still_renders_ordinary_markdown(client, ticket_admin, team_member) -> None:
    """Sanitising did not cost riders normal formatting."""
    ticket = Ticket.objects.create(
        title="Formatting",
        details="**urgent** please see [the roster](https://example.test/roster)\n\n- one\n- two",
        submitted_by=team_member,
    )
    client.force_login(ticket_admin)

    response = client.get(reverse("tickets:ticket_detail", kwargs={"pk": ticket.pk}))

    assert response.status_code == 200
    body = _rendered_markdown(response.content.decode())

    assert "<strong>urgent</strong>" in body
    assert 'href="https://example.test/roster"' in body
    assert "<li>one</li>" in body

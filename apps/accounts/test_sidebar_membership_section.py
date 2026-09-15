"""The Membership sidebar section must only be offered to people its pages accept.

Every view it links to -- Registrations, Discord Review, Zwift Connections and Team Kit --
is ``@discord_permission_required("membership_admin", raise_exception=True)``, which 403s
rather than redirecting. The section's condition was
``is_membership_admin or is_any_captain``, so a team captain who was not also a membership
admin was shown the whole menu and refused by every link in it.
"""

import pytest
from django.urls import reverse

SECTION_LINKS = (
    "team:application_list",
    "team:discord_review",
    "team:zwift_connections",
)


def _sidebar(client, user):
    """Render a page the sidebar appears on, signed in as ``user``.

    Args:
        client: Test client.
        user: The user to sign in.

    Returns:
        The decoded HTML.

    """
    client.force_login(user)
    return client.get(reverse("team:roster")).content.decode()


@pytest.mark.django_db
def test_a_captain_is_not_offered_the_membership_section(client, user_model):
    """The bug: a captain saw every link in this section and was refused by all of them."""
    captain = user_model.objects.create_user(
        username="captain",
        email="captain@example.test",
        permission_overrides={"team_captain": True, "team_member": True},
    )

    body = _sidebar(client, captain)

    assert ">Membership<" not in body
    for name in SECTION_LINKS:
        assert reverse(name) not in body


@pytest.mark.django_db
def test_a_membership_admin_still_gets_every_link(client, membership_admin):
    """The section must not have been narrowed past the people it is for."""
    body = _sidebar(client, membership_admin)

    assert ">Membership<" in body
    for name in SECTION_LINKS:
        assert reverse(name) in body


@pytest.mark.django_db
def test_every_link_in_the_section_accepts_a_membership_admin(client, membership_admin):
    """What the sidebar offers and what the views allow are the same set."""
    client.force_login(membership_admin)

    for name in SECTION_LINKS:
        assert client.get(reverse(name)).status_code == 200, name

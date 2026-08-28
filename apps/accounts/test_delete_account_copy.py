"""Guards on the delete-account page copy.

The page used to promise "All your data will be permanently deleted", which was false:
several models outlive ``User.delete()`` because their link is ``SET_NULL`` (GuildMember,
Ticket, PageVisit) or because they have no User FK at all (MembershipApplication, and the
zwid-keyed ZwiftPower / Zwift Racing tables). These tests exist so that claim cannot come
back unnoticed.

Verification media used to be on that list -- the rows cascaded but the files stayed in
storage. It is now purged before the cascade, so the page lists it as deleted.
"""

import pytest
from django.urls import reverse


@pytest.fixture
def delete_page(auth_client):
    """Render the delete-account confirmation page as a logged-in member.

    Returns:
        The decoded HTML of the confirmation page.

    """
    response = auth_client.get(reverse("accounts:profile_delete_confirm"))
    assert response.status_code == 200
    return response.content.decode()


def test_page_does_not_claim_everything_is_deleted(delete_page) -> None:
    """The old blanket promise must not reappear."""
    assert "All your data will be permanently deleted" not in delete_page


@pytest.mark.parametrize(
    "survivor",
    [
        "ZwiftPower and Zwift Racing",  # keyed by zwid, no User FK
        "Support tickets",  # Ticket.submitted_by, SET_NULL
        "Page visit logs",  # PageVisit.user, SET_NULL
        "Zwift connection",  # zauth link is not torn down on delete
    ],
)
def test_page_names_what_survives_deletion(delete_page, survivor) -> None:
    """Every category that outlives the account must be disclosed on the page."""
    assert survivor in delete_page


def test_page_does_not_still_claim_media_is_kept(delete_page) -> None:
    """Verification media is purged before the cascade now, so the old caveat must be gone."""
    assert "files themselves stay in storage" not in delete_page


def test_the_records_now_purged_are_listed_as_deleted_not_kept(delete_page) -> None:
    """GuildMember and MembershipApplication used to survive; both are removed now.

    Both strings still appear on the page, so asserting their presence proves nothing --
    what matters is which list they sit in.
    """
    deleted_section = delete_page[
        delete_page.index("What is deleted") : delete_page.index("What we keep")
    ]

    assert "membership registration" in deleted_section
    assert "Discord server membership record" in deleted_section


def test_the_page_warns_the_discord_record_comes_back(delete_page) -> None:
    """Deleting the account does not remove them from the server, so the sync re-creates it."""
    assert "reappears on the next" in delete_page


def test_page_still_asks_for_typed_confirmation(delete_page) -> None:
    """The honest copy must not have cost us the confirmation step."""
    assert 'name="confirmation"' in delete_page
    assert reverse("accounts:profile_delete") in delete_page

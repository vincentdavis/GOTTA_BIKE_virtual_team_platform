"""Guards on the delete-account page copy.

The page used to promise "All your data will be permanently deleted", which was false:
several models outlive ``User.delete()`` because their link is ``SET_NULL`` (GuildMember,
Ticket, PageVisit) or because they have no User FK at all (MembershipApplication, and the
zwid-keyed ZwiftPower / Zwift Racing tables). These tests exist so that claim cannot come
back unnoticed.

Verification media used to be on that list -- the rows cascaded but the files stayed in
storage. It is now purged before the cascade, so the page lists it as deleted.

The Zwift connection made the same journey and is the reason these tests are worth having in
both directions. The page kept saying the link was *not* torn down long after
``delete_user_account`` started calling ``zwift_client.disconnect()`` -- and a test here
asserted the false version, so nothing caught it. A deletion notice understating what it
erases is still a deletion notice that is wrong, and it sent riders looking for a manual
disconnect that would find nothing.
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


def test_the_zwift_connection_is_listed_as_deleted_not_kept(delete_page) -> None:
    """It is torn down on every deletion, so claiming otherwise understates the erasure."""
    deleted_section = delete_page[
        delete_page.index("What is deleted") : delete_page.index("What we keep")
    ]
    assert "Zwift connection" in deleted_section


def test_the_page_no_longer_claims_the_zwift_link_survives(delete_page) -> None:
    """The exact false sentence, and the manual step it sent riders on, must not come back."""
    assert "is not disconnected automatically" not in delete_page
    assert "Disconnect it from" not in delete_page


def test_the_discord_sentence_renders_a_pronoun(delete_page) -> None:
    """The sentence read 'does not remove your from the Discord server' -- a branch emitted the wrong word.

    Collapsed whitespace, because the pronoun sits on its own template line and the words are
    separated by a newline and indentation in the rendered output.
    """
    import re

    flat = re.sub(r"\s+", " ", delete_page)
    assert "does not remove your from" not in flat
    assert "does not remove you from the Discord server" in flat


@pytest.fixture
def admin_delete_page(admin_authed_client, user_model):
    """Render the admin Compliance confirmation, which passes ``subject``.

    The partial is shared, so every pronoun branch has a second rendering that no test
    covered before -- which is how an admin-only wording bug would survive.

    Returns:
        The decoded HTML of the admin confirmation page.

    """
    target = user_model.objects.create_user(username="to_erase", email="erase@example.test")
    response = admin_authed_client.get(reverse("compliance_delete_confirm"), {"user_id": target.pk})
    assert response.status_code == 200
    return response.content.decode()


def test_the_admin_view_never_addresses_the_absent_rider_as_you(admin_delete_page) -> None:
    """An admin reads this about somebody else, so second-person phrasing is simply wrong."""
    body = admin_delete_page[admin_delete_page.index("What is deleted") :]
    for wrong in ("issued to you", "connections you created", "videos of yours", "if you set it up"):
        assert wrong not in body, f"admin view still says {wrong!r}"


def test_the_admin_view_keeps_the_discord_removal_hint(admin_delete_page) -> None:
    """The self-serve wording ('leave the server') makes no sense to an admin."""
    assert "Remove them in Discord" in admin_delete_page

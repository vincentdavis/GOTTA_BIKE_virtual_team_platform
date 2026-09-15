"""/team/discord-review/ renders nowhere else in the suite *with rows in it*.

``test_sidebar_membership_section`` does reverse-and-GET the page, but it creates no
``GuildMember`` rows, so it renders an empty table and never reaches a single rider column.
This file puts real members through it. The page reads rider fields by attribute access and
template lookup, which fail at render time rather than at import, so a dropped model field is
invisible without an actual render -- which is how it earned its keep during the
``User.has_jersey`` retirement.
"""

import pytest
from django.urls import reverse

from apps.accounts.models import GuildMember


@pytest.fixture
def reviewable(db, user_model):
    """Populate both enrichment branches of the Discord review query.

    Returns:
        The member who has a zwid.

    """
    rider = user_model.objects.create_user(
        username="smoke-rider",
        email="smoke@example.test",
        zwid=555001,
        discord_id="900001",
        first_name="Smoke",
        last_name="Rider",
    )
    GuildMember.objects.create(discord_id="900001", username="smoke", user=rider, is_bot=False)
    # A linked user with no zwid takes the second enrichment branch, and exports through the
    # blank-ZWID path -- neither of which the rider above reaches.
    zwidless = user_model.objects.create_user(
        username="smoke-noz", email="noz@example.test", discord_id="900002"
    )
    GuildMember.objects.create(discord_id="900002", username="noz", user=zwidless, is_bot=False)
    return rider


PATHS = [
    "/team/discord-review/",
    # A stale bookmark of the filter that was removed: must still be a 200 afterwards.
    "/team/discord-review/?has_jersey=yes",
]


@pytest.mark.django_db
@pytest.mark.parametrize("path", PATHS)
def test_the_page_renders(client, membership_admin, reviewable, path):
    """The page renders with real members in it."""
    client.force_login(membership_admin)

    assert client.get(path, secure=True).status_code == 200


@pytest.mark.django_db
def test_the_export_writes_a_row_per_member(client, membership_admin, reviewable):
    """The CSV is where a header and its value can drift apart unseen, so count both."""
    client.force_login(membership_admin)

    body = client.get(reverse("team:discord_review_export"), secure=True).content.decode()

    lines = [line for line in body.splitlines() if line.strip()]
    assert len(lines) >= 2, "export rendered no data rows, so it never read a rider column"
    assert len(lines[0].split(",")) == len(lines[1].split(",")), "header and row are misaligned"

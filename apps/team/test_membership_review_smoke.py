"""These pages render nothing in the rest of the suite, so a FieldError here is invisible.

Both read rider columns through ``.values()`` lists that name fields as strings, so dropping a
model field fails at query time rather than at import, and every existing test still passes.
Added while retiring ``User.has_jersey`` -- the guard is the point, not the field.

The fixture is shaped to actually reach the read sites: a rider with a zwid (the main query), a
member with none (the guild-only branch, a separate query), and a ``GuildMember`` for each, or
the Discord review export renders headers and no rows.
"""

import pytest
from django.urls import reverse

from apps.accounts.models import GuildMember


@pytest.fixture
def reviewable(db, user_model):
    """Populate both branches of the membership review join.

    Returns:
        The rider who has a zwid.

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
    # No zwid: reached only through the guild-only query, which is a separate .values() list.
    # It selects on discord_id, so a user without one never enters that branch at all.
    zwidless = user_model.objects.create_user(
        username="smoke-noz", email="noz@example.test", discord_id="900002"
    )
    GuildMember.objects.create(discord_id="900002", username="noz", user=zwidless, is_bot=False)
    return rider


# status=all because the view defaults to active, and an unfiltered fixture rider renders no
# row -- which would let every assertion below pass without touching a single rider column.
PATHS = [
    "/team/membership-review/?status=all&view=member",
    "/team/membership-review/?status=all&view=race",
    # A stale bookmark of the filter and sort being removed: must still be a 200 afterwards.
    "/team/membership-review/?status=all&view=member&sort=jersey&jersey=no",
    "/team/discord-review/",
    "/team/discord-review/?has_jersey=yes",
]


@pytest.mark.django_db
@pytest.mark.parametrize("path", PATHS)
def test_the_page_renders(client, membership_admin, reviewable, path):
    """Each page renders with real riders in it."""
    client.force_login(membership_admin)

    assert client.get(path, secure=True).status_code == 200


@pytest.mark.django_db
def test_the_discord_review_export_writes_a_row_per_member(client, membership_admin, reviewable):
    """The CSV is where a header and its value can drift apart unseen, so count both."""
    client.force_login(membership_admin)

    body = client.get(reverse("team:discord_review_export"), secure=True).content.decode()

    lines = [line for line in body.splitlines() if line.strip()]
    assert len(lines) >= 2, "export rendered no data rows, so it never read a rider column"
    assert len(lines[0].split(",")) == len(lines[1].split(",")), "header and row are misaligned"

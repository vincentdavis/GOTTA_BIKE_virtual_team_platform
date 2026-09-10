"""The team kit config page at /site/config/team_kit/, and the "current" kit.

Each season a new kit is added and made current, and the team works to get everyone into it.
The rules worth pinning are the ones about "current", because they are enforced by the
database rather than by the page: at most one kit is current, and a current kit is always
active. A page can promise both; only a constraint means no other path -- the Django admin,
a shell, a future automation -- can quietly break them.
"""

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.team.kits import current_kit, kit_status_counts
from apps.team.models import KitStatus, TeamKit


@pytest.fixture
def old_kit(db) -> TeamKit:
    """Build last season's kit, current.

    Returns:
        The kit.

    """
    return TeamKit.objects.create(name="2026 Race Kit", slug="race-2026", is_current=True)


@pytest.fixture
def new_kit(db) -> TeamKit:
    """Build this season's kit, not yet current.

    Returns:
        The kit.

    """
    return TeamKit.objects.create(name="2027 Race Kit", slug="race-2027")


def _page(client, viewer):
    """Render the team kit section.

    Args:
        client: Test client.
        viewer: The signed-in user.

    Returns:
        The response.

    """
    client.force_login(viewer)
    return client.get(reverse("config_section_page", args=["team_kit"]))


# --- "current", enforced by the database ------------------------------------------------


@pytest.mark.django_db
def test_only_one_kit_can_be_current(old_kit):
    """Enforced by a partial unique index, so no path can leave two kits current."""
    with pytest.raises(IntegrityError), transaction.atomic():
        TeamKit.objects.create(name="Rogue", slug="rogue", is_current=True)


@pytest.mark.django_db
def test_the_current_kit_cannot_be_retired(old_kit):
    """A retired current kit would be hidden from the very riders it is meant to reach."""
    with pytest.raises(IntegrityError), transaction.atomic():
        TeamKit.objects.filter(pk=old_kit.pk).update(active=False)


@pytest.mark.django_db
def test_making_a_kit_current_moves_the_flag(old_kit, new_kit):
    """The yearly switch: the new kit takes over and the old one stays, just not current."""
    new_kit.make_current()

    old_kit.refresh_from_db()
    new_kit.refresh_from_db()
    assert new_kit.is_current
    assert not old_kit.is_current
    assert old_kit.active  # left as it was -- riders may still be recording it


@pytest.mark.django_db
def test_making_a_retired_kit_current_brings_it_back(old_kit):
    """A current kit must be active, so promoting a retired one activates it."""
    retired = TeamKit.objects.create(name="Throwback", slug="throwback", active=False)

    retired.make_current()

    retired.refresh_from_db()
    assert retired.is_current
    assert retired.active


@pytest.mark.django_db
def test_the_current_kit_leads_every_list(old_kit, new_kit):
    """Ordered at the model, so callers get the kit being chased first without sorting."""
    new_kit.sort_order = 0
    old_kit.sort_order = 99
    new_kit.save()
    old_kit.save()

    assert list(TeamKit.objects.all()) == [old_kit, new_kit]


@pytest.mark.django_db
def test_current_kit_is_the_one_place_to_ask(old_kit, new_kit):
    """What future automations and exports should call, so a new season needs no code change."""
    assert current_kit() == old_kit
    new_kit.make_current()
    assert current_kit() == new_kit


@pytest.mark.django_db
def test_no_current_kit_is_a_valid_state(new_kit):
    """Before one is chosen there simply is none -- not an error."""
    assert current_kit() is None


# --- counts --------------------------------------------------------------------------


def _member(user_model, name: str, team_kit: dict | None = None, **extra):
    """Create a team member -- someone with a Discord login.

    Args:
        user_model: The active user model.
        name: Username, also used for the Discord id and email.
        team_kit: Their kit statuses.
        **extra: Further user fields.

    Returns:
        The member.

    """
    return user_model.objects.create_user(
        username=name, email=f"{name}@example.test", discord_id=f"d-{name}",
        discord_username=name, team_kit=team_kit or {}, **extra,
    )


@pytest.mark.django_db
def test_counts_are_per_kit_and_per_status(user_model, old_kit, new_kit):
    """The progress the page exists to show: how far the team is with each kit."""
    _member(user_model, "r0", {old_kit.slug: KitStatus.HAVE, new_kit.slug: KitStatus.NEED})
    _member(user_model, "r1", {old_kit.slug: KitStatus.HAVE, new_kit.slug: KitStatus.SUBMITTED})
    _member(user_model, "r2", {old_kit.slug: KitStatus.NEED})

    counts = kit_status_counts([old_kit, new_kit])

    assert counts[old_kit.slug] == {"unknown": 0, "need": 1, "submitted": 0, "completed": 0, "have": 2}
    # r2 never answered for the new kit.
    assert counts[new_kit.slug] == {"unknown": 1, "need": 1, "submitted": 1, "completed": 0, "have": 0}


@pytest.mark.django_db
def test_haven_t_answered_counts_members_with_no_answer(user_model, old_kit):
    """Now honest: "team member" has a filterable definition, so the denominator is real."""
    _member(user_model, "answered", {old_kit.slug: KitStatus.HAVE})
    _member(user_model, "silent")
    _member(user_model, "explicit-unknown", {old_kit.slug: KitStatus.UNKNOWN})

    assert kit_status_counts([old_kit])[old_kit.slug]["unknown"] == 2


@pytest.mark.django_db
def test_counts_ignore_junk_and_people_without_a_discord_login(user_model, old_kit):
    """A hand-edited bad value is not an answer, and a non-member is not in the denominator."""
    _member(user_model, "junk", {old_kit.slug: "garbage"})
    user_model.objects.create_user(username="local", email="local@example.test", team_kit={old_kit.slug: "have"})

    counts = kit_status_counts([old_kit])[old_kit.slug]

    assert counts["have"] == 0  # the local account is not a team member
    assert counts["unknown"] == 1  # "junk" counted as unanswered, not as a status


@pytest.mark.django_db
def test_counting_is_one_query_however_many_riders(django_assert_max_num_queries, user_model, old_kit):
    """Rendered on an admin page, it must not become a query per rider."""
    for i in range(25):
        _member(user_model, f"u{i}", {old_kit.slug: "have"})

    with django_assert_max_num_queries(1):
        kit_status_counts([old_kit])


# --- the page ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_page_shows_the_current_kit_and_its_progress(client, app_admin, user_model, old_kit):
    """What the page is for: which kit, and how far along everyone is."""
    _member(user_model, "n", {old_kit.slug: "need"})

    body = _page(client, app_admin).content.decode()

    assert "Current kit" in body
    assert "2026 Race Kit" in body
    assert "1 need it" in body


@pytest.mark.django_db
def test_the_page_warns_when_no_kit_is_current(client, app_admin, new_kit):
    """Kits exist but none is being chased -- worth saying rather than silently showing nothing."""
    body = _page(client, app_admin).content.decode()

    assert "No kit is current" in body


@pytest.mark.django_db
def test_the_page_is_in_the_config_sidebar(client, app_admin):
    """Found where the rest of the configuration lives, not only in the Django admin."""
    body = _page(client, app_admin).content.decode()

    assert reverse("config_section_page", args=["team_kit"]) in body


@pytest.mark.django_db
def test_the_page_is_refused_to_a_plain_team_member(client, team_member):
    """Same gate as the rest of /site/config/."""
    assert _page(client, team_member).status_code == 403


# --- the actions -------------------------------------------------------------------------


@pytest.mark.django_db
def test_adding_a_kit_derives_the_slug_from_the_name(client, app_admin):
    """The slug is permanent, so the easy path should produce a sensible one."""
    client.force_login(app_admin)

    client.post(reverse("team_kit_add"), {"name": "2027 Race Kit", "sort_order": "0"})

    assert TeamKit.objects.filter(slug="2027-race-kit", name="2027 Race Kit").exists()


@pytest.mark.django_db
def test_adding_a_kit_can_make_it_current_in_one_step(client, app_admin, old_kit):
    """The yearly flow: add the new kit and switch to it together."""
    client.force_login(app_admin)

    client.post(reverse("team_kit_add"), {"name": "2027 Race Kit", "sort_order": "0", "make_current": "1"})

    assert current_kit().name == "2027 Race Kit"
    old_kit.refresh_from_db()
    assert not old_kit.is_current


@pytest.mark.django_db
def test_a_duplicate_slug_is_refused_with_a_reason(client, app_admin, old_kit):
    """Two kits sharing a slug would share every rider's status."""
    client.force_login(app_admin)

    response = client.post(
        reverse("team_kit_add"), {"name": "Another", "slug": old_kit.slug, "sort_order": "0"}, follow=True
    )

    assert TeamKit.objects.filter(slug=old_kit.slug).count() == 1
    assert "already exists" in " ".join(str(m) for m in response.context["messages"])


@pytest.mark.django_db
def test_editing_cannot_change_the_slug(client, app_admin, user_model, old_kit):
    """Refused at the form, not just hidden -- a crafted POST must not orphan every rider."""
    rider = user_model.objects.create_user(username="r", email="r@example.test", team_kit={old_kit.slug: "have"})
    client.force_login(app_admin)

    client.post(
        reverse("team_kit_edit", args=[old_kit.pk]),
        {"name": "Renamed", "slug": "hijacked", "description": "", "sort_order": "3"},
    )

    old_kit.refresh_from_db()
    assert old_kit.name == "Renamed"
    assert old_kit.sort_order == 3
    assert old_kit.slug == "race-2026"
    rider.refresh_from_db()
    assert rider.team_kit == {"race-2026": "have"}


@pytest.mark.django_db
def test_make_current_from_the_page(client, app_admin, old_kit, new_kit):
    """The button on each kit that is not already current."""
    client.force_login(app_admin)

    client.post(reverse("team_kit_make_current", args=[new_kit.pk]))

    assert current_kit() == new_kit


@pytest.mark.django_db
def test_a_kit_can_be_retired_and_restored(client, app_admin, new_kit):
    """Retiring hides it from riders; restoring brings it back. Nothing is erased either way."""
    client.force_login(app_admin)

    client.post(reverse("team_kit_toggle_active", args=[new_kit.pk]))
    new_kit.refresh_from_db()
    assert not new_kit.active

    client.post(reverse("team_kit_toggle_active", args=[new_kit.pk]))
    new_kit.refresh_from_db()
    assert new_kit.active


@pytest.mark.django_db
def test_retiring_the_current_kit_is_refused_with_a_reason(client, app_admin, old_kit):
    """Says why, rather than leaving the database to refuse it with a 500."""
    client.force_login(app_admin)

    response = client.post(reverse("team_kit_toggle_active", args=[old_kit.pk]), follow=True)

    old_kit.refresh_from_db()
    assert old_kit.active
    assert "Make another kit current" in " ".join(str(m) for m in response.context["messages"])


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "needs_pk"),
    [
        ("team_kit_add", False),
        ("team_kit_edit", True),
        ("team_kit_make_current", True),
        ("team_kit_toggle_active", True),
    ],
)
def test_every_action_is_refused_to_a_plain_team_member(client, team_member, old_kit, name, needs_pk):
    """Each action applies the config gate itself -- the page hiding the buttons is not enough."""
    client.force_login(team_member)
    url = reverse(name, args=[old_kit.pk]) if needs_pk else reverse(name)

    assert client.post(url, {"name": "x", "sort_order": "0"}).status_code == 403
    old_kit.refresh_from_db()
    assert old_kit.is_current and old_kit.active and old_kit.name == "2026 Race Kit"
    assert not TeamKit.objects.filter(name="x").exists()  # the add case: nothing was created


@pytest.mark.django_db
def test_actions_refuse_get(client, app_admin, old_kit):
    """A link preview or prefetch must never be able to switch the current kit."""
    client.force_login(app_admin)

    assert client.get(reverse("team_kit_make_current", args=[old_kit.pk])).status_code == 405


# --- the team member list ------------------------------------------------------------------


def _list(client, viewer, **params):
    """Render the team kit section and return the response.

    Args:
        client: Test client.
        viewer: The signed-in user.
        **params: Query string, e.g. verified="1".

    Returns:
        The response.

    """
    client.force_login(viewer)
    return client.get(reverse("config_section_page", args=["team_kit"]), params)


def _usernames(response) -> list[str]:
    """Usernames in the rendered member list, in order.

    Args:
        response: The page response.

    Returns:
        The listed usernames.

    """
    return [row["user"].username for row in response.context["member_rows"]]


@pytest.mark.django_db
def test_the_list_is_everyone_with_a_discord_login(client, app_admin, user_model):
    """The definition given for "team member" -- a locally-created account is not one."""
    _member(user_model, "rider")
    user_model.objects.create_user(username="local", email="local@example.test")

    names = _usernames(_list(client, app_admin))

    assert "rider" in names
    assert "local" not in names


@pytest.mark.django_db
def test_the_verified_filter_narrows_the_list(client, app_admin, user_model):
    """The optional filter: verified Zwift accounts only."""
    _member(user_model, "verified", zwid=111, zwid_verified=True)
    _member(user_model, "unverified", zwid=222, zwid_verified=False)

    assert set(_usernames(_list(client, app_admin))) >= {"verified", "unverified"}
    filtered = _usernames(_list(client, app_admin, verified="1"))
    assert "verified" in filtered
    assert "unverified" not in filtered


@pytest.mark.django_db
def test_the_filter_does_not_change_the_counts(client, app_admin, user_model, old_kit):
    """The filter narrows the list only -- "N need it" must mean the same thing either way."""
    _member(user_model, "v", {old_kit.slug: "need"}, zwid=1, zwid_verified=True)
    _member(user_model, "u", {old_kit.slug: "need"}, zwid=2, zwid_verified=False)

    unfiltered = _list(client, app_admin).context["current_kit_entry"]["counts"]
    filtered = _list(client, app_admin, verified="1").context["current_kit_entry"]["counts"]

    assert unfiltered == filtered
    assert filtered["need"] == 2


@pytest.mark.django_db
def test_a_filtered_list_says_how_much_it_is_showing(client, app_admin, user_model):
    """Otherwise a short list reads as a small team rather than a filtered view."""
    _member(user_model, "v", zwid=1, zwid_verified=True)
    _member(user_model, "u1", zwid=2, zwid_verified=False)
    _member(user_model, "u2", zwid=3, zwid_verified=False)

    body = _list(client, app_admin, verified="1").content.decode()

    assert "Showing 1 of 3 members" in body


@pytest.mark.django_db
def test_the_columns(client, app_admin, user_model, old_kit):
    """Discord name, Zwift name, Zwift ID, Zwift verified, and the current kit's status."""
    from apps.zwiftpower.models import ZPTeamRiders

    _member(
        user_model, "ana", {old_kit.slug: "submitted"},
        discord_nickname="Ana R", zwid=6164399, zwid_verified=True,
    )
    ZPTeamRiders.objects.create(zwid=6164399, name="Ana Rider [COALITION]")

    body = _list(client, app_admin).content.decode()
    table = body[body.index("<th>Discord name</th>"):]

    assert "Ana R" in table
    assert "@ana" in table  # the username, since it differs from the nickname
    assert "Ana Rider [COALITION]" in table
    assert "6164399" in table
    assert "Verified" in table
    assert "Submitted to Zwift" in table
    assert "2026 Race Kit" in table  # the status column is headed with the current kit's name


@pytest.mark.django_db
def test_the_zwift_name_follows_the_roster(user_model, old_kit):
    """ZwiftPower first, then ZwiftRacing -- the same order as the team roster, so they agree."""
    from apps.team.kits import kit_member_rows
    from apps.zwiftpower.models import ZPTeamRiders
    from apps.zwiftracing.models import ZRRider

    _member(user_model, "both", zwid=1)
    _member(user_model, "zr-only", zwid=2)
    ZPTeamRiders.objects.create(zwid=1, name="ZP Name")
    ZRRider.objects.create(zwid=1, name="ZR Name")
    ZRRider.objects.create(zwid=2, name="Only ZR")

    names = {row["user"].username: row["zwift_name"] for row in kit_member_rows(kit=old_kit)}

    assert names["both"] == "ZP Name"
    assert names["zr-only"] == "Only ZR"


@pytest.mark.django_db
def test_no_current_kit_says_so_in_the_status_column(client, app_admin, user_model, new_kit):
    """There is no kit to report on; the column must not invent a status."""
    _member(user_model, "rider")

    body = _list(client, app_admin).content.decode()

    assert "No current kit" in body


@pytest.mark.django_db
def test_the_list_is_sorted_by_discord_name(client, app_admin, user_model):
    """Predictable order, case-insensitive."""
    _member(user_model, "zed", discord_nickname="zed")
    _member(user_model, "amy", discord_nickname="Amy")
    _member(user_model, "bob", discord_nickname="bob")

    assert _usernames(_list(client, app_admin))[:3] == ["amy", "bob", "zed"]


@pytest.mark.django_db
def test_the_list_costs_the_same_however_many_members(user_model, old_kit):
    """A fixed handful of queries, never one per rider -- the lesson of the captain banner.

    Warmed first, and asserted as equality: an unwarmed baseline once hid a per-rider query
    inside the slack of a <= comparison.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.team.kits import kit_member_rows
    from apps.zwiftpower.models import ZPTeamRiders

    for i in range(2):
        _member(user_model, f"few{i}", zwid=100 + i)
        ZPTeamRiders.objects.create(zwid=100 + i, name=f"Few {i}")
    kit_member_rows(kit=old_kit)  # warm
    with CaptureQueriesContext(connection) as few:
        kit_member_rows(kit=old_kit)

    for i in range(20):
        _member(user_model, f"many{i}", zwid=200 + i)
        ZPTeamRiders.objects.create(zwid=200 + i, name=f"Many {i}")
    with CaptureQueriesContext(connection) as many:
        kit_member_rows(kit=old_kit)

    assert len(many.captured_queries) == len(few.captured_queries)

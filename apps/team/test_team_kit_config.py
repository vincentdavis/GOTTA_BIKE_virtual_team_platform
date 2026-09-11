"""The team kit config page at /site/config/team_kit/, and the "current" kit.

Each season a new kit is added and made current, and the team works to get everyone into it.
The rules worth pinning are the ones about "current", because they are enforced by the
database rather than by the page: at most one kit is current, and a current kit is always
active. A page can promise both; only a constraint means no other path -- the Django admin,
a shell, a future automation -- can quietly break them.
"""

from unittest import mock

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
        username=name,
        email=f"{name}@example.test",
        discord_id=f"d-{name}",
        discord_username=name,
        team_kit=team_kit or {},
        **extra,
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


def _add_dialog(body: str) -> str:
    """Cut the Add a kit dialog out of the page.

    Args:
        body: The rendered page.

    Returns:
        The dialog's HTML, from its opening tag to its close.

    """
    start = body.index('<dialog id="kit-add-dialog"')
    return body[start : body.index("</dialog>", start)]


@pytest.mark.django_db
def test_adding_a_kit_waits_in_a_closed_dialog(client, app_admin, old_kit):
    """The form is not on the page until asked for -- adding a kit is a once-a-season job."""
    body = _page(client, app_admin).content.decode()
    dialog = _add_dialog(body)

    assert "<dialog" in dialog and " open" not in dialog.split(">", 1)[0]
    assert f'action="{reverse("team_kit_add")}"' in dialog
    # The only way to add a kit is through the dialog.
    assert body.count(f'action="{reverse("team_kit_add")}"') == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("dialog_id", "submit_label"), [("kit-add-dialog", "Add kit"), ("kit-import-dialog", "Preview import")]
)
def test_cancelling_a_dialog_never_submits_it(client, app_admin, old_kit, dialog_id, submit_label):
    """Cancel closes the dialog without posting it.

    As a submit button it would add the kit anyway -- and, with "Make this the current kit"
    pre-ticked, make it the kit riders are asked about.
    """
    import re

    body = _page(client, app_admin).content.decode()
    start = body.index(f'<dialog id="{dialog_id}"')
    dialog = body[start : body.index("</dialog>", start)]
    post_form = dialog[dialog.index('<form method="post"') : dialog.index("</form>")]

    # A form nested in the POST form is dropped by the HTML parser, and its button with it.
    assert "<form" not in post_form[1:]
    buttons = re.findall(r"<button([^>]*)>\s*([^<]*?)\s*</button>", post_form)
    assert [label for attrs, label in buttons if 'type="submit"' in attrs] == [submit_label]
    assert all('type="button"' in attrs for attrs, label in buttons if label != submit_label)
    assert any(label == "Cancel" for _, label in buttons)


@pytest.mark.django_db
def test_every_filter_box_applies_itself(client, app_admin, old_kit):
    """The filter form has no button: a box that does not submit on change would tick and do nothing."""
    import re

    body = _page(client, app_admin).content.decode()
    form = body[body.index('<form method="get"') : body.index("</form>", body.index('<form method="get"'))]
    boxes = re.findall(r'<input type="checkbox" name="(\w+)"[^>]*>', form)

    assert sorted(boxes) == sorted(["verified", "race_verified", *["status"] * len(KitStatus.values)])
    for box in re.findall(r'<input type="checkbox" name="\w+"[^>]*>', form):
        assert 'onchange="this.form.submit()"' in box, box


@pytest.mark.django_db
@pytest.mark.parametrize("have_kits", [True, False], ids=["beside-all-kits", "in-the-empty-state"])
def test_the_add_button_opens_the_dialog(client, app_admin, have_kits):
    """Beside "All kits" normally; in the "No kits yet" note when there is nothing else to show."""
    if have_kits:
        TeamKit.objects.create(name="2026 Race Kit", slug="race-2026", is_current=True)
    body = " ".join(_page(client, app_admin).content.decode().split())
    button = "onclick=\"document.getElementById('kit-add-dialog').showModal()\">Add a kit</button>"

    assert body.count(button) == 1
    heading = body.index("All kits") if have_kits else body.index("No kits yet")
    assert heading < body.index(button) < heading + 400


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("setup", "ticked"),
    [("none", True), ("none-current", True), ("current", False)],
    ids=["no-kits", "kits-but-none-current", "a-kit-is-current"],
)
def test_make_current_starts_ticked_only_without_a_current_kit(client, app_admin, setup, ticked):
    """With no current kit the new one is almost always it; otherwise the choice stays deliberate."""
    if setup != "none":
        TeamKit.objects.create(name="2026 Race Kit", slug="race-2026", is_current=setup == "current")
    dialog = " ".join(_add_dialog(_page(client, app_admin).content.decode()).split())

    box = dialog[dialog.index('name="make_current"') : dialog.index(">", dialog.index('name="make_current"'))]
    assert ("checked" in box) is ticked


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

# A member verified the way the kit page counts: through zauth.
ZAUTH_VERIFIED = {"zwid_verified": True, "zwid_verification_method": "zauth"}


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


# Every stored state a member's Zwift verification can be in, as (zwid_verified, method), with
# whether the kit page counts it as verified and the badge its column shows.
VERIFICATION_STATES = [
    pytest.param(True, "zauth", True, "Zauth", id="zauth"),
    pytest.param(True, "legacy", False, "Legacy (Sauce mod)", id="legacy"),
    pytest.param(True, "admin", False, "Admin (manual)", id="admin"),
    pytest.param(True, "", False, "Other", id="verified-no-method"),
    # What unverify_zwift leaves behind: the method stays "zauth", the verification is gone.
    pytest.param(False, "zauth", False, "Not verified", id="zauth-method-left-behind"),
    pytest.param(False, "", False, "Not verified", id="never-verified"),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("verified", "method", "counts", "badge"), VERIFICATION_STATES)
def test_verified_means_verified_through_zauth(client, app_admin, user_model, verified, method, counts, badge):
    """The filter is zauth only -- not the legacy Sauce-mod or admin verifications."""
    _member(user_model, "rider", zwid=111, zwid_verified=verified, zwid_verification_method=method)

    assert "rider" in _usernames(_list(client, app_admin))
    assert ("rider" in _usernames(_list(client, app_admin, verified="1"))) is counts


@pytest.mark.django_db
@pytest.mark.parametrize(("verified", "method", "counts", "badge"), VERIFICATION_STATES)
def test_the_verified_column_shows_how(client, app_admin, user_model, verified, method, counts, badge):
    """Zauth, the older method by name, or not verified -- so a legacy rider is visibly one."""
    _member(user_model, "rider", zwid=111, zwid_verified=verified, zwid_verification_method=method)

    body = _list(client, app_admin).content.decode()
    cell = body[body.index("<th>Discord name</th>") :]

    assert f">{badge}</span>" in cell
    for other in {"Zauth", "Legacy (Sauce mod)", "Admin (manual)", "Other", "Not verified"} - {badge}:
        assert f">{other}</span>" not in cell


@pytest.mark.django_db
def test_the_page_never_asks_the_zauth_service(client, app_admin, user_model, old_kit):
    """Verification is read from what the platform stored, never the live connection."""
    from apps.zwift import client as zwift_client

    _member(user_model, "rider", zwid=111, zwid_verified=True, zwid_verification_method="zauth")
    # Every function in the zauth client that talks to the service.
    service_calls = (
        "get_connection_status",
        "get_authorize_url",
        "get_racing_profile",
        "get_profile_stats",
        "get_activity_stats",
        "list_connections",
        "disconnect",
    )
    calls = dict.fromkeys(service_calls, mock.DEFAULT)
    with mock.patch.multiple(zwift_client, **calls) as patched:
        for name in calls:
            patched[name].side_effect = AssertionError(f"the team kit page called {name}")
        assert _usernames(_list(client, app_admin, verified="1")) == ["rider"]
        assert client.get(reverse("team_kit_export"), {"verified": "1"}).status_code == 200


def test_the_zauth_literal_matches_the_model():
    """kits.py spells it out rather than importing User at load time; the two must not drift."""
    from apps.accounts.models import User
    from apps.team.kits import ZAUTH

    assert ZAUTH == User.VerificationMethod.ZAUTH


# --- the race verified filter --------------------------------------------------------------


@pytest.mark.django_db
def test_the_race_verified_filter_narrows_the_list(client, app_admin, user_model):
    """Race Verified only -- the status the rest of the app calls Race Verified."""
    _member(user_model, "ready", is_race_ready=True)
    _member(user_model, "not-ready", is_race_ready=False)

    assert set(_usernames(_list(client, app_admin))) >= {"ready", "not-ready"}
    assert _usernames(_list(client, app_admin, race_verified="1")) == ["ready"]


@pytest.mark.django_db
def test_race_verified_is_the_stored_status_not_recalculated(client, app_admin, user_model):
    """The cached is_race_ready the roster also reads -- recalculating would query per rider."""
    from apps.accounts.models import User

    _member(user_model, "ready", is_race_ready=True)  # no verification records at all
    with mock.patch.object(User, "calculate_race_ready", side_effect=AssertionError("recalculated")):
        assert _usernames(_list(client, app_admin, race_verified="1")) == ["ready"]


@pytest.mark.django_db
def test_only_race_verified_1_turns_it_on(rf):
    """The same strictness as the other boxes: the page only ever sends "1"."""
    from apps.team.kits import member_filters

    assert member_filters(rf.get("/", {"race_verified": "1"}).GET, None).race_verified_only is True
    for value in ("yes", "true", "0", ""):
        assert member_filters(rf.get("/", {"race_verified": value}).GET, None).race_verified_only is False


@pytest.mark.django_db
def test_all_three_filters_combine(client, app_admin, user_model, old_kit):
    """Every box on means every condition at once."""
    need = {old_kit.slug: KitStatus.NEED}
    _member(user_model, "all-three", need, zwid=1, is_race_ready=True, **ZAUTH_VERIFIED)
    _member(user_model, "not-race", need, zwid=2, is_race_ready=False, **ZAUTH_VERIFIED)
    _member(
        user_model, "legacy", need, zwid=3, is_race_ready=True, zwid_verified=True, zwid_verification_method="legacy"
    )
    _member(user_model, "has-it", {old_kit.slug: KitStatus.HAVE}, zwid=4, is_race_ready=True, **ZAUTH_VERIFIED)

    assert _usernames(_list(client, app_admin, verified="1", race_verified="1", status="need")) == ["all-three"]


@pytest.mark.django_db
def test_the_race_filter_does_not_change_the_counts(client, app_admin, user_model, old_kit):
    """Like the others, it narrows the list and never the summary above it."""
    _member(user_model, "r", {old_kit.slug: "need"}, is_race_ready=True)
    _member(user_model, "n", {old_kit.slug: "need"}, is_race_ready=False)

    unfiltered = _list(client, app_admin).context["current_kit_entry"]["counts"]
    filtered = _list(client, app_admin, race_verified="1").context["current_kit_entry"]["counts"]

    assert unfiltered == filtered
    assert filtered["need"] == 2


@pytest.mark.parametrize(
    ("filters", "phrase"),
    [
        ({"verified_only": True}, "verified with zauth"),
        ({"race_verified_only": True}, "race verified"),
        ({"statuses": ("need",)}, "whose 2026 Race Kit status is Need kit"),
        ({"statuses": ("need", "submitted")}, "whose 2026 Race Kit status is Need kit or Submitted to Zwift"),
        (
            {"statuses": ("unknown", "need", "have")},
            "whose 2026 Race Kit status is Unknown, Need kit or I have the kit",
        ),
        ({"verified_only": True, "race_verified_only": True}, "verified with zauth and race verified"),
        ({"verified_only": True, "statuses": ("need",)}, "verified with zauth, whose 2026 Race Kit status is Need kit"),
        (
            {"race_verified_only": True, "statuses": ("completed",)},
            "race verified, whose 2026 Race Kit status is Completed by Zwift",
        ),
        (
            {"verified_only": True, "race_verified_only": True, "statuses": ("need", "submitted")},
            "verified with zauth and race verified, whose 2026 Race Kit status is Need kit or Submitted to Zwift",
        ),
        ({}, ""),
    ],
)
def test_the_summary_says_which_filters_are_on(filters, phrase):
    """Every combination reads as a sentence, so a short list is never mistaken for a small team."""
    from apps.team.kits import MemberFilters

    assert MemberFilters(**filters).describe("2026 Race Kit") == phrase


@pytest.mark.django_db
def test_the_race_filter_is_on_the_page_and_in_the_export_link(client, app_admin, user_model, old_kit):
    """Ticked when on, named in the summary, and carried to Export CSV."""
    _member(user_model, "ready", is_race_ready=True)
    _member(user_model, "not-ready")

    body = " ".join(_list(client, app_admin, race_verified="1").content.decode().split())

    assert 'name="race_verified" value="1" class="checkbox checkbox-sm checkbox-primary" checked' in body
    assert "Showing 1 of 2 members &mdash; race verified." in body
    assert f'href="{reverse("team_kit_export")}?race_verified=1"' in body


def _ticked(response) -> set[tuple[str, str]]:
    """Read which filter boxes the page shows ticked.

    Args:
        response: The page response.

    Returns:
        ``(name, value)`` for every ticked box in the filter form.

    """
    import re

    body = response.content.decode()
    form = body[body.index('<form method="get"') :]
    form = form[: form.index("</form>")]
    return set(re.findall(r'name="(\w+)" value="(\w+)"[^>]*\bchecked\b', form))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "on",
    [
        (),
        (("verified", "1"),),
        (("race_verified", "1"),),
        (("status", "need"),),
        (("status", "need"), ("status", "submitted")),
        (("verified", "1"), ("status", "unknown")),
        (("race_verified", "1"), ("status", "completed"), ("status", "have")),
        (("verified", "1"), ("race_verified", "1"), ("status", "need")),
        tuple(("status", status) for status in KitStatus.values),
    ],
    ids=lambda on: "+".join(f"{name}={value}" for name, value in on) or "none",
)
def test_each_box_is_ticked_exactly_when_its_filter_is_on(client, app_admin, old_kit, on):
    """Each box shows its own filter, and no other.

    The form submits on change, and an unticked box sends nothing -- so a box shown unticked
    while its filter is on would quietly drop that filter the next time any box is clicked.
    """
    params: dict[str, list[str]] = {}
    for name, value in on:
        params.setdefault(name, []).append(value)

    assert _ticked(_list(client, app_admin, **params)) == set(on)


@pytest.mark.django_db
def test_the_filter_does_not_change_the_counts(client, app_admin, user_model, old_kit):
    """The filter narrows the list only -- "N need it" must mean the same thing either way."""
    _member(user_model, "v", {old_kit.slug: "need"}, zwid=1, **ZAUTH_VERIFIED)
    _member(user_model, "u", {old_kit.slug: "need"}, zwid=2, zwid_verified=False)

    unfiltered = _list(client, app_admin).context["current_kit_entry"]["counts"]
    filtered = _list(client, app_admin, verified="1").context["current_kit_entry"]["counts"]

    assert unfiltered == filtered
    assert filtered["need"] == 2


@pytest.mark.django_db
def test_a_filtered_list_says_how_much_it_is_showing(client, app_admin, user_model):
    """Otherwise a short list reads as a small team rather than a filtered view."""
    _member(user_model, "v", zwid=1, **ZAUTH_VERIFIED)
    _member(user_model, "u1", zwid=2, zwid_verified=False)
    _member(user_model, "u2", zwid=3, zwid_verified=True, zwid_verification_method="legacy")

    body = _list(client, app_admin, verified="1").content.decode()

    assert "Showing 1 of 3 members" in body


@pytest.mark.django_db
def test_the_columns(client, app_admin, user_model, old_kit):
    """Discord name, Zwift name, Zwift ID, Zwift verified, and the current kit's status."""
    from apps.zwiftpower.models import ZPTeamRiders

    _member(
        user_model,
        "ana",
        {old_kit.slug: "submitted"},
        discord_nickname="Ana R",
        zwid=6164399,
        **ZAUTH_VERIFIED,
    )
    ZPTeamRiders.objects.create(zwid=6164399, name="Ana Rider [COALITION]")

    body = _list(client, app_admin).content.decode()
    table = body[body.index("<th>Discord name</th>") :]

    assert "Ana R" in table
    assert "@ana" in table  # the username, since it differs from the nickname
    assert "Ana Rider [COALITION]" in table
    assert "6164399" in table
    assert ">Zauth</span>" in table
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
@pytest.mark.parametrize(
    "filters",
    [{}, {"verified_only": True}, {"race_verified_only": True}, {"statuses": ("need",)}],
    ids=["unfiltered", "zauth", "race", "status"],
)
def test_the_list_costs_the_same_however_many_members(user_model, old_kit, filters):
    """A fixed handful of queries, never one per rider -- the lesson of the captain banner.

    Warmed first, and asserted as equality: an unwarmed baseline once hid a per-rider query
    inside the slack of a <= comparison. Run under each filter, since each could add one.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.team.kits import kit_member_rows
    from apps.zwiftpower.models import ZPTeamRiders

    # Verified, and by varied methods, so every row reads zwid_verification_method -- left
    # out of the query's field list, that read would cost a query per rider.
    methods = ["zauth", "legacy", "admin", ""]
    kit_need = {old_kit.slug: KitStatus.NEED}

    def make(prefix: str, count: int, zwid_base: int) -> None:
        for i in range(count):
            _member(
                user_model,
                f"{prefix}{i}",
                kit_need,
                zwid=zwid_base + i,
                zwid_verified=True,
                zwid_verification_method=methods[i % 4],
                is_race_ready=True,
            )
            ZPTeamRiders.objects.create(zwid=zwid_base + i, name=f"{prefix} {i}")

    make("few", 4, 100)
    kit_member_rows(kit=old_kit, **filters)  # warm
    with CaptureQueriesContext(connection) as few:
        rows_few = kit_member_rows(kit=old_kit, **filters)

    make("many", 20, 200)
    with CaptureQueriesContext(connection) as many:
        rows_many = kit_member_rows(kit=old_kit, **filters)

    assert len(rows_many) > len(rows_few) > 0, "the filter left nothing to measure"
    assert len(many.captured_queries) == len(few.captured_queries)


# --- the kit status filter -------------------------------------------------------------------


@pytest.mark.django_db
def test_several_statuses_combine_as_any_of(client, app_admin, user_model, old_kit):
    """Tick several and a member shows if their current-kit status is any one of them."""
    for status in KitStatus.values:
        _member(user_model, status, {old_kit.slug: status})
    _member(user_model, "never-answered")
    _member(user_model, "junk", {old_kit.slug: "not-a-status"})  # a hand edit; reads as unknown

    assert _usernames(_list(client, app_admin, status=["need", "submitted"])) == ["need", "submitted"]
    assert _usernames(_list(client, app_admin, status=["completed", "have"])) == ["completed", "have"]
    # Unknown is everyone without an answer: no entry, a junk one, or one set to unknown.
    assert _usernames(_list(client, app_admin, status="unknown")) == ["junk", "never-answered", "unknown"]
    # Every status ticked leaves everyone in.
    assert len(_usernames(_list(client, app_admin, status=list(KitStatus.values)))) == len(KitStatus.values) + 2


@pytest.mark.django_db
def test_a_value_that_is_not_a_status_is_ignored(client, app_admin, user_model, old_kit):
    """Rather than matching nobody and emptying the list."""
    _member(user_model, "needs", {old_kit.slug: KitStatus.NEED})
    _member(user_model, "has", {old_kit.slug: KitStatus.HAVE})

    alone = _list(client, app_admin, status="bogus")
    assert _usernames(alone) == ["has", "needs"]
    assert alone.context["member_list_filtered"] is False
    assert _usernames(_list(client, app_admin, status=["bogus", "need"])) == ["needs"]


@pytest.mark.django_db
def test_statuses_are_read_in_the_teams_order_once_each(rf, old_kit):
    """So the export link, its filename and the summary come out the same for the same choice."""
    from apps.team.kits import member_filters

    params = rf.get("/", {"status": ["have", "need", "need", "unknown"]}).GET

    assert member_filters(params, old_kit).statuses == ("unknown", "need", "have")


@pytest.mark.django_db
def test_old_need_links_still_mean_need_kit(client, app_admin, user_model, old_kit):
    """?need=1 was this filter's only option; bookmarks and shared links keep working."""
    _member(user_model, "needs", {old_kit.slug: KitStatus.NEED})
    _member(user_model, "submitted", {old_kit.slug: KitStatus.SUBMITTED})

    response = _list(client, app_admin, need="1")
    assert _usernames(response) == ["needs"]
    assert _ticked(response) == {("status", "need")}
    assert f'href="{reverse("team_kit_export")}?status=need"' in response.content.decode()
    # Alongside the new form, it adds Need kit to whatever else is ticked.
    assert _usernames(_list(client, app_admin, need="1", status="submitted")) == ["needs", "submitted"]


@pytest.mark.django_db
def test_the_status_boxes_wear_the_status_columns_badges(client, app_admin, old_kit):
    """What you tick looks like what the list shows, one box per status."""
    import re

    from apps.team.kits import BADGE_CLASSES

    body = _list(client, app_admin).content.decode()
    form = body[body.index('<form method="get"') : body.index("</form>", body.index('<form method="get"'))]
    boxes = re.findall(
        r'name="status" value="(\w+)"[^>]*>\s*<span class="badge ([\w-]+) badge-sm[^"]*">([^<]+)</span>', form
    )

    assert boxes == [(status.value, BADGE_CLASSES[status.value], status.label) for status in KitStatus]
    assert f'id="kit-status-filter-label" class="label-text">{old_kit.name} status</p>' in form


@pytest.mark.django_db
def test_the_need_filter_shows_only_members_who_need_the_current_kit(client, app_admin, user_model, old_kit):
    """Exactly the "Need kit" status -- not those already submitted, completed or equipped."""
    _member(user_model, "needs", {old_kit.slug: KitStatus.NEED})
    _member(user_model, "submitted", {old_kit.slug: KitStatus.SUBMITTED})
    _member(user_model, "completed", {old_kit.slug: KitStatus.COMPLETED})
    _member(user_model, "has", {old_kit.slug: KitStatus.HAVE})
    _member(user_model, "silent")

    assert _usernames(_list(client, app_admin, status="need")) == ["needs"]


@pytest.mark.django_db
def test_need_means_the_current_kit_not_any_kit(client, app_admin, user_model, old_kit, new_kit):
    """Needing last season's kit is not needing this one -- the filter follows "current"."""
    _member(user_model, "wants-old", {new_kit.slug: KitStatus.NEED, old_kit.slug: KitStatus.HAVE})
    _member(user_model, "wants-current", {old_kit.slug: KitStatus.NEED})

    assert _usernames(_list(client, app_admin, status="need")) == ["wants-current"]


@pytest.mark.django_db
def test_the_two_filters_combine(client, app_admin, user_model, old_kit):
    """Both boxes on means both conditions: verified AND needs the kit."""
    _member(user_model, "verified-needs", {old_kit.slug: KitStatus.NEED}, zwid=1, **ZAUTH_VERIFIED)
    _member(user_model, "unverified-needs", {old_kit.slug: KitStatus.NEED}, zwid=2, zwid_verified=False)
    _member(
        user_model,
        "legacy-needs",
        {old_kit.slug: KitStatus.NEED},
        zwid=4,
        zwid_verified=True,
        zwid_verification_method="legacy",
    )
    _member(user_model, "verified-has", {old_kit.slug: KitStatus.HAVE}, zwid=3, **ZAUTH_VERIFIED)

    assert _usernames(_list(client, app_admin, status="need", verified="1")) == ["verified-needs"]


@pytest.mark.django_db
def test_the_need_filter_does_not_change_the_counts(client, app_admin, user_model, old_kit):
    """Same rule as the verified filter: it narrows the list, never the summary."""
    _member(user_model, "a", {old_kit.slug: KitStatus.NEED})
    _member(user_model, "b", {old_kit.slug: KitStatus.HAVE})

    unfiltered = _list(client, app_admin).context["current_kit_entry"]["counts"]
    filtered = _list(client, app_admin, status="need").context["current_kit_entry"]["counts"]

    assert unfiltered == filtered


@pytest.mark.django_db
def test_the_summary_names_what_is_being_shown(client, app_admin, user_model, old_kit):
    """A short list must read as filtered, and say by what."""
    _member(user_model, "a", {old_kit.slug: KitStatus.NEED}, zwid=1, **ZAUTH_VERIFIED)
    _member(user_model, "b", {old_kit.slug: KitStatus.HAVE}, zwid=2, **ZAUTH_VERIFIED)

    def summary(**params) -> str:
        return " ".join(_list(client, app_admin, **params).content.decode().split())

    # &mdash; because this is the raw HTML, where the dash is an entity.
    assert "Showing 1 of 2 members &mdash; whose 2026 Race Kit status is Need kit." in summary(status="need")
    assert "Showing 2 of 2 members &mdash; verified with zauth." in summary(verified="1")
    assert "Showing 1 of 2 members &mdash; verified with zauth, whose 2026 Race Kit status is Need kit." in summary(
        status="need", verified="1"
    )
    assert "Showing 2 of 2 members &mdash; whose 2026 Race Kit status is Need kit or I have the kit." in summary(
        status=["need", "have"]
    )


@pytest.mark.django_db
def test_without_a_current_kit_the_box_is_disabled(client, app_admin, user_model, new_kit):
    """There is nothing to need until a kit is current."""
    _member(user_model, "rider")

    body = _list(client, app_admin).content.decode()

    assert 'name="status"' not in body
    assert "Make a kit current first" in body


@pytest.mark.django_db
@pytest.mark.parametrize("params", [{"status": "need"}, {"status": ["need", "have"]}, {"need": "1"}])
def test_a_hand_typed_status_without_a_current_kit_is_ignored(client, app_admin, user_model, new_kit, params):
    """Rather than emptying the list for a reason nothing on the page explains."""
    _member(user_model, "rider")

    response = _list(client, app_admin, **params)

    assert "rider" in _usernames(response)
    assert response.context["status_filter"] == ()


# --- membership admins -------------------------------------------------------------------
#
# Getting kits to riders is membership work, so membership admins can use this page. The
# thing that must NOT happen is that opening one section opens the rest of /site/config/,
# which holds the Discord bot token, API credentials and the permission mappings. The gate
# is repeated across six views, which is exactly how a widening meant for one lands on
# another -- hence the explicit refusals below.


@pytest.mark.django_db
def test_a_membership_admin_can_open_the_team_kit_page(client, membership_admin, old_kit):
    """The request: PERM_MEMBERSHIP_ADMIN_ROLES should have access."""
    assert _page(client, membership_admin).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "needs_pk", "data"),
    [
        ("team_kit_add", False, {"name": "2027 Race Kit", "sort_order": "0"}),
        ("team_kit_edit", True, {"name": "Renamed", "description": "", "sort_order": "1"}),
        ("team_kit_make_current", True, {}),
        ("team_kit_toggle_active", True, {}),
    ],
)
def test_a_membership_admin_can_use_every_action(client, membership_admin, old_kit, new_kit, name, needs_pk, data):
    """Access to the page is useless without access to what it posts to."""
    client.force_login(membership_admin)
    url = reverse(name, args=[new_kit.pk]) if needs_pk else reverse(name)

    response = client.post(url, data)

    assert response.status_code == 302  # redirected back with a message, not refused


@pytest.mark.django_db
@pytest.mark.parametrize(
    "section",
    ["permission_mappings", "zwift_credentials", "discord_guild", "compliance", "background_tasks", "site_images"],
)
def test_a_membership_admin_cannot_open_any_other_config_section(client, membership_admin, section):
    """One section opened, not all of them -- these hold credentials and permission mappings."""
    client.force_login(membership_admin)

    assert client.get(reverse("config_section_page", args=[section])).status_code == 403


@pytest.mark.django_db
def test_a_membership_admin_cannot_save_other_config_settings(client, membership_admin):
    """The section page is not the only door: its settings form posts somewhere else."""
    client.force_login(membership_admin)

    response = client.post(reverse("config_section_update", args=["permission_mappings"]), {})

    assert response.status_code == 403


@pytest.mark.django_db
def test_a_membership_admin_finds_the_page_among_their_own_tools(client, membership_admin, old_kit):
    """They never see the Configuration menu, so the link has to be in the Membership one."""
    body = _page(client, membership_admin).content.decode()

    assert reverse("config_section_page", args=["team_kit"]) in body
    # The Configuration menu, and so the links to the credential-bearing sections, stays hidden.
    assert reverse("config_section_page", args=["permission_mappings"]) not in body
    assert reverse("config_section_page", args=["zwift_credentials"]) not in body


@pytest.mark.django_db
def test_the_rule_refuses_an_anonymous_user():
    """Checked before any permission attribute, which an anonymous user does not have."""
    from django.contrib.auth.models import AnonymousUser

    from apps.team.kits import can_manage_team_kit

    assert can_manage_team_kit(AnonymousUser()) is False

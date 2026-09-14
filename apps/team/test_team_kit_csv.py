"""CSV export and import on the team kit page (/site/config/team_kit/).

The import is a bulk write to every rider's kit status, so most of what is pinned here is what
it must NOT do: write anything at preview time, touch a cell left blank, touch a kit the file
does not mention, guess which member an ambiguous row means, half-apply a bad row, apply a file
other than the one previewed, or overwrite a status that moved after the preview.
"""

import csv
import io
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.accounts.permission_registry import PERMISSION_REGISTRY
from apps.team.kit_csv import INFO_COLUMNS, MAX_IMPORT_BYTES, SESSION_KEY, parse_status
from apps.team.models import KitStatus, TeamKit

EXPORT_HEADER = [*INFO_COLUMNS, "kit:race-2026", "kit:race-2025", "exported_statuses"]
# Looked up by name, so a column added to the export cannot silently shift what a test edits.
KIT_2026 = EXPORT_HEADER.index("kit:race-2026")
SNAPSHOT = EXPORT_HEADER.index("exported_statuses")


@pytest.fixture
def kits(db) -> tuple[TeamKit, TeamKit]:
    """Build this season's kit (current) and last season's (retired).

    Returns:
        ``(current, retired)``.

    """
    current = TeamKit.objects.create(name="2026 Race Kit", slug="race-2026", is_current=True)
    retired = TeamKit.objects.create(name="2025 Race Kit", slug="race-2025", active=False)
    return current, retired


def _member(user_model, name: str, team_kit: dict | None = None, **extra):
    """Create a team member -- someone with a Discord login.

    Args:
        user_model: The User class.
        name: Username, Discord username and the seed of the Discord id.
        team_kit: Their stored statuses.
        **extra: Other User fields.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username=name,
        email=f"{name}@example.test",
        discord_id=f"d-{name}",
        discord_username=name,
        team_kit=team_kit or {},
        **extra,
    )


def _export(client, viewer, query: str = ""):
    """Download the export.

    Args:
        client: Test client.
        viewer: The signed-in user.
        query: A query string, including its "?".

    Returns:
        ``(response, rows)`` with the rows parsed as CSV, header first.

    """
    client.force_login(viewer)
    response = client.get(reverse("team_kit_export") + query)
    text = response.content.decode("utf-8") if response.status_code == 200 else ""
    return response, list(csv.reader(io.StringIO(text.removeprefix("\ufeff"))))


def _csv(rows: list[list]) -> str:
    """Write rows as CSV text.

    Args:
        rows: The rows, header first.

    Returns:
        The CSV.

    """
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


def _upload(client, viewer, content: str | bytes, name: str = "kits.csv"):
    """Upload a file for preview.

    Args:
        client: Test client.
        viewer: The signed-in user.
        content: The file, as text or bytes.
        name: Its filename.

    Returns:
        The response.

    """
    client.force_login(viewer)
    data = content.encode("utf-8") if isinstance(content, str) else content
    response = client.post(
        reverse("team_kit_import"), {"csv_file": SimpleUploadedFile(name, data, content_type="text/csv")}
    )
    client.last_preview = response  # what _confirm submits from, as a browser would
    return response


def _ticked_rows(response) -> list[str]:
    """Read the selection a preview page's confirm form will post.

    The one ``apply`` field, holding the default ticks as the page was rendered -- what
    pressing Apply without touching anything sends.

    Args:
        response: The preview response.

    Returns:
        The positions in the field, in order.

    """
    import re

    if response is None or response.status_code != 200:
        return []
    found = re.search(r'name="apply" id="kit-import-apply-list" value="([^"]*)"', response.content.decode())
    return found.group(1).split(",") if found and found.group(1) else []


def _ticked_boxes(response) -> list[str]:
    """Read which row boxes a preview page shows ticked.

    Args:
        response: The preview response.

    Returns:
        The ``data-position`` of every ticked row box, in page order.

    """
    import re

    return re.findall(r'data-position="(\d+)"[^>]*\bchecked\b', response.content.decode())


def _confirm(client, token: str | None = None, apply: list | None = None):
    """Confirm the pending preview.

    Args:
        client: Test client, already signed in.
        token: The token to post; defaults to the pending preview's own.
        apply: The rows to apply, as positions; defaults to exactly the rows the last preview
            ticked -- what pressing Apply without touching anything sends. Posted as the one
            comma-separated field the page's form posts.

    Returns:
        The response.

    """
    if token is None:
        token = client.session[SESSION_KEY]["token"]
    if apply is None:
        apply = _ticked_rows(getattr(client, "last_preview", None))
    return client.post(reverse("team_kit_import_confirm"), {"token": token, "apply": ",".join(apply)}, follow=True)


def _messages(response) -> list[str]:
    """Collect the flash messages on a followed response.

    Args:
        response: A response fetched with ``follow=True``.

    Returns:
        The message texts.

    """
    return [str(message) for message in response.context["messages"]]


def _kit(user) -> dict:
    """Re-read a user's stored statuses.

    Args:
        user: The user.

    Returns:
        Their ``team_kit`` as stored now.

    """
    user.refresh_from_db()
    return user.team_kit


# --- who may use it -----------------------------------------------------------------------


@pytest.mark.django_db
def test_team_member_cannot_export_import_or_confirm(client, team_member, kits):
    """The gate is on every action, not just the page that links to them."""
    client.force_login(team_member)
    assert client.get(reverse("team_kit_export")).status_code == 403
    upload = SimpleUploadedFile("k.csv", b"user_id,kit:race-2026\n", content_type="text/csv")
    assert client.post(reverse("team_kit_import"), {"csv_file": upload}).status_code == 403
    assert client.post(reverse("team_kit_import_confirm"), {"token": "x"}).status_code == 403


@pytest.mark.django_db
def test_membership_admin_can_export_and_import(client, membership_admin, user_model, kits):
    """Membership admins have the team kit page, so they have its export and import too."""
    rider = _member(user_model, "rider")
    response, _ = _export(client, membership_admin)
    assert response.status_code == 200

    _upload(client, membership_admin, _csv([["user_id", "kit:race-2026"], [rider.pk, "need"]]))
    _confirm(client)
    assert _kit(rider) == {"race-2026": "need"}


@pytest.mark.django_db
def test_export_and_import_are_in_the_permission_registry():
    """So the help icons on /site/config/ list them for both roles that can use them."""
    for role in ("app_admin", "membership_admin"):
        views = " ".join(PERMISSION_REGISTRY[role]["views"])
        assert "/site/config/team-kit/export/" in views
        assert "/site/config/team-kit/import/" in views


# --- export -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_lists_every_member_with_every_kit(client, app_admin, user_model, kits):
    """Every team member, a column per kit (retired included), statuses as stored keys.

    "Verified" is the page's rule -- through zauth -- with the method beside it, so a legacy
    verification shows as "no" but is still visible as legacy.
    """
    have = _member(
        user_model,
        "anna",
        {"race-2026": "have", "race-2025": "completed"},
        zwid=111,
        zwid_verified=True,
        zwid_verification_method="zauth",
    )
    blank = _member(user_model, "bert")
    legacy = _member(user_model, "cleo", zwid=333, zwid_verified=True, zwid_verification_method="legacy")
    user_model.objects.create_user(username="local-admin", email="l@example.test")  # no Discord login

    response, rows = _export(client, app_admin)

    assert response.status_code == 200
    assert rows[0] == EXPORT_HEADER
    assert rows[1:] == [
        [
            str(have.pk),
            "anna",
            "anna",
            "",
            "111",
            "yes",
            "zauth",
            "have",
            "completed",
            "race-2026=have race-2025=completed",
        ],
        [str(blank.pk), "bert", "bert", "", "", "no", "", "unknown", "unknown", "race-2026=unknown race-2025=unknown"],
        [
            str(legacy.pk),
            "cleo",
            "cleo",
            "",
            "333",
            "no",
            "legacy",
            "unknown",
            "unknown",
            "race-2026=unknown race-2025=unknown",
        ],
    ]


@pytest.mark.django_db
def test_export_downloads_the_list_on_screen(client, app_admin, user_model, kits):
    """The page's filters apply, so "needs the kit" exports the list to send to Zwift."""
    zauth = {"zwid_verified": True, "zwid_verification_method": "zauth"}
    _member(user_model, "needs-verified", {"race-2026": "need"}, **zauth)
    _member(user_model, "needs-unverified", {"race-2026": "need"})
    _member(user_model, "needs-legacy", {"race-2026": "need"}, zwid_verified=True, zwid_verification_method="legacy")
    _member(user_model, "has-it", {"race-2026": "have"}, **zauth)

    response, rows = _export(client, app_admin, "?status=need")
    assert [row[2] for row in rows[1:]] == ["needs-legacy", "needs-unverified", "needs-verified"]
    assert response["Content-Disposition"].endswith('-status-need.csv"')

    response, rows = _export(client, app_admin, "?status=need&verified=1")
    assert [row[2] for row in rows[1:]] == ["needs-verified"]
    assert response["Content-Disposition"].endswith('-zauth-verified-status-need.csv"')

    response, rows = _export(client, app_admin, "?status=need&status=have")
    assert [row[2] for row in rows[1:]] == ["has-it", "needs-legacy", "needs-unverified", "needs-verified"]
    assert response["Content-Disposition"].endswith('-status-need-have.csv"')


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("verified", "method", "zauth_cell", "method_cell"),
    [
        (True, "zauth", "yes", "zauth"),
        (True, "legacy", "no", "legacy"),
        (True, "admin", "no", "admin"),
        (True, "", "no", "other"),
        # A half-written row (both fields are editable in the Django admin).
        # Not zauth-verified -- the method alone must never count.
        (False, "zauth", "no", ""),
        (False, "", "no", ""),
    ],
    ids=["zauth", "legacy", "admin", "verified-no-method", "method-without-the-flag", "never-verified"],
)
def test_export_verification_columns_follow_the_page_rule(
    client, app_admin, user_model, kits, verified, method, zauth_cell, method_cell
):
    """Every stored verification state, in both columns and in the zauth-verified export."""
    _member(user_model, "rider", zwid=111, zwid_verified=verified, zwid_verification_method=method)

    _, rows = _export(client, app_admin)
    header = rows[0]
    assert rows[1][header.index("zauth_verified")] == zauth_cell
    assert rows[1][header.index("verification_method")] == method_cell

    _, filtered = _export(client, app_admin, "?verified=1")
    assert [row[2] for row in filtered[1:]] == (["rider"] if zauth_cell == "yes" else [])


@pytest.mark.django_db
def test_export_follows_the_race_verified_filter(client, app_admin, user_model, kits):
    """Race verified narrows the file as it narrows the page, alone and with the others."""
    zauth = {"zwid_verified": True, "zwid_verification_method": "zauth"}
    _member(user_model, "ready-zauth-needs", {"race-2026": "need"}, is_race_ready=True, **zauth)
    _member(user_model, "ready-needs", {"race-2026": "need"}, is_race_ready=True)
    _member(user_model, "not-ready-needs", {"race-2026": "need"}, **zauth)

    response, rows = _export(client, app_admin, "?race_verified=1")
    assert [row[2] for row in rows[1:]] == ["ready-needs", "ready-zauth-needs"]
    assert response["Content-Disposition"].endswith('-race-verified.csv"')

    response, rows = _export(client, app_admin, "?verified=1&race_verified=1&status=need")
    assert [row[2] for row in rows[1:]] == ["ready-zauth-needs"]
    assert response["Content-Disposition"].endswith('-zauth-verified-race-verified-status-need.csv"')


@pytest.mark.django_db
def test_export_neutralises_formulas_in_rider_chosen_names(client, app_admin, user_model, kits):
    """A Discord nickname is typed by the rider and must not run as a formula when opened."""
    _member(user_model, "mallory", discord_nickname='=HYPERLINK("http://evil.example","x")')
    _, rows = _export(client, app_admin)
    assert rows[1][1] == '\'=HYPERLINK("http://evil.example","x")'


@pytest.mark.django_db
def test_export_is_an_attachment_excel_reads_as_utf8(client, app_admin, kits):
    """The byte-order mark is what stops Excel mangling accented and emoji names."""
    client.force_login(app_admin)
    response = client.get(reverse("team_kit_export"))
    assert response["Content-Disposition"].startswith('attachment; filename="team-kit-')
    assert response.content.startswith("\ufeff".encode())


# --- the round trip -----------------------------------------------------------------------


@pytest.mark.django_db
def test_round_trip_changes_only_the_cells_edited(client, app_admin, user_model, kits):
    """Export, edit one cell, import: one change, and nothing written until confirmed."""
    anna = _member(user_model, "anna", {"race-2026": "need", "race-2025": "have"}, zwid=111)
    bert = _member(user_model, "bert", {"race-2026": "need"}, zwid=222)
    _, rows = _export(client, app_admin)
    rows[1][KIT_2026] = "submitted"  # anna, kit:race-2026

    response = _upload(client, app_admin, _csv(rows))

    assert response.status_code == 200
    plan = response.context["plan"]
    assert [(c.member.pk, c.kit.slug, c.old, c.new) for c in plan.changes] == [
        (anna.pk, "race-2026", "need", "submitted")
    ]
    assert plan.unchanged == 3  # anna 2025, bert 2026 and 2025
    assert _kit(anna) == {"race-2026": "need", "race-2025": "have"}, "the preview wrote to the database"

    response = _confirm(client)

    assert _kit(anna) == {"race-2026": "submitted", "race-2025": "have"}
    assert _kit(bert) == {"race-2026": "need"}
    assert _messages(response) == ["Updated 1 kit status for 1 member."]


@pytest.mark.django_db
@pytest.mark.parametrize("delimiter", [";", "\t"])
def test_semicolon_and_tab_separated_files_are_read(client, app_admin, user_model, kits, delimiter):
    """Excel writes semicolons wherever decimals use a comma, which is most of Europe."""
    anna = _member(user_model, "anna", {"race-2026": "need"})
    content = delimiter.join(["user_id", "discord_name", "kit:race-2026"]) + "\r\n"
    content += delimiter.join([str(anna.pk), "Anna, Team A", "have"]) + "\r\n"
    _upload(client, app_admin, content)
    _confirm(client)
    assert _kit(anna) == {"race-2026": "have"}


@pytest.mark.django_db
def test_blank_cell_leaves_the_status_alone(client, app_admin, user_model, kits):
    """Blank means "no change" -- never "reset to unknown".

    Anna is at "Need kit", not "I have the kit": a have-row is held back from applying by
    default, which would hide a blank cell wrongly read as unknown.
    """
    anna = _member(user_model, "anna", {"race-2026": "need"})
    bert = _member(user_model, "bert")
    preview = _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [anna.pk, ""], [bert.pk, "need"]]))
    assert [c.member.username for c in preview.context["plan"].changes] == ["bert"]
    _confirm(client)
    assert _kit(anna) == {"race-2026": "need"}
    assert _kit(bert) == {"race-2026": "need"}


@pytest.mark.django_db
def test_kits_the_file_does_not_mention_are_kept(client, app_admin, user_model, kits):
    """A file with one kit column must not drop the rider's other kits."""
    anna = _member(user_model, "anna", {"race-2025": "have", "some-old-kit": "need"})
    _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [anna.pk, "need"]]))
    _confirm(client)
    assert _kit(anna) == {"race-2025": "have", "some-old-kit": "need", "race-2026": "need"}


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("need", KitStatus.NEED),
        ("Need kit", KitStatus.NEED),
        ("  NEED   KIT ", KitStatus.NEED),
        ("I need the kit", KitStatus.NEED),
        ("Submitted to Zwift", KitStatus.SUBMITTED),
        ("completed", KitStatus.COMPLETED),
        ("I have the kit", KitStatus.HAVE),
        ("What\u2019s a kit", KitStatus.UNKNOWN),  # a curly apostrophe, as a spreadsheet autocorrects to
        ("unknown", KitStatus.UNKNOWN),
        ("", None),
        ("   ", None),
    ],
)
def test_status_cells_accept_keys_and_both_sets_of_labels(cell, expected):
    """Whatever wording a person is looking at on the site works in the sheet."""
    assert parse_status(cell) == expected


@pytest.mark.parametrize("cell", ["maybe", "needs", "yes"])
def test_anything_else_is_not_a_status(cell):
    """Unrecognised text is refused, not guessed at."""
    with pytest.raises(ValueError, match=cell):
        parse_status(cell)


@pytest.mark.django_db
def test_an_unreadable_status_refuses_the_whole_row(client, app_admin, user_model, kits):
    """A row is applied whole or not at all -- never the half that happened to parse."""
    anna = _member(user_model, "anna")
    response = _upload(
        client, app_admin, _csv([["user_id", "kit:race-2026", "kit:race-2025"], [anna.pk, "submitted", "maybe"]])
    )
    plan = response.context["plan"]
    assert plan.changes == []
    assert plan.errors == [
        {
            "row": 2,
            "message": 'Not a kit status: "maybe" under kit:race-2025. '
            "Use one of: unknown, need, submitted, completed, have.",
        }
    ]
    assert SESSION_KEY not in client.session


# --- matching rows to members ------------------------------------------------------------


@pytest.mark.django_db
def test_zwid_matches_when_there_is_no_user_id(client, app_admin, user_model, kits):
    """A sheet built by hand from a Zwift list has Zwift IDs, not our ids."""
    anna = _member(user_model, "anna", zwid=111)
    _upload(client, app_admin, _csv([["zwid", "kit:race-2026"], ["111", "completed"]]))
    _confirm(client)
    assert _kit(anna) == {"race-2026": "completed"}


@pytest.mark.django_db
def test_a_zwid_two_members_share_is_refused(client, app_admin, user_model, kits):
    """Zwift ID is not unique on a user, so a shared one names nobody in particular."""
    first = _member(user_model, "anna", zwid=111)
    second = _member(user_model, "anna-alt", zwid=111)
    response = _upload(client, app_admin, _csv([["zwid", "kit:race-2026"], ["111", "need"]]))
    plan = response.context["plan"]
    assert plan.changes == []
    assert plan.errors[0]["message"] == "2 team members have Zwift ID 111. Add a user_id column to say which."
    assert _kit(first) == _kit(second) == {}


@pytest.mark.django_db
def test_user_id_and_zwid_must_agree(client, app_admin, user_model, kits):
    """Catches a sheet with one column sorted and not the others."""
    anna = _member(user_model, "anna", zwid=111)
    _member(user_model, "bert", zwid=222)
    response = _upload(client, app_admin, _csv([["user_id", "zwid", "kit:race-2026"], [anna.pk, "222", "need"]]))
    plan = response.context["plan"]
    assert plan.changes == []
    assert "whose Zwift ID is 111, not 222" in plan.errors[0]["message"]
    assert _kit(anna) == {}


@pytest.mark.django_db
def test_only_team_members_can_be_matched(client, app_admin, user_model, kits):
    """Someone without a Discord login is not on the page, so the import will not reach them."""
    outsider = user_model.objects.create_user(username="outsider", email="o@example.test")
    response = _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [outsider.pk, "need"]]))
    assert response.context["plan"].errors[0]["message"] == f"No team member has user_id {outsider.pk}."
    assert _kit(outsider) == {}


@pytest.mark.django_db
def test_a_member_on_two_rows_is_refused_on_both(client, app_admin, user_model, kits):
    """Rather than whichever row came last silently winning."""
    anna = _member(user_model, "anna")
    response = _upload(
        client,
        app_admin,
        _csv([["user_id", "kit:race-2026"], [anna.pk, "need"], ["", ""], [anna.pk, "have"]]),
    )
    plan = response.context["plan"]
    assert plan.changes == []
    assert plan.errors == [
        {"row": 2, "message": "anna is on rows 2, 4. Keep one row per member -- none of these rows were used."}
    ]


# --- the file as a whole ------------------------------------------------------------------


@pytest.mark.django_db
def test_an_unknown_kit_column_is_reported_and_the_rest_still_read(client, app_admin, user_model, kits):
    """A typo'd or deleted kit is named on the preview rather than silently dropped."""
    anna = _member(user_model, "anna")
    response = _upload(
        client,
        app_admin,
        _csv([["user_id", "kit:race-2062", "kit:race-2026", "notes"], [anna.pk, "need", "have", "hi"]]),
    )
    plan = response.context["plan"]
    assert plan.unknown_kit_columns == ["kit:race-2062"]
    assert plan.ignored_columns == ["notes"]
    assert [(c.kit.slug, c.new) for c in plan.changes] == [("race-2026", "have")]
    content = response.content.decode()
    assert "kit:race-2062" in content
    assert "notes" in content


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("user_id,discord_name\n1,anna\n", "No kit columns found."),
        ("discord_name,kit:race-2026\nanna,need\n", 'Add a "user_id" or "zwid" column'),
        ("user_id,kit:race-2026,kit:race-2026\n1,need,have\n", 'Two columns are for "2026 Race Kit"'),
        ("user_id,USER_ID,kit:race-2026\n1,1,need\n", 'The column "USER_ID" appears twice'),
        ('user_id,discord_name,kit:race-2026\n1,"Bad name,need\n2,bert,have\n', "could not be read as CSV"),
        ("", "The file is empty."),
        ("user_id,kit:race-2026\n1,Café\n".encode("cp1252"), "not UTF-8"),
    ],
)
def test_a_file_that_cannot_be_used_is_refused_before_any_preview(client, app_admin, kits, content, message):
    """Problems with the whole file go back to the page with a message, and leave nothing pending."""
    response = _upload(client, app_admin, content)
    assert response.status_code == 302
    followed = client.get(response["Location"])
    assert any(message in text for text in _messages(followed))
    assert SESSION_KEY not in client.session


@pytest.mark.django_db
def test_an_oversized_file_is_refused(client, app_admin, kits):
    """Checked before the file is read at all."""
    response = _upload(client, app_admin, b"x" * (MAX_IMPORT_BYTES + 1))
    followed = client.get(response["Location"])
    assert _messages(followed) == ["That file is too large to import (the limit is 1024 KB)."]


@pytest.mark.django_db
def test_errors_beyond_the_limit_are_counted_not_listed(client, app_admin, kits):
    """A wrong file fails on every row; the preview summarises rather than scrolling forever."""
    rows = [["user_id", "kit:race-2026"]] + [[str(900_000 + n), "need"] for n in range(60)]
    response = _upload(client, app_admin, _csv(rows))
    assert len(response.context["shown_errors"]) == 50
    assert response.context["more_errors"] == 10
    assert "and 10 more" in response.content.decode()


# --- confirming ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_status_that_moved_after_the_preview_is_not_overwritten(client, app_admin, user_model, kits):
    """The rider (or another admin) changed it in between; confirming must not undo that unseen."""
    anna = _member(user_model, "anna", {"race-2026": "need"})
    bert = _member(user_model, "bert", {"race-2026": "need"})
    _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [anna.pk, "submitted"], [bert.pk, "submitted"]]))
    anna.team_kit = {"race-2026": "have"}
    anna.save(update_fields=["team_kit"])

    response = _confirm(client)

    assert _kit(anna) == {"race-2026": "have"}
    assert _kit(bert) == {"race-2026": "submitted"}
    assert _messages(response) == [
        "Updated 1 kit status for 1 member. 1 was skipped because it changed after the preview "
        "-- export again to see them as they are now."
    ]


@pytest.mark.django_db
def test_a_kit_deleted_after_the_preview_is_skipped(client, app_admin, user_model, kits):
    """Nothing is written under a slug that no longer belongs to a kit."""
    anna = _member(user_model, "anna")
    _upload(client, app_admin, _csv([["user_id", "kit:race-2025"], [anna.pk, "have"]]))
    kits[1].delete()
    _confirm(client)
    assert _kit(anna) == {}


@pytest.mark.django_db
def test_confirming_applies_only_the_preview_it_came_from(client, app_admin, user_model, kits):
    """A second upload in another tab must not become what the first tab's button applies."""
    anna = _member(user_model, "anna")
    _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [anna.pk, "need"]]))
    first_token = client.session[SESSION_KEY]["token"]
    _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [anna.pk, "have"]]))

    response = _confirm(client, first_token)

    assert _kit(anna) == {}
    assert "no longer waiting" in _messages(response)[0]
    # The newer preview is still pending, and its own tab can confirm it.
    _confirm(client)
    assert _kit(anna) == {"race-2026": "have"}


@pytest.mark.django_db
def test_a_preview_is_applied_once(client, app_admin, user_model, kits):
    """Pressing Apply twice (or the back button and Apply again) does nothing the second time."""
    anna = _member(user_model, "anna")
    _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [anna.pk, "need"]]))
    token = client.session[SESSION_KEY]["token"]
    _confirm(client, token)
    anna.team_kit = {"race-2026": "have"}
    anna.save(update_fields=["team_kit"])

    response = _confirm(client, token)

    assert _kit(anna) == {"race-2026": "have"}
    assert "no longer waiting" in _messages(response)[0]


@pytest.mark.django_db
def test_a_preview_with_nothing_to_do_clears_an_older_pending_one(client, app_admin, user_model, kits):
    """Otherwise the older plan would still be sitting there, confirmable from a stale tab."""
    anna = _member(user_model, "anna", {"race-2026": "have"})
    _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [anna.pk, "need"]]))
    stale_token = client.session[SESSION_KEY]["token"]

    response = _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [anna.pk, "have"]]))
    assert "every status in the file already matches" in response.content.decode()
    assert SESSION_KEY not in client.session

    _confirm(client, stale_token)
    assert _kit(anna) == {"race-2026": "have"}


@pytest.mark.django_db
def test_every_applied_change_is_logged(client, app_admin, user_model, kits):
    """So "who set my kit to submitted?" has an answer later."""
    anna = _member(user_model, "anna", {"race-2026": "need"})
    _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [anna.pk, "submitted"]]), name="zwift-order.csv")
    with mock.patch("apps.team.kit_views.logfire") as logfire:
        _confirm(client)
    applied = [call for call in logfire.info.call_args_list if call.args[0] == "Team kit CSV import applied"]
    assert len(applied) == 1
    assert applied[0].kwargs["user_id"] == app_admin.pk
    assert applied[0].kwargs["filename"] == "zwift-order.csv"
    assert applied[0].kwargs["changes"] == [{"user_id": anna.pk, "kit": "race-2026", "old": "need", "new": "submitted"}]


# --- the page -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_page_offers_export_with_its_filters_and_the_import_dialog(client, app_admin, kits):
    """Export carries the filters on screen; import explains the format it expects."""
    client.force_login(app_admin)
    page = reverse("config_section_page", args=["team_kit"])
    content = client.get(page + "?status=submitted&verified=1&status=need").content.decode()
    # Statuses in the team's order, whatever order the query gave them in.
    assert f'href="{reverse("team_kit_export")}?verified=1&amp;status=need&amp;status=submitted"' in content
    assert 'id="kit-import-dialog"' in content
    assert "<code>kit:race-2026</code>" in content
    assert "<code>submitted</code>" in content


@pytest.mark.django_db
def test_import_is_unavailable_until_there_is_a_kit(client, app_admin):
    """With no kit there is no column an import could fill in."""
    client.force_login(app_admin)
    content = client.get(reverse("config_section_page", args=["team_kit"])).content.decode()
    assert 'id="kit-import-dialog"' not in content
    assert "Add a kit first" in content


# --- an export that has gone stale --------------------------------------------------------


@pytest.mark.django_db
def test_an_unedited_cell_never_undoes_a_newer_status(client, app_admin, user_model, kits):
    """Export, the rider updates herself, the admin edits someone else: she is not reverted."""
    anna = _member(user_model, "anna", {"race-2026": "need"})
    bert = _member(user_model, "bert", {"race-2026": "need"})
    _, rows = _export(client, app_admin)
    anna.team_kit = {"race-2026": "have"}
    anna.save(update_fields=["team_kit"])
    rows[2][KIT_2026] = "submitted"  # bert, kit:race-2026 -- anna's row is left exactly as exported

    response = _upload(client, app_admin, _csv(rows))

    plan = response.context["plan"]
    assert [(c.member.pk, c.new) for c in plan.changes] == [(bert.pk, "submitted")]
    assert plan.kept_newer == 1
    assert "1 status changed after this file was exported" in response.content.decode()
    _confirm(client)
    assert _kit(anna) == {"race-2026": "have"}
    assert _kit(bert) == {"race-2026": "submitted"}


@pytest.mark.django_db
def test_an_edit_to_a_status_that_also_moved_is_shown_and_not_applied(client, app_admin, user_model, kits):
    """Neither the rider's change nor the admin's can be assumed to be the one that should win."""
    anna = _member(user_model, "anna", {"race-2026": "need"})
    _, rows = _export(client, app_admin)
    anna.team_kit = {"race-2026": "have"}
    anna.save(update_fields=["team_kit"])
    rows[1][KIT_2026] = "submitted"

    response = _upload(client, app_admin, _csv(rows))

    plan = response.context["plan"]
    assert plan.changes == []
    assert [(c.member.pk, c.exported, c.now, c.wanted) for c in plan.conflicts] == [
        (anna.pk, "need", "have", "submitted")
    ]
    content = response.content.decode()
    assert "Changed since your export" in content
    assert "1 conflict" in content
    assert SESSION_KEY not in client.session
    assert _kit(anna) == {"race-2026": "have"}


@pytest.mark.django_db
def test_clearing_the_snapshot_cell_applies_the_edit_anyway(client, app_admin, user_model, kits):
    """The way out of a conflict the preview names: the row is then read like a hand-built one.

    She has the kit by now, so the row is also held back by default -- overriding both takes
    clearing the cell and then ticking the row.
    """
    anna = _member(user_model, "anna", {"race-2026": "need"})
    _, rows = _export(client, app_admin)
    anna.team_kit = {"race-2026": "have"}
    anna.save(update_fields=["team_kit"])
    rows[1][KIT_2026] = "submitted"
    rows[1][SNAPSHOT] = ""

    preview = _upload(client, app_admin, _csv(rows))
    assert [(c.old, c.new) for c in preview.context["plan"].changes] == [("have", "submitted")]
    assert _ticked_rows(preview) == []

    _confirm(client, apply=["0"])

    assert _kit(anna) == {"race-2026": "submitted"}


@pytest.mark.django_db
def test_a_kit_added_after_the_export_is_read_without_the_check(client, app_admin, user_model, kits):
    """The snapshot has nothing to say about a kit it predates, so that cell simply sets it."""
    anna = _member(user_model, "anna", {"race-2026": "need"})
    _, rows = _export(client, app_admin)
    TeamKit.objects.create(name="2027 Race Kit", slug="race-2027")
    rows[0].append("kit:race-2027")
    rows[1].append("need")

    _upload(client, app_admin, _csv(rows))
    _confirm(client)

    assert _kit(anna) == {"race-2026": "need", "race-2027": "need"}


@pytest.mark.django_db
@pytest.mark.parametrize("snapshot", ["race-2026", "race-2026=maybe", "=need", "race-2026=need,race-2025=have"])
def test_a_damaged_snapshot_refuses_the_row(client, app_admin, user_model, kits, snapshot):
    """Rather than guessing what the row said when it was exported."""
    anna = _member(user_model, "anna")
    response = _upload(
        client, app_admin, _csv([["user_id", "kit:race-2026", "exported_statuses"], [anna.pk, "need", snapshot]])
    )
    plan = response.context["plan"]
    assert plan.changes == []
    assert "exported_statuses has been changed and cannot be read" in plan.errors[0]["message"]


@pytest.mark.django_db
def test_a_snapshot_the_formula_guard_prefixed_still_reads(client, app_admin, user_model):
    """An admin-typed slug may start with "-", which the export guards with an apostrophe.

    Checked through the one outcome only a snapshot that was actually read produces: the
    rider's newer status survives an unedited cell. Misread, the row would fall back to a
    plain import and revert her.
    """
    TeamKit.objects.create(name="Odd", slug="-odd", is_current=True)
    anna = _member(user_model, "anna", {"-odd": "need"})
    _, rows = _export(client, app_admin)
    assert rows[1][-1] == "'-odd=need"
    anna.team_kit = {"-odd": "have"}
    anna.save(update_fields=["team_kit"])

    response = _upload(client, app_admin, _csv(rows))

    plan = response.context["plan"]
    assert plan.changes == []
    assert plan.kept_newer == 1


# --- more ways a file can mislead -------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad_row",
    [
        ["222", "need"],  # a zwid that is not hers
        ["111", "maybe"],  # not a status
    ],
)
def test_a_duplicate_row_with_its_own_problem_still_refuses_the_other(client, app_admin, user_model, kits, bad_row):
    """One of anna's two rows being bad must not leave the other free to apply."""
    anna = _member(user_model, "anna", zwid=111)
    response = _upload(
        client,
        app_admin,
        _csv([["user_id", "zwid", "kit:race-2026"], [anna.pk, *bad_row], [anna.pk, "111", "have"]]),
    )
    plan = response.context["plan"]
    assert plan.changes == []
    assert plan.refused_rows == {2, 3}
    assert any("is on rows 2, 3" in error["message"] for error in plan.errors)


@pytest.mark.django_db
def test_rows_not_used_counts_rows_not_messages(client, app_admin, user_model, kits):
    """One duplicate message covers three rows; the badge says three."""
    anna = _member(user_model, "anna")
    rows = [["user_id", "kit:race-2026"], [anna.pk, "need"], [anna.pk, "need"], [anna.pk, "have"]]
    response = _upload(client, app_admin, _csv(rows))
    assert response.context["plan"].refused_row_count == 3
    assert "3 rows not used" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("raw_id", ["1_2", "+12", "\uff11\uff12", "12.0", "-12"])
def test_ids_must_be_plain_digits(client, app_admin, user_model, kits, raw_id):
    """int() alone would read "1_2" as 12 and match someone the sheet never named."""
    for n in range(12):
        _member(user_model, f"rider{n}")
    response = _upload(client, app_admin, _csv([["user_id", "kit:race-2026"], [raw_id, "need"]]))
    plan = response.context["plan"]
    assert plan.changes == []
    assert plan.errors[0]["message"] == f'user_id "{raw_id}" is not a number.'


@pytest.mark.django_db
def test_a_repeated_column_the_import_ignores_is_harmless(client, app_admin, user_model, kits):
    """The dialog says other columns are ignored, so a second "notes" must not refuse the file."""
    anna = _member(user_model, "anna")
    _upload(client, app_admin, _csv([["user_id", "notes", "kit:race-2026", "notes"], [anna.pk, "a", "need", "b"]]))
    _confirm(client)
    assert _kit(anna) == {"race-2026": "need"}


@pytest.mark.django_db
def test_slugs_differing_only_in_case_still_round_trip(client, app_admin, user_model):
    """Refused for new kits now, but one made before (or in the Django admin) must still work."""
    upper = TeamKit.objects.create(name="Upper", slug="Race-2027", is_current=True)
    lower = TeamKit.objects.create(name="Lower", slug="race-2027")
    anna = _member(user_model, "anna")
    _, rows = _export(client, app_admin)
    header = rows[0]
    rows[1][header.index(f"kit:{upper.slug}")] = "need"
    rows[1][header.index(f"kit:{lower.slug}")] = "have"

    _upload(client, app_admin, _csv(rows))
    _confirm(client)

    assert _kit(anna) == {"Race-2027": "need", "race-2027": "have"}


@pytest.mark.django_db
def test_a_new_kit_slug_differing_only_in_case_is_refused(client, app_admin, kits):
    """Two keys a person reads as one; the import could not tell a column for one from the other."""
    client.force_login(app_admin)
    response = client.post(
        reverse("team_kit_add"), {"name": "Again", "slug": "RACE-2026", "sort_order": 0}, follow=True
    )
    assert TeamKit.objects.filter(name="Again").count() == 0
    assert _messages(response) == ['Kit not added: A kit with the slug "race-2026" already exists.']


@pytest.mark.django_db
def test_kit_column_headers_match_without_regard_to_case(client, app_admin, user_model, kits):
    """A spreadsheet that upper-cases its header row still lines up with the kits."""
    anna = _member(user_model, "anna")
    _upload(client, app_admin, _csv([["USER_ID", "KIT:RACE-2026"], [anna.pk, "need"]]))
    _confirm(client)
    assert _kit(anna) == {"race-2026": "need"}


@pytest.mark.django_db
def test_a_header_that_could_mean_two_kits_is_not_guessed(client, app_admin, user_model):
    """With "Race-2027" and "race-2027" both existing, "kit:RACE-2027" names neither."""
    TeamKit.objects.create(name="Upper", slug="Race-2027", is_current=True)
    TeamKit.objects.create(name="Lower", slug="race-2027")
    anna = _member(user_model, "anna")
    response = _upload(
        client, app_admin, _csv([["user_id", "kit:RACE-2027", "kit:race-2027"], [anna.pk, "need", "have"]])
    )
    plan = response.context["plan"]
    assert plan.unknown_kit_columns == ["kit:RACE-2027"]
    assert [(c.kit.slug, c.new) for c in plan.changes] == [("race-2027", "have")]


# --- choosing which rows to apply ---------------------------------------------------------


def _three_riders(user_model) -> tuple:
    """Build three riders whose rows an import will change, one of whom has the kit.

    Args:
        user_model: The User class.

    Returns:
        ``(anna, bert, cleo)`` -- need, have and no answer, all about to be set to submitted.

    """
    anna = _member(user_model, "anna", {"race-2026": "need"})
    bert = _member(user_model, "bert", {"race-2026": "have"})
    cleo = _member(user_model, "cleo")
    return anna, bert, cleo


def _submit_all(*riders) -> str:
    """Write a sheet setting every rider's current kit to submitted.

    Args:
        *riders: The riders.

    Returns:
        The CSV.

    """
    return _csv([["user_id", "kit:race-2026"], *([rider.pk, "submitted"] for rider in riders)])


@pytest.mark.django_db
def test_every_row_is_ticked_except_riders_who_have_the_kit(client, app_admin, user_model, kits):
    """Moving a rider who already has the kit is almost always a stale sheet, so it waits for a tick.

    Only "I have the kit" holds a row back -- a rider at Submitted or Completed is still ticked.
    """
    anna, bert, cleo = _three_riders(user_model)
    dora = _member(user_model, "dora", {"race-2026": "submitted"})
    eve = _member(user_model, "eve", {"race-2026": "completed"})
    sheet = _csv([
        ["user_id", "kit:race-2026"],
        *([rider.pk, "submitted"] for rider in (anna, bert, cleo)),
        [dora.pk, "need"],
        [eve.pk, "need"],
    ])

    preview = _upload(client, app_admin, sheet)

    changes = preview.context["plan"].changes
    assert [(c.member.username, c.old) for c in changes] == [
        ("anna", "need"),
        ("bert", "have"),
        ("cleo", "unknown"),
        ("dora", "submitted"),
        ("eve", "completed"),
    ]
    # What is shown ticked and what Apply will post agree.
    assert _ticked_boxes(preview) == ["0", "2", "3", "4"]
    assert _ticked_rows(preview) == ["0", "2", "3", "4"]
    content = " ".join(preview.content.decode().split())
    assert "1 row would change a rider who already has the kit. It is not ticked" in content
    assert "Already has the kit &mdash; tick to change it anyway" in content
    assert 'Apply selected (<span id="kit-import-selected-count">4</span>)' in content


@pytest.mark.django_db
def test_pressing_apply_leaves_the_unticked_rider_alone(client, app_admin, user_model, kits):
    """The default: everyone moves but the rider who has the kit, and the message says so."""
    anna, bert, cleo = _three_riders(user_model)
    _upload(client, app_admin, _submit_all(anna, bert, cleo))

    response = _confirm(client)

    assert _kit(anna) == {"race-2026": "submitted"}
    assert _kit(bert) == {"race-2026": "have"}
    assert _kit(cleo) == {"race-2026": "submitted"}
    assert _messages(response) == ["Updated 2 kit statuses for 2 members. 1 unticked row was left as it was."]


@pytest.mark.django_db
def test_only_ticked_rows_are_applied(client, app_admin, user_model, kits):
    """Untick a row and it is not written -- including one that was ticked by default."""
    anna, bert, cleo = _three_riders(user_model)
    _upload(client, app_admin, _submit_all(anna, bert, cleo))

    _confirm(client, apply=["2"])

    assert _kit(anna) == {"race-2026": "need"}
    assert _kit(bert) == {"race-2026": "have"}
    assert _kit(cleo) == {"race-2026": "submitted"}


@pytest.mark.django_db
def test_ticking_a_held_back_row_applies_it(client, app_admin, user_model, kits):
    """Held back is a default, not a rule: a rider who has the kit can still be changed on purpose."""
    anna, bert, cleo = _three_riders(user_model)
    _upload(client, app_admin, _submit_all(anna, bert, cleo))

    _confirm(client, apply=["0", "1", "2"])

    assert _kit(bert) == {"race-2026": "submitted"}


@pytest.mark.django_db
def test_nothing_ticked_changes_nothing_and_ends_the_preview(client, app_admin, user_model, kits):
    """Applying with every row unticked is a cancel: nothing written, and nothing left pending."""
    anna, bert, cleo = _three_riders(user_model)
    _upload(client, app_admin, _submit_all(anna, bert, cleo))
    token = client.session[SESSION_KEY]["token"]

    response = _confirm(client, apply=[])

    assert (_kit(anna), _kit(bert), _kit(cleo)) == ({"race-2026": "need"}, {"race-2026": "have"}, {})
    assert _messages(response) == ["No rows were ticked, so nothing was changed."]
    assert SESSION_KEY not in client.session
    assert "no longer waiting" in _messages(_confirm(client, token, apply=["0"]))[0]


@pytest.mark.django_db
def test_a_tampered_selection_can_only_choose_previewed_rows(client, app_admin, user_model, kits):
    """Out-of-range, negative, non-numeric and repeated positions are ignored, not guessed at."""
    anna, bert, cleo = _three_riders(user_model)
    _upload(client, app_admin, _submit_all(anna, bert, cleo))

    # 5000 digits: int() refuses anything over 4300, so parsing positions would crash with a 500.
    _confirm(client, apply=["99", "-1", "abc", "1.0", "\uff11", "", "01", " 1", "1" * 5000, "2", "2"])

    # "\uff11" is a full-width 1 -- bert's row -- which int() would happily read as 1.
    assert _kit(anna) == {"race-2026": "need"}
    assert _kit(bert) == {"race-2026": "have"}
    assert _kit(cleo) == {"race-2026": "submitted"}


@pytest.mark.django_db
def test_unticked_rows_are_logged_with_the_import(client, app_admin, user_model, kits):
    """The audit trail says how many previewed rows were deliberately left out."""
    anna, bert, cleo = _three_riders(user_model)
    _upload(client, app_admin, _submit_all(anna, bert, cleo))

    with mock.patch("apps.team.kit_views.logfire") as logfire:
        _confirm(client, apply=["2"])

    applied = [call for call in logfire.info.call_args_list if call.args[0] == "Team kit CSV import applied"]
    assert applied[0].kwargs["unticked_count"] == 2
    assert applied[0].kwargs["changes"] == [{"user_id": cleo.pk, "kit": "race-2026", "old": None, "new": "submitted"}]


@pytest.mark.django_db
def test_the_tick_all_box_waits_for_javascript(client, app_admin, user_model, kits):
    """Without the script it would do nothing, so it starts hidden and the script reveals it."""
    anna, bert, cleo = _three_riders(user_model)
    content = _upload(client, app_admin, _submit_all(anna, bert, cleo)).content.decode()

    box = content[
        content.index('id="kit-import-select-all"') : content.index(">", content.index('id="kit-import-select-all"'))
    ]
    assert " hidden" in box
    assert "all.hidden = false;" in content


@pytest.mark.django_db
def test_row_boxes_post_nothing_themselves(client, app_admin, user_model, kits):
    """The selection travels in one field; the row boxes post nothing themselves.

    They are nameless, and disabled until the script wires them to that field, so without
    JavaScript the page shows exactly what it will post.
    """
    import re

    anna, bert, cleo = _three_riders(user_model)
    content = _upload(client, app_admin, _submit_all(anna, bert, cleo)).content.decode()
    form = content[
        content.index('id="kit-import-confirm"') : content.index("</form>", content.index('id="kit-import-confirm"'))
    ]

    assert re.findall(r'name="(\w+)"', form) == ["csrfmiddlewaretoken", "token", "apply"]
    boxes = re.findall(r"<input[^>]*data-position[^>]*>", form)
    assert len(boxes) == 3
    assert all(" disabled" in box and 'autocomplete="off"' in box for box in boxes)
    assert "box.disabled = false;" in content


@pytest.mark.django_db
@override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=10)
def test_an_import_bigger_than_the_field_limit_still_applies(client, app_admin, user_model, kits):
    """An import with more rows than Django's POST field limit can still be applied.

    Django refuses a POST of more fields than DATA_UPLOAD_MAX_NUMBER_FIELDS (1000 by default).
    One field for the whole selection fits any import; one field per row could not.
    """
    riders = [_member(user_model, f"rider{n:02d}") for n in range(30)]
    _upload(client, app_admin, _submit_all(*riders))

    response = _confirm(client)

    assert response.status_code == 200
    assert all(_kit(rider) == {"race-2026": "submitted"} for rider in riders)

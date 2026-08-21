"""CSV export of the Zwift connections report.

The export hangs off the page's own query string and runs the same filtering code, so
what downloads is exactly what is on screen. These tests exist to keep that true --
a parallel implementation would be free to drift from the filters.
"""

import csv
import io

import pytest
from django.urls import reverse

URL = reverse("team:zwift_connections")


@pytest.fixture(autouse=True)
def _no_zauth(monkeypatch):
    """Keep the service out of it; these tests are about filtering and CSV shape."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: False)


@pytest.fixture
def members(user_model, db):
    """Three members spanning the verification buckets.

    Returns:
        dict of bucket name -> user.

    """
    method = user_model.VerificationMethod
    return {
        "zauth": user_model.objects.create_user(
            username="zed", email="z@example.test", first_name="Zoe", last_name="Auth",
            discord_id="1", discord_username="zoe", zwid=101,
            zwid_verified=True, zwid_verification_method=method.ZAUTH,
        ),
        "legacy": user_model.objects.create_user(
            username="len", email="l@example.test", first_name="Len", last_name="Gacy",
            discord_id="2", discord_username="len", zwid=102,
            zwid_verified=True, zwid_verification_method=method.LEGACY,
        ),
        "unverified": user_model.objects.create_user(
            username="una", email="u@example.test", first_name="Una", last_name="Verified",
            discord_id="3", discord_username="una",
        ),
    }


def _rows(response) -> list[dict]:
    """Parse a CSV response.

    Returns:
        The data rows as dicts.

    """
    return list(csv.DictReader(io.StringIO(response.content.decode())))


@pytest.mark.django_db
def test_export_returns_a_csv_attachment(client, members, user_model) -> None:
    admin = user_model.objects.create_user(
        username="adm", email="a@example.test",
        permission_overrides={"team_member": True, "membership_admin": True},
    )
    client.force_login(admin)

    resp = client.get(URL, {"export": "csv"})

    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert "attachment" in resp["Content-Disposition"]
    assert "zwift-connections-" in resp["Content-Disposition"]
    assert len(_rows(resp)) == 3


@pytest.mark.django_db
def test_the_export_honours_the_active_filter(client, members, user_model) -> None:
    """The whole point: it exports the filtered rows, not the whole table."""
    admin = user_model.objects.create_user(
        username="adm", email="a@example.test",
        permission_overrides={"team_member": True, "membership_admin": True},
    )
    client.force_login(admin)

    rows = _rows(client.get(URL, {"export": "csv", "method": "legacy"}))

    assert [r["Name"] for r in rows] == ["Len Gacy"]


@pytest.mark.django_db
def test_the_export_honours_the_search(client, members, user_model) -> None:
    admin = user_model.objects.create_user(
        username="adm", email="a@example.test",
        permission_overrides={"team_member": True, "membership_admin": True},
    )
    client.force_login(admin)

    rows = _rows(client.get(URL, {"export": "csv", "q": "una"}))

    assert [r["Name"] for r in rows] == ["Una Verified"]


@pytest.mark.django_db
def test_the_export_honours_the_sort(client, members, user_model) -> None:
    """It is built from the live query string, so the sort travels with it."""
    admin = user_model.objects.create_user(
        username="adm", email="a@example.test",
        permission_overrides={"team_member": True, "membership_admin": True},
    )
    client.force_login(admin)

    asc = [r["Name"] for r in _rows(client.get(URL, {"export": "csv", "sort": "name", "dir": "asc"}))]
    desc = [r["Name"] for r in _rows(client.get(URL, {"export": "csv", "sort": "name", "dir": "desc"}))]

    assert asc == list(reversed(desc))


@pytest.mark.django_db
def test_the_page_still_renders_html_without_the_flag(client, members, user_model) -> None:
    admin = user_model.objects.create_user(
        username="adm", email="a@example.test",
        permission_overrides={"team_member": True, "membership_admin": True},
    )
    client.force_login(admin)

    resp = client.get(URL)

    assert "text/html" in resp["Content-Type"]
    assert "Export CSV" in resp.content.decode()


@pytest.mark.django_db
def test_export_needs_the_same_permission_as_the_page(client, team_member) -> None:
    """The CSV carries every row the page does, so it must not be a softer door."""
    client.force_login(team_member)

    assert client.get(URL, {"export": "csv"}).status_code == 403

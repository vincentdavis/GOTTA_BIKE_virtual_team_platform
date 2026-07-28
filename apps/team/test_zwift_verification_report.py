"""The Zwift verification admin report at /team/zwift-connections/.

Phase 4 of the zauth migration: shows how each member verified, so the legacy and
not-verified counts can be driven to zero before the cutover flag is flipped.
"""

import pytest
from django.urls import reverse

URL = reverse("team:zwift_connections")


@pytest.fixture
def membership_admin(db, user_model):
    return user_model.objects.create_user(
        username="mem-admin",
        discord_id="1",
        permission_overrides={"membership_admin": True, "team_member": True},
    )


@pytest.fixture
def admin_client_(client, membership_admin):
    client.force_login(membership_admin)
    return client


@pytest.fixture
def _no_service(monkeypatch):
    """Default to an unconfigured service so tests opt in to connection data."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: False)


def _member(user_model, username, **kwargs):
    return user_model.objects.create_user(username=username, discord_id=username, **kwargs)


@pytest.mark.django_db
def test_requires_membership_admin(client, user_model):
    plain = user_model.objects.create_user(username="plain", discord_id="99")
    client.force_login(plain)

    assert client.get(URL).status_code == 403


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_counts_bucket_members_by_verification_method(admin_client_, user_model):
    _member(user_model, "z", zwid_verified=True, zwid_verification_method="zauth")
    _member(user_model, "l", zwid_verified=True, zwid_verification_method="legacy")
    _member(user_model, "a", zwid_verified=True, zwid_verification_method="admin")
    _member(user_model, "n")  # never verified

    counts = admin_client_.get(URL).context["counts"]

    assert counts["zauth"] == 1
    assert counts["legacy"] == 1
    assert counts["admin"] == 1
    assert counts["unverified"] == 2  # "n" plus the admin viewing the page
    assert counts["total"] == 5


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_verified_without_a_method_gets_its_own_bucket(admin_client_, user_model):
    """The retired password flow never stamped a method, so these are not 'legacy'."""
    _member(user_model, "ghost", zwid_verified=True, zwid_verification_method="")

    counts = admin_client_.get(URL).context["counts"]

    assert counts["unmethoded"] == 1
    assert counts["legacy"] == 0
    assert counts["unverified"] == 1  # only the admin, who is unverified


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_users_without_a_discord_id_are_excluded(admin_client_, user_model):
    """Service accounts and bare Django users are not members to chase."""
    user_model.objects.create_user(username="scripted")  # no discord_id

    rows = admin_client_.get(URL).context["rows"]

    assert "scripted" not in [r["user"].username for r in rows]


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_method_filter_narrows_the_rows_but_not_the_counts(admin_client_, user_model):
    _member(user_model, "z", zwid_verified=True, zwid_verification_method="zauth")
    _member(user_model, "l", zwid_verified=True, zwid_verification_method="legacy")

    resp = admin_client_.get(URL, {"method": "legacy"})

    assert [r["user"].username for r in resp.context["rows"]] == ["l"]
    assert resp.context["counts"]["zauth"] == 1  # summary still spans everyone


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_search_matches_name_discord_and_zwid(admin_client_, user_model):
    _member(user_model, "findme", first_name="Ada", last_name="Lovelace", zwid=4242)
    _member(user_model, "other", first_name="Someone", last_name="Else")

    for term in ("ada", "lovelace", "4242", "findme"):
        rows = admin_client_.get(URL, {"q": term}).context["rows"]
        assert [r["user"].username for r in rows] == ["findme"], term


@pytest.mark.django_db
def test_connection_state_comes_from_the_service(admin_client_, user_model, monkeypatch):
    connected = _member(user_model, "conn", zwid_verified=True, zwid_verification_method="zauth", zwid=555)
    _member(user_model, "notconn", zwid_verified=True, zwid_verification_method="legacy")
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.zwift.client.list_connections",
        lambda: [{"user_id": str(connected.pk), "zwid": "555", "zwift_name": "Ada", "connected_at": "2026-07-01"}],
    )

    resp = admin_client_.get(URL)
    by_name = {r["user"].username: r for r in resp.context["rows"]}

    assert resp.context["counts"]["connected"] == 1
    assert by_name["conn"]["connected"] is True
    assert by_name["conn"]["zwift_name"] == "Ada"
    assert by_name["notconn"]["connected"] is False


@pytest.mark.django_db
def test_connected_filter(admin_client_, user_model, monkeypatch):
    connected = _member(user_model, "conn")
    _member(user_model, "notconn")
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.list_connections", lambda: [{"user_id": str(connected.pk), "zwid": "555"}])

    yes = admin_client_.get(URL, {"connected": "yes"}).context["rows"]
    no = admin_client_.get(URL, {"connected": "no"}).context["rows"]

    assert [r["user"].username for r in yes] == ["conn"]
    assert "conn" not in [r["user"].username for r in no]


@pytest.mark.django_db
def test_application_uuid_connections_are_listed_as_orphans(admin_client_, monkeypatch):
    """A membership application connects under its own UUID, which is not a user pk."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.zwift.client.list_connections",
        lambda: [{"user_id": "3f6a1e2c-0000-4000-8000-000000000000", "zwid": "777"}],
    )

    resp = admin_client_.get(URL)

    assert [c["zwid"] for c in resp.context["orphans"]] == ["777"]
    assert resp.context["rows"] == [] or all(not r["connected"] for r in resp.context["rows"])


# --- sorting ------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_sorts_by_name_ascending_by_default(admin_client_, user_model):
    _member(user_model, "b", first_name="Bea")
    _member(user_model, "a", first_name="Ada")
    _member(user_model, "c", first_name="Cas")

    resp = admin_client_.get(URL)
    names = [r["user"].first_name for r in resp.context["rows"] if r["user"].first_name]

    assert names == ["Ada", "Bea", "Cas"]
    assert resp.context["sort_by"] == "name"
    assert resp.context["sort_dir"] == "asc"


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_sort_direction_reverses(admin_client_, user_model):
    _member(user_model, "a", first_name="Ada")
    _member(user_model, "b", first_name="Bea")

    rows = admin_client_.get(URL, {"sort": "name", "dir": "desc"}).context["rows"]
    names = [r["user"].first_name for r in rows if r["user"].first_name]

    assert names == ["Bea", "Ada"]


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_verification_sorts_by_migration_priority_not_alphabetically(admin_client_, user_model):
    """Alphabetical would interleave admin and unverified; priority groups the work."""
    _member(user_model, "z", zwid_verified=True, zwid_verification_method="zauth")
    _member(user_model, "l", zwid_verified=True, zwid_verification_method="legacy")
    _member(user_model, "a", zwid_verified=True, zwid_verification_method="admin")
    _member(user_model, "n")

    rows = admin_client_.get(URL, {"sort": "method"}).context["rows"]
    buckets = [r["bucket"] for r in rows]

    assert buckets.index("unverified") < buckets.index("legacy") < buckets.index("admin") < buckets.index("zauth")


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_sorting_by_a_nullable_column_does_not_error(admin_client_, user_model):
    """A None mixed with real values raises TypeError in sorted() if unguarded."""
    _member(user_model, "has", zwid=500, zwid_verified=True, zwid_verification_method="zauth")
    _member(user_model, "none")  # zwid and verified_at both None

    for column in ("zwid", "verified_at", "zwift_name", "category", "discord", "connected"):
        resp = admin_client_.get(URL, {"sort": column})
        assert resp.status_code == 200, column


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_an_unknown_sort_key_is_ignored(admin_client_, user_model):
    _member(user_model, "a", first_name="Ada")

    resp = admin_client_.get(URL, {"sort": "'; DROP TABLE"})

    assert resp.status_code == 200


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_sorting_preserves_the_active_filters(admin_client_, user_model):
    _member(user_model, "l1", first_name="Ada", zwid_verified=True, zwid_verification_method="legacy")
    _member(user_model, "z1", first_name="Bea", zwid_verified=True, zwid_verification_method="zauth")

    resp = admin_client_.get(URL, {"method": "legacy", "sort": "name", "dir": "desc"})

    assert [r["user"].username for r in resp.context["rows"]] == ["l1"]
    assert "method=legacy" in resp.context["filter_qs"]


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_service")
def test_headers_link_with_filters_and_toggle_direction(admin_client_, user_model):
    _member(user_model, "l1", zwid_verified=True, zwid_verification_method="legacy")

    body = admin_client_.get(URL, {"method": "legacy", "sort": "name", "dir": "asc"}).content.decode()

    assert "method=legacy&amp;sort=zwid&amp;dir=asc" in body  # other columns start ascending
    assert "method=legacy&amp;sort=name&amp;dir=desc" in body  # the active one flips


@pytest.mark.django_db
def test_a_service_outage_still_renders_local_verification_data(admin_client_, user_model, monkeypatch):
    _member(user_model, "l", zwid_verified=True, zwid_verification_method="legacy")
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.list_connections", lambda: None)

    resp = admin_client_.get(URL)

    assert resp.status_code == 200
    assert resp.context["service_error"] is True
    assert resp.context["counts"]["legacy"] == 1

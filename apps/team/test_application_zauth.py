"""Tests for the Zwift OAuth (zauth) path on the public membership application.

An application has no User yet, so the service is keyed by the application UUID.
These also pin that such a connection can never leak into the per-user reconcile.
"""

from html.parser import HTMLParser

import pytest
from django.urls import reverse

from apps.team import views
from apps.team.models import MembershipApplication
from apps.zwift.client import DisconnectOutcome


@pytest.fixture
def application(db):
    return MembershipApplication.objects.create(discord_id="123456789", discord_username="applicant")


def _connect_url(app):
    return reverse("team:application_zauth_connect", args=[app.pk])


# --- connect -----------------------------------------------------------------


@pytest.mark.django_db
def test_connect_redirects_to_consent_keyed_by_application_uuid(client, application, monkeypatch):
    seen = {}

    def fake_authorize(user_id, return_url, **kwargs):
        seen.update(user_id=user_id, return_url=return_url)
        return "https://zwift.example/consent?state=abc"

    monkeypatch.setattr("apps.zwift.client.get_authorize_url", fake_authorize)

    resp = client.post(_connect_url(application))

    assert resp.status_code == 302
    assert resp["Location"] == "https://zwift.example/consent?state=abc"
    assert seen["user_id"] == str(application.pk)  # UUID, not a user PK
    assert str(application.pk) in seen["return_url"]


@pytest.mark.django_db
def test_connect_returns_to_the_application_when_the_service_fails(client, application, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.get_authorize_url", lambda *a, **kw: None)

    resp = client.post(_connect_url(application))

    assert resp.status_code == 302
    assert str(application.pk) in resp["Location"]


@pytest.mark.django_db
def test_connect_refuses_once_the_application_is_no_longer_editable(client, application, monkeypatch):
    application.status = "approved"
    application.save(update_fields=["status"])
    called = []
    monkeypatch.setattr("apps.zwift.client.get_authorize_url", lambda *a, **kw: called.append(1))

    resp = client.post(_connect_url(application))

    assert resp.status_code == 302
    assert called == []  # never reaches the service


# --- status sync -------------------------------------------------------------


@pytest.mark.django_db
def test_sync_stamps_zwift_id_when_connected(application, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": True, "zwid": "4242"})

    assert views._sync_application_zauth(application) is True

    application.refresh_from_db()
    assert application.zwift_id == "4242"
    assert application.zwift_verified is True


@pytest.mark.parametrize("bad_zwid", [None, "", "abc", "0"])
@pytest.mark.django_db
def test_sync_refuses_without_a_usable_zwid(application, monkeypatch, bad_zwid):
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": True, "zwid": bad_zwid})

    assert views._sync_application_zauth(application) is False

    application.refresh_from_db()
    assert application.zwift_verified is False
    assert application.zwift_id == ""


@pytest.mark.parametrize("status", [{"connected": False}, None])
@pytest.mark.django_db
def test_sync_is_a_noop_when_not_connected_or_unavailable(application, monkeypatch, status):
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: status)

    assert views._sync_application_zauth(application) is False
    application.refresh_from_db()
    assert application.zwift_verified is False


@pytest.mark.django_db
def test_public_page_picks_up_the_connection_on_return(client, application, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": True, "zwid": "777"})

    client.get(reverse("team:application_public", args=[application.pk]))

    application.refresh_from_db()
    assert application.zwift_verified is True
    assert application.zwift_id == "777"


# --- isolation from the per-user reconcile -----------------------------------


@pytest.mark.django_db
def test_reconcile_ignores_application_uuid_connections(application, user_model, monkeypatch):
    """An application's connection must never be mistaken for a platform user's."""
    from apps.zwift import verification

    u = user_model.objects.create_user(username="z", zwid=555, zwid_verified=True, zwid_verification_method="zauth")
    monkeypatch.setattr(
        "apps.zwift.client.list_connections",
        lambda: [{"user_id": str(application.pk), "zwid": "4242"}],
    )

    result = verification.reconcile_all()

    u.refresh_from_db()
    assert result["connected"] == 0  # the UUID row is skipped outright
    assert result["granted"] == 0


# --- the public page's forms --------------------------------------------------


class _FormMap(HTMLParser):
    """Walk the page the way a browser's form-owner rules do, recording what it finds.

    Records every ``<form>`` start seen while another form was still open (which the HTML
    parser would silently drop), and for each submit button, which form it belongs to.
    """

    def __init__(self):
        super().__init__()
        self.open_forms = []
        self.nested_starts = []
        self.forms = {}
        self.buttons = []
        self._button = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            if self.open_forms:
                self.nested_starts.append(attrs)
            key = attrs.get("id") or f"form-{len(self.forms)}"
            self.forms[key] = attrs
            self.open_forms.append(key)
        elif tag == "button" and attrs.get("type", "submit") == "submit":
            owner = attrs.get("form") or (self.open_forms[-1] if self.open_forms else None)
            self._button = {"owner": owner, "text": ""}
            self.buttons.append(self._button)

    def handle_endtag(self, tag):
        if tag == "form" and self.open_forms:
            self.open_forms.pop()
        elif tag == "button":
            self._button = None

    def handle_data(self, data):
        if self._button is not None:
            self._button["text"] += data

    def button(self, label):
        (match,) = [b for b in self.buttons if label in " ".join(b["text"].split())]
        return match


def _render_public_page(client, application, monkeypatch):
    """Render and parse the public page for a registration.

    Returns:
        The parsed page.

    """
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": False})
    body = client.get(reverse("team:application_public", args=[application.pk])).content.decode()
    page = _FormMap()
    page.feed(body)
    return page


@pytest.fixture
def public_page(client, application, monkeypatch):
    """Render the public page for an editable registration that has not connected Zwift.

    Returns:
        The parsed page.

    """
    return _render_public_page(client, application, monkeypatch)


@pytest.fixture
def verified_public_page(client, application, monkeypatch):
    """Render the public page for an editable registration that has connected Zwift.

    Returns:
        The parsed page.

    """
    application.zwift_id = "4242"
    application.zwift_verified = True
    application.save(update_fields=["zwift_id", "zwift_verified"])
    return _render_public_page(client, application, monkeypatch)


@pytest.mark.django_db
def test_no_form_is_opened_inside_another(public_page):
    """A nested <form> start is dropped by the parser, and its end tag closes the outer form."""
    assert public_page.nested_starts == []
    assert public_page.open_forms == []


@pytest.mark.django_db
def test_connect_submits_to_the_zauth_connect_url(public_page, application):
    owner = public_page.button("Connect Zwift")["owner"]

    assert public_page.forms[owner]["action"] == reverse("team:application_zauth_connect", args=[application.pk])
    assert public_page.forms[owner]["method"] == "post"


@pytest.mark.django_db
def test_save_belongs_to_the_registration_form(public_page):
    owner = public_page.button("Save Registration")["owner"]

    assert owner == "registration-form"
    # No action: it posts back to the registration page itself.
    assert "action" not in public_page.forms[owner]
    assert public_page.forms[owner]["method"] == "post"


@pytest.mark.django_db
def test_enter_in_a_field_saves_rather_than_removing_zwift(verified_public_page):
    """A browser's Enter presses the form's first submit button; Remove must not be one."""
    owned = [b for b in verified_public_page.buttons if b["owner"] == "registration-form"]

    assert owned, "the registration form has a submit button"
    assert "Save Registration" in " ".join(owned[0]["text"].split())
    assert not [b for b in owned if "Remove" in b["text"]]


@pytest.mark.django_db
def test_the_verified_page_nests_no_forms_either(verified_public_page):
    assert verified_public_page.nested_starts == []
    assert verified_public_page.open_forms == []


# --- removing the connection ---------------------------------------------------


@pytest.fixture
def verified_application(application):
    """Mark the registration as connected to Zwift.

    Returns:
        The registration.

    """
    application.zwift_id = "4242"
    application.zwift_verified = True
    application.save(update_fields=["zwift_id", "zwift_verified"])
    return application


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", [DisconnectOutcome.REMOVED, DisconnectOutcome.NO_LINK])
def test_remove_disconnects_the_registration_link(client, verified_application, monkeypatch, outcome):
    """Otherwise the public page re-reads the service on its next load and verifies it again."""
    calls = []
    monkeypatch.setattr("apps.zwift.client.disconnect_link", lambda uid: calls.append(uid) or outcome)

    client.post(reverse("team:application_unverify_zwift", args=[verified_application.pk]))

    verified_application.refresh_from_db()
    assert calls == [str(verified_application.pk)]
    assert verified_application.zwift_verified is False
    assert verified_application.zwift_id == ""


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", [DisconnectOutcome.FAILED, DisconnectOutcome.UNCONFIGURED])
def test_remove_keeps_the_registration_marked_while_the_link_may_survive(
    client, verified_application, monkeypatch, outcome
):
    """The fields are what tells a later deletion to look for a link, so they stay."""
    monkeypatch.setattr("apps.zwift.client.disconnect_link", lambda uid: outcome)

    response = client.post(reverse("team:application_unverify_zwift", args=[verified_application.pk]))

    verified_application.refresh_from_db()
    assert verified_application.zwift_verified is True
    assert verified_application.zwift_id == "4242"
    body = response.content.decode()
    assert 'role="alert"' in body
    assert "try again later" in body


@pytest.mark.django_db
def test_remove_does_nothing_once_the_registration_is_locked(client, application, monkeypatch):
    application.status = "approved"
    application.zwift_verified = True
    application.save(update_fields=["status", "zwift_verified"])
    calls = []
    monkeypatch.setattr("apps.zwift.client.disconnect_link", lambda uid: calls.append(uid))

    client.post(reverse("team:application_unverify_zwift", args=[application.pk]))

    application.refresh_from_db()
    assert calls == []
    assert application.zwift_verified is True

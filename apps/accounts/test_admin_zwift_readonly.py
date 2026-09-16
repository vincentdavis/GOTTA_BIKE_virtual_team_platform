"""The Django admin can show a rider's Zwift verification but not change it.

Verification only comes from the zauth connection now. An admin-typed zwid or flag is the
staff grant that was retired, so the four fields are read-only on the change form and
absent from the add form -- and a hand-built POST carrying them must change nothing.
"""

from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django.utils import timezone

ZWIFT_FIELDS = ("zwid", "zwid_verified", "zwid_verification_method", "zwid_verified_at")


class _ChangeForm(HTMLParser):
    """Collect what a browser would submit from the admin change form.

    Only the main form's controls are read; checkboxes and radios count only when checked,
    and a select submits its selected options.
    """

    def __init__(self):
        super().__init__()
        self.data = {}
        self._in_form = False
        self._select = None
        self._textarea = None

    def _add(self, name, value):
        self.data.setdefault(name, []).append(value)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "user_form":
            self._in_form = True
        if not self._in_form:
            return
        name = attrs.get("name")
        if tag == "input" and name:
            kind = attrs.get("type", "text")
            if kind in {"checkbox", "radio"}:
                if "checked" in attrs:
                    self._add(name, attrs.get("value", "on"))
            elif kind not in {"submit", "button", "file", "image", "reset"}:
                self._add(name, attrs.get("value", ""))
        elif tag == "select" and name:
            self._select = name
        elif tag == "option" and self._select and "selected" in attrs:
            self._add(self._select, attrs.get("value", ""))
        elif tag == "textarea" and name:
            self._textarea = name
            self._add(name, "")

    def handle_endtag(self, tag):
        if tag == "form" and self._in_form:
            self._in_form = False
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea:
            self.data[self._textarea][-1] += data


@pytest.fixture
def rider(user_model):
    """Build a zauth-verified rider.

    Returns:
        The rider.

    """
    return user_model.objects.create_user(
        username="rider",
        email="rider@example.test",
        zwid=4242,
        zwid_verified=True,
        zwid_verification_method="zauth",
        zwid_verified_at=timezone.now(),
    )


@pytest.mark.django_db
def test_the_change_form_shows_the_fields_without_inputs(client, superuser, rider):
    client.force_login(superuser)

    body = client.get(reverse("admin:accounts_user_change", args=[rider.pk])).content.decode()

    for field in ZWIFT_FIELDS:
        assert f'name="{field}"' not in body
    assert "4242" in body  # still shown, read-only


@pytest.mark.django_db
def test_the_add_form_does_not_offer_a_zwid(client, superuser):
    client.force_login(superuser)

    body = client.get(reverse("admin:accounts_user_add")).content.decode()

    for field in ZWIFT_FIELDS:
        assert f'name="{field}"' not in body


@pytest.mark.django_db
def test_a_change_form_post_cannot_alter_the_verification(client, superuser, rider):
    """The form saves -- so the POST was valid -- and the four fields are untouched."""
    client.force_login(superuser)
    url = reverse("admin:accounts_user_change", args=[rider.pk])
    before = rider.zwid_verified_at
    page = _ChangeForm()
    page.feed(client.get(url).content.decode())
    data = page.data
    data["first_name"] = ["Changed"]
    data.update({
        "zwid": ["999999"],
        "zwid_verified": [""],
        "zwid_verification_method": ["admin"],
        "zwid_verified_at": ["2020-01-01 00:00:00"],
        "_save": ["Save"],
    })

    response = client.post(url, data)

    assert response.status_code == 302, response.context["adminform"].form.errors if response.context else ""
    rider.refresh_from_db()
    assert rider.first_name == "Changed"  # the save really happened
    assert rider.zwid == 4242
    assert rider.zwid_verified is True
    assert rider.zwid_verification_method == "zauth"
    assert rider.zwid_verified_at == before


@pytest.mark.django_db
def test_the_add_form_ignores_a_posted_verification(client, superuser, user_model):
    client.force_login(superuser)

    client.post(
        reverse("admin:accounts_user_add"),
        {
            "username": "brand-new",
            "password1": "a-long-Unusual-passphrase-42",
            "password2": "a-long-Unusual-passphrase-42",
            "usable_password": "true",
            "zwid": "999999",
            "zwid_verified": "on",
            "zwid_verification_method": "admin",
            "_save": "Save",
        },
    )

    created = user_model.objects.get(username="brand-new")
    assert created.zwid is None
    assert created.zwid_verified is False
    assert created.zwid_verification_method == ""

"""``has_jersey`` must stay writable from the admin, because nothing else writes it.

The bulk CSV on ``/team/membership-review/`` was its only write path in the whole application
-- it was in no admin fieldset and no form -- while three surfaces kept reading it: the
``/team/discord-review/`` filter, that page's CSV export, and a ``data_connection`` Sheets
field. Retiring the page without this would have left a field displayed and filtered by three
places and settable by nobody, frozen at whatever the last upload wrote.

``User.team_kit`` is its richer successor and the two have never been reconciled, so this
guards the write path rather than asserting the field has a future.
"""

import pytest
from django.contrib.admin.sites import site
from django.urls import reverse

from apps.accounts.models import User


def _fieldset_fields(user):
    """Flatten the admin change form's fieldsets to a set of field names.

    Args:
        user: The user being edited.

    Returns:
        Every field name the change form renders.

    """
    admin = site._registry[User]
    fields = set()
    for _name, options in admin.get_fieldsets(None, user):
        fields.update(options["fields"])
    return fields


@pytest.mark.django_db
def test_the_change_form_offers_has_jersey(user):
    """One rider at a time: the field is on the change form."""
    assert "has_jersey" in _fieldset_fields(user)


@pytest.mark.django_db
def test_the_add_form_does_not(user_model):
    """The kit section is for editing a rider, not creating one."""
    admin = site._registry[User]

    names = [name for name, _ in admin.get_fieldsets(None, None)]

    assert "Team kit" not in names


@pytest.mark.django_db
def test_a_superuser_can_flip_it_in_bulk_from_the_changelist(client, superuser):
    """In bulk: what the CSV did, now inline. This is the write path that must not be lost."""
    rider = User.objects.create_user(username="jersey-rider", email="jr@example.test")
    assert rider.has_jersey is False
    client.force_login(superuser)

    response = client.post(
        reverse("admin:accounts_user_changelist"),
        {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(rider.pk),
            "form-0-has_jersey": "on",
            "_save": "Save",
        },
    )

    assert response.status_code in {200, 302}
    rider.refresh_from_db()
    assert rider.has_jersey is True

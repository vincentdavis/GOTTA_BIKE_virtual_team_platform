"""Changes to site settings must leave a trace.

The permission mappings decide who holds every permission in the app, including who may view
verification photographs. An admin can widen their own access through this form, and until
now that left no record at all -- no actor, no before, no after. A privacy audit flagged it.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_permission_change_is_logged_with_actor_and_before_after(admin_authed_client, app_admin):
    with patch("apps.accounts.views.logfire.info") as log:
        admin_authed_client.post(
            reverse("config_section_update", kwargs={"section_key": "permission_mappings"}),
            {"PERM_APP_ADMIN_ROLES": ["111", "222"]},
        )

    calls = [c for c in log.call_args_list if c.args and c.args[0] == "Site setting changed"]
    assert calls, "changing a permission mapping recorded nothing"
    kw = calls[0].kwargs
    assert kw["changed_by_id"] == app_admin.pk
    assert kw["setting"] == "PERM_APP_ADMIN_ROLES"
    assert kw["is_permission_mapping"] is True
    assert "old_value" in kw and "new_value" in kw


@pytest.mark.django_db
def test_unchanged_settings_are_not_logged(admin_authed_client):
    """Resaving a form without edits should not fill the log with noise."""
    url = reverse("config_section_update", kwargs={"section_key": "permission_mappings"})
    admin_authed_client.post(url, {"PERM_APP_ADMIN_ROLES": ["111"]})

    with patch("apps.accounts.views.logfire.info") as log:
        admin_authed_client.post(url, {"PERM_APP_ADMIN_ROLES": ["111"]})

    changed = [c for c in log.call_args_list if c.args and c.args[0] == "Site setting changed"]
    assert not changed


@pytest.mark.django_db
def test_secret_values_are_not_written_to_the_log(admin_authed_client):
    """The point is an audit trail, not a second copy of every credential."""
    with patch("apps.accounts.views.logfire.info") as log:
        admin_authed_client.post(
            reverse("config_section_update", kwargs={"section_key": "strava"}),
            {"STRAVA_CLIENT_SECRET": "a-real-looking-secret-value"},
        )

    for call in log.call_args_list:
        if call.args and call.args[0] == "Site setting changed":
            assert "a-real-looking-secret-value" not in str(call.kwargs)

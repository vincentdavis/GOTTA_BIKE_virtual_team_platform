"""The Get Roles button explains itself: it is a read-only cache refresh.

The Discord call is patched at the ``apps.events.views`` boundary, so no test here
makes a real HTTP request.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup
from apps.events.views import SYNC_ROLES_EXPLAINER


@pytest.fixture
def event(db) -> Event:
    """Build a visible, currently-running event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today - timedelta(days=1), end_date=today + timedelta(days=7), visible=True,
    )


@pytest.fixture
def rider(user_model, event):
    """Register a rider with a Discord id so the sync has someone to fetch.

    Returns:
        The rider user.

    """
    user = user_model.objects.create_user(
        username="rider", email="rider@example.test", discord_id="123456",
    )
    EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)
    return user


@pytest.mark.django_db
def test_success_message_explains_the_action_is_read_only(client, event, rider, event_admin, monkeypatch) -> None:
    monkeypatch.setattr("apps.events.views.sync_user_discord_roles", lambda user: True)
    client.force_login(event_admin)

    resp = client.post(reverse("events:sync_event_roles", args=[event.pk]), follow=True)
    body = " ".join(str(m) for m in resp.context["messages"])

    assert "Synced Discord roles for 1 riders" in body
    assert SYNC_ROLES_EXPLAINER in body
    assert "read-only" in body
    assert "nothing was changed in Discord" in body


@pytest.mark.django_db
def test_partial_failure_still_explains_the_action(client, event, rider, event_admin, monkeypatch) -> None:
    """The explanation matters most when something went wrong and the admin is unsure."""
    monkeypatch.setattr("apps.events.views.sync_user_discord_roles", lambda user: False)
    client.force_login(event_admin)

    resp = client.post(reverse("events:sync_event_roles", args=[event.pk]), follow=True)
    body = " ".join(str(m) for m in resp.context["messages"])

    assert "1 failed" in body
    assert SYNC_ROLES_EXPLAINER in body


@pytest.mark.django_db
def test_both_buttons_say_read_only_before_it_is_clicked(client, event, user_model) -> None:
    """The button reads like it might push roles outward, so both entry points say so.

    Viewing Discord Roles needs `assign_roles`; event_admin alone can POST the sync but
    cannot open that page.
    """
    admin = user_model.objects.create_user(
        username="ra", email="ra@example.test",
        permission_overrides={"team_member": True, "event_admin": True, "assign_roles": True},
    )
    client.force_login(admin)

    for url in (reverse("events:discord_roles", args=[event.pk]),
                reverse("events:squad_assign_page", args=[event.pk])):
        body = client.get(url).content.decode()
        assert "Get Roles" in body
        assert "Sync Roles" not in body      # the label it used to have, and the one that misled
        assert "Read-only" in body
        assert "Nothing is changed in Discord" in body

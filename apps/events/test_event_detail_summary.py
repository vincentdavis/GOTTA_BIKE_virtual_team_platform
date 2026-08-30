"""The summary counts under the event description, and where Add members lives.

The counts come from one aggregate rather than three round trips, and they follow the same
gate as the signup table: aggregate rather than personal, but there is no reason to show
figures summarising a list the viewer cannot open. The squad count is ungated, because the
squads themselves are listed further down the page for everyone.
"""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad


@pytest.fixture
def event(db) -> Event:
    """Build a visible, currently-running event with signups on show.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="Summer Series",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=7),
        visible=True,
        show_signups=True,
    )


def _rider(user_model, name: str, gender: str):
    return user_model.objects.create_user(
        username=name,
        email=f"{name}@example.test",
        gender=gender,
        permission_overrides={"team_member": True},
    )


def _counts(body: str) -> dict[str, int]:
    """Pull the number/label pairs out of the summary list.

    Args:
        body: The rendered page.

    Returns:
        Mapping of label to count.

    """
    block = re.search(r"<dl[^>]*>(.*?)</dl>", body, re.S)
    if not block:
        return {}
    pairs = re.findall(r'<dd[^>]*>\s*(\d+)\s*</dd>\s*<dt[^>]*>\s*([a-z]+)\s*</dt>', block.group(1))
    return {label: int(n) for n, label in pairs}


@pytest.mark.django_db
def test_counts_reflect_signups_and_squads(client, event, team_member, user_model):
    Squad.objects.create(event=event, name="A")
    Squad.objects.create(event=event, name="B")
    for name, gender in [("m1", "male"), ("m2", "male"), ("f1", "female"), ("x1", "other")]:
        EventSignup.objects.create(event=event, user=_rider(user_model, name, gender))
    client.force_login(team_member)

    counts = _counts(client.get(reverse("events:event_detail", args=[event.pk])).content.decode())

    assert counts["signups"] == 4
    assert counts["male"] == 2
    assert counts["female"] == 1  # "other" is counted in the total but has no column
    assert counts["squads"] == 2


@pytest.mark.django_db
def test_singular_labels(client, event, team_member, user_model):
    Squad.objects.create(event=event, name="A")
    EventSignup.objects.create(event=event, user=_rider(user_model, "m1", "male"))
    client.force_login(team_member)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "signup<" in body.replace("</dt>", "<")
    assert _counts(body) == {"signup": 1, "male": 1, "female": 0, "squad": 1}


@pytest.mark.django_db
def test_counts_show_even_when_the_signup_list_is_hidden(client, event, team_member, user_model):
    """The figures are aggregate, so they do not follow show_signups.

    A member on an event with the list hidden still sees the totals; what they cannot do is
    expand the list to see who those signups are.
    """
    event.show_signups = False
    event.save(update_fields=["show_signups"])
    Squad.objects.create(event=event, name="A")
    EventSignup.objects.create(event=event, user=_rider(user_model, "m1", "male"))
    client.force_login(team_member)

    counts = _counts(client.get(reverse("events:event_detail", args=[event.pk])).content.decode())

    assert counts == {"signup": 1, "male": 1, "female": 0, "squad": 1}


@pytest.mark.django_db
def test_add_members_is_only_in_the_gear_menu(client, event, superuser):
    """It used to be a standalone button below the squads; the dialog it opens stays put."""
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert "Add members" in body
    assert ">\n            Add Members\n          </button>" not in body  # the old button
    assert 'id="add_members_modal"' in body
    # One trigger, in the menu.
    assert body.count("add_members_modal').showModal()") == 1

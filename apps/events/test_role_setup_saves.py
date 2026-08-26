"""Role Setup must actually save, and must say so when it does not.

A stale role id -- one left in a list after the event's prefixes changed, or seeded by
migration 0068 -- used to make the whole page un-saveable. The JS filter hides an
off-prefix role, the browser resubmits it because it is still checked, and clean()
rejects the form. An unrelated edit elsewhere on the page then looked saved and was not.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event
from apps.team.models import DiscordRole

CPT1, CPT2, STALE = "601", "602", "700"


@pytest.fixture
def event(db) -> Event:
    """Build an event whose region list carries a role that is off its prefixes.

    Returns:
        The event.

    """
    DiscordRole.objects.create(role_id=CPT1, name="$ Div 1 Captain", position=1)
    DiscordRole.objects.create(role_id=CPT2, name="$ Div 2 Captain", position=2)
    DiscordRole.objects.create(role_id=STALE, name="/APAC B", position=3)
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7),
        prefixes=["$"], captain_role_ids=[CPT1, CPT2], region_role_ids=[STALE],
    )


def _checked(client, event, field):
    """Collect what the page would submit for one checkbox list, as the browser would.

    Returns:
        The ids rendered checked on a fresh GET.

    """
    response = client.get(reverse("events:event_role_setup", args=[event.pk]))
    assert response.status_code == 200
    return list(response.context["form"].initial.get(field) or [])


@pytest.mark.django_db
def test_a_stale_off_prefix_id_is_not_resubmitted(client, event, superuser) -> None:
    """It renders unchecked, so the browser never sends it back."""
    client.force_login(superuser)

    assert STALE not in _checked(client, event, "region_role_ids")


@pytest.mark.django_db
def test_unchecking_a_captain_role_sticks(client, event, superuser) -> None:
    """The bug: a stale id elsewhere on the page made every save fail silently."""
    client.force_login(superuser)
    captains = _checked(client, event, "captain_role_ids")
    regions = _checked(client, event, "region_role_ids")
    captains.remove(CPT2)

    response = client.post(
        reverse("events:event_role_setup", args=[event.pk]),
        {"prefixes": ["$"], "head_captain_role_id": "0", "event_role": "0",
         "captain_role_ids": captains, "region_role_ids": regions},
    )

    assert response.status_code == 302, "form was rejected instead of saved"
    event.refresh_from_db()
    assert event.captain_role_ids == [CPT1]


@pytest.mark.django_db
def test_the_stale_id_heals_on_the_next_save(client, event, superuser) -> None:
    """The list cleans itself rather than needing a migration to prune it."""
    client.force_login(superuser)
    regions = _checked(client, event, "region_role_ids")

    client.post(
        reverse("events:event_role_setup", args=[event.pk]),
        {"prefixes": ["$"], "head_captain_role_id": "0", "event_role": "0",
         "captain_role_ids": _checked(client, event, "captain_role_ids"),
         "region_role_ids": regions},
    )

    event.refresh_from_db()
    assert event.region_role_ids == []


@pytest.mark.django_db
def test_emptying_a_list_entirely_sticks(client, event, superuser) -> None:
    """Unchecking everything submits nothing for the field, which must mean "none"."""
    client.force_login(superuser)

    client.post(
        reverse("events:event_role_setup", args=[event.pk]),
        {"prefixes": ["$"], "head_captain_role_id": "0", "event_role": "0"},
    )

    event.refresh_from_db()
    assert event.captain_role_ids == []
    assert event.region_role_ids == []


@pytest.mark.django_db
def test_a_rejected_save_says_so(client, event, superuser) -> None:
    """A field error can sit far below three long lists; the reload must not read as success."""
    client.force_login(superuser)

    response = client.post(
        reverse("events:event_role_setup", args=[event.pk]),
        {"prefixes": ["$"], "head_captain_role_id": "0", "event_role": "0",
         "captain_role_ids": ["999999"]},
        follow=True,
    )

    assert any("was not saved" in str(m) for m in response.context["messages"])

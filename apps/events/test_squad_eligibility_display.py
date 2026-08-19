"""The squad's women's Zwift category range has to be visible where the other two are.

Regression test. The value always saved correctly, but it had no display surface at
all -- no badge on the squad card, no row in the Category Range panel -- so admins who
set it saw nothing change and reasonably concluded the edit had been dropped. Worse,
the panel's own visibility guard only tested the ZR and ZP fields, so a squad with
*only* a women's range rendered no panel whatsoever.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad


@pytest.fixture
def event(db) -> Event:
    """Build a visible event that squads can hang off.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL",
        start_date=today,
        end_date=today + timedelta(days=7),
        visible=True,
    )


@pytest.mark.django_db
def test_manage_page_shows_the_womens_range_alongside_the_others(client, event, event_admin) -> None:
    Squad.objects.create(
        event=event,
        name="Synthesis",
        min_zwift_category="C",
        max_zwift_category="B",
        min_womens_zwift_category="D",
        max_womens_zwift_category="A",
    )
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    assert "Zwift Category" in body
    assert "Women's Zwift Category" in body
    assert "D" in body and "A" in body


@pytest.mark.django_db
def test_womens_range_alone_still_renders_the_panel(client, event, event_admin) -> None:
    """The guard used to test only the ZR/ZP fields, hiding the whole panel."""
    Squad.objects.create(
        event=event,
        name="Womens Only",
        min_womens_zwift_category="D",
        max_womens_zwift_category="B",
    )
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    assert "Category Range" in body
    assert "Women's Zwift Category" in body


@pytest.mark.django_db
def test_squad_card_carries_a_womens_badge(client, event, event_admin) -> None:
    """`_squad_panel.html` badges ZR and ZP; the women's range needs one too."""
    Squad.objects.create(
        event=event,
        name="Synthesis",
        min_zwift_category="C",
        max_zwift_category="B",
        min_womens_zwift_category="D",
        max_womens_zwift_category="A",
    )
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_assign_page", args=[event.pk])).content.decode()

    assert "ZP: C-B" in body
    assert "ZP-W: D-A" in body

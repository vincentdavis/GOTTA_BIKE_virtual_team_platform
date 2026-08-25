"""The squads table's column selector.

The Squads section used to carry a single "Columns" button that actually controlled the
*rider* columns inside an expanded squad, so nothing it offered matched the table it sat
above. There are now two selectors, and the squad one drives squad-level fields.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils.html import escape

from apps.events.models import Event, Squad


@pytest.fixture
def event(db) -> Event:
    """Build a visible event that requires a squad gender.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL Season 5", start_date=today, end_date=today + timedelta(days=30),
        visible=True, squad_gender_required=True,
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad with enforced requirements and category bounds.

    Returns:
        The squad.

    """
    return Squad.objects.create(
        event=event, name="Div 1", squad_timezone="Europe/London", gender="Female",
        enforce_gender=True,
        min_zwift_category="D", max_zwift_category="B",
        enforce_min_zwift_category=True, enforce_max_zwift_category=True,
        min_womens_zwift_category="C", max_womens_zwift_category="A",
        min_zwift_racing_category="Gold", max_zwift_racing_category="Ruby",
    )


def _page(client, event):
    """Load the event detail page.

    Returns:
        The decoded response body.

    """
    response = client.get(reverse("events:event_detail", args=[event.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_the_squad_selector_offers_squad_fields(client, event, squad, event_admin) -> None:
    """Every squad-level column must be reachable from the squad selector."""
    client.force_login(event_admin)
    body = _page(client, event)

    for col in ("sqf_tz", "sqf_gender", "sqf_enforced", "sqf_zwift_cat", "sqf_womens_cat",
                "sqf_zr_cat", "sqf_members", "sqf_captain", "sqf_vice"):
        assert f'data-scol="{col}"' in body, col


@pytest.mark.django_db
def test_the_two_selectors_are_distinguishable(client, event, squad, event_admin) -> None:
    """A single "Columns" button was the whole problem -- they must be named apart."""
    client.force_login(event_admin)
    body = _page(client, event)

    assert "Squad columns" in body
    assert "Rider columns" in body


@pytest.mark.django_db
def test_rider_columns_keep_their_own_attribute_namespace(client, event, squad, event_admin) -> None:
    """The togglers select globally by attribute, so a shared key would cross-wire them."""
    client.force_login(event_admin)
    body = _page(client, event)

    # Rider columns stay on data-col; squad columns are data-scol. No squad key may leak
    # into the rider namespace, or toggling one would hide the other.
    assert 'data-col="sqf_' not in body
    assert 'data-scol="sq_name"' not in body


@pytest.mark.django_db
def test_enforced_requirements_render_from_the_shared_summary(client, event, squad, event_admin) -> None:
    """Reuses Squad.enforcement_summary, so it cannot drift from the squad manage page."""
    client.force_login(event_admin)
    body = _page(client, event)

    assert "Enforced Requirements" in body
    for label in squad.enforcement_summary:
        assert escape(label) in body


@pytest.mark.django_db
def test_a_squad_with_nothing_enforced_says_so(client, event, event_admin) -> None:
    """An empty cell would read as missing data rather than a deliberate "anyone may join"."""
    Squad.objects.create(event=event, name="Open", squad_timezone="UTC")
    client.force_login(event_admin)

    body = _page(client, event)

    assert "None" in body


@pytest.mark.django_db
def test_category_ranges_render_for_all_three_scales(client, event, squad, event_admin) -> None:
    """Zwift, Women's Zwift and ZR all use the shared range partial."""
    client.force_login(event_admin)
    body = _page(client, event)

    assert "Women&#x27;s Zwift Category" in body or "Women's Zwift Category" in body
    assert "Gold" in body and "Ruby" in body  # ZR bounds


@pytest.mark.django_db
def test_a_heavily_enforced_squad_is_capped_with_the_rest_on_hover(client, event, event_admin) -> None:
    """Eight badges in one cell towers over the neighbouring rows."""
    Squad.objects.create(
        event=event, name="Strict", gender="Female", enforce_gender=True,
        min_zwift_category="D", max_zwift_category="B",
        enforce_min_zwift_category=True, enforce_max_zwift_category=True,
        min_womens_zwift_category="C", max_womens_zwift_category="A",
        enforce_min_womens_zwift_category=True, enforce_max_womens_zwift_category=True,
        min_zwift_racing_category="Gold", max_zwift_racing_category="Ruby",
        enforce_min_zwift_racing_category=True, enforce_max_zwift_racing_category=True,
        require_zauth=True,
    )
    squad = Squad.objects.get(name="Strict")
    assert len(squad.enforcement_summary) > 2
    client.force_login(event_admin)

    body = _page(client, event)

    overflow = len(squad.enforcement_summary) - 2
    assert f"+{overflow}" in body
    # Nothing is lost -- the hidden labels still ship in the tooltip, HTML-escaped.
    for label in squad.enforcement_summary:
        assert escape(label) in body


@pytest.mark.django_db
def test_the_heading_carries_the_squad_count(client, event, squad, event_admin) -> None:
    """Saves counting rows to know how many squads an event has."""
    Squad.objects.create(event=event, name="Div 2")
    client.force_login(event_admin)

    assert "Squads (2)" in _page(client, event)


@pytest.mark.django_db
def test_expand_all_is_offered_only_when_there_are_squads(client, event, squad, event_admin) -> None:
    """A control that toggles nothing is noise."""
    client.force_login(event_admin)
    assert 'id="squad-expand-all"' in _page(client, event)

    squad.delete()
    assert 'id="squad-expand-all"' not in _page(client, event)


@pytest.mark.django_db
def test_members_sits_immediately_after_the_squad_name(client, event, squad, event_admin) -> None:
    """Member count is the first thing you want off a squad row, so it leads the data."""
    client.force_login(event_admin)
    body = _page(client, event)

    head = body[body.index('id="squad-table"'):]
    assert head.index('data-scol="sqf_members"') < head.index('data-scol="sqf_tz"')


@pytest.mark.django_db
def test_members_is_visible_without_touching_the_selector(client, event, squad, event_admin) -> None:
    """Members is a default column, not one you have to go and switch on."""
    client.force_login(event_admin)
    body = _page(client, event)

    defaults = body[body.index("event_squad_field_cols"):]
    assert "sqf_members: true" in defaults[:400]


@pytest.mark.django_db
def test_squad_gender_shows_even_when_signups_do_not_ask_for_it(client, event, event_admin) -> None:
    """Event.squad_gender_required governs the rider's signup preference, not Squad.gender.

    The squads table used to hide its Squad Gender column behind that flag, so an event
    that never asks riders for a preference showed no gender for its squads either --
    even though each squad has one set on the squad form.
    """
    event.squad_gender_required = False
    event.save(update_fields=["squad_gender_required"])
    Squad.objects.create(event=event, name="Women's Div", gender="Female")
    client.force_login(event_admin)

    body = _page(client, event)

    assert 'data-scol="sqf_gender"' in body
    assert "Female" in body

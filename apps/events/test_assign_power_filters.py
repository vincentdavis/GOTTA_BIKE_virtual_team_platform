"""zFTP / zMAP columns and the assign-page min/max filters.

Both surfaces read the local mirror on ``User`` (see apps/accounts/tasks.py) rather
than calling the zauth service per rider, which would be one HTTP round-trip per row.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup


@pytest.fixture
def event(db) -> Event:
    """Build a visible event riders can sign up to.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True,
    )


@pytest.fixture
def powered_rider(user_model, event):
    """Register a rider with mirrored zFTP/zMAP.

    248W at 66kg is 3.76 W/kg; 340W is 5.15 W/kg.

    Returns:
        The rider.

    """
    rider = user_model.objects.create_user(
        username="powered",
        email="powered@example.test",
        first_name="Pow",
        last_name="Ered",
        z_ftp=Decimal("248.0"),
        z_map=Decimal("340.0"),
        z_metrics_weight_grams=66000,
    )
    EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED)
    return rider


@pytest.fixture
def bare_rider(user_model, event):
    """Register a rider with no zauth metrics at all.

    Returns:
        The rider.

    """
    rider = user_model.objects.create_user(
        username="bare", email="bare@example.test", first_name="Bare", last_name="Rider",
    )
    EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED)
    return rider


@pytest.mark.django_db
def test_assign_rows_carry_both_units(client, event, powered_rider, bare_rider, event_admin) -> None:
    """The filter switches units client-side, so each row must ship both."""
    client.force_login(event_admin)
    body = client.get(reverse("events:squad_assign_page", args=[event.pk])).content.decode()

    assert 'data-zftp-w="248.0"' in body
    assert 'data-zftp-wkg="3.76"' in body
    assert 'data-zmap-w="340.0"' in body
    assert 'data-zmap-wkg="5.15"' in body
    # a rider with no metrics ships empty attributes, which the filter treats as "no match"
    assert 'data-zftp-w=""' in body
    assert 'data-zftp-wkg=""' in body


@pytest.mark.django_db
def test_assign_page_renders_the_filter_controls(client, event, powered_rider, event_admin) -> None:
    client.force_login(event_admin)
    body = client.get(reverse("events:squad_assign_page", args=[event.pk])).content.decode()

    for control in ("filter-power-unit", "filter-zftp-min", "filter-zftp-max",
                    "filter-zmap-min", "filter-zmap-max"):
        assert control in body, control


@pytest.mark.django_db
def test_signup_table_shows_the_metrics(client, event, powered_rider, bare_rider, event_admin) -> None:
    client.force_login(event_admin)
    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    assert 'data-col="zftp"' in body
    assert 'data-col="zmap"' in body
    assert 'data-sort-value="248.0"' in body   # watts sorts, W/kg rides along underneath
    assert "3.76" in body


@pytest.mark.django_db
def test_assign_page_shows_how_stale_the_mirror_is(client, event, powered_rider, event_admin) -> None:
    """The mirror's age is shown on the page.

    A stopped db_worker should read as an old timestamp, not as riders silently
    failing the power bounds.
    """
    from django.utils import timezone

    powered_rider.z_metrics_updated_at = timezone.now() - timedelta(days=3)
    powered_rider.save(update_fields=["z_metrics_updated_at"])
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_assign_page", args=[event.pk])).content.decode()

    assert "mirrored from Zwift" in body
    assert "3\xa0days ago" in body or "3 days ago" in body


@pytest.mark.django_db
def test_assign_page_says_what_to_do_when_nothing_is_mirrored(client, event, bare_rider, event_admin) -> None:
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_assign_page", args=[event.pk])).content.decode()

    assert "No zFTP/zMAP data yet" in body
    assert "Background Tasks" in body


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["squad_assign_page", "manage_roles"])
def test_back_link_goes_to_manage_squads(client, event, user_model, url_name) -> None:
    """Both pages are reached from Manage Squads, so Back belongs there.

    manage-roles needs `assign_roles` to view, which event_admin alone does not grant.
    """
    admin = user_model.objects.create_user(
        username="ra", email="ra@example.test",
        permission_overrides={"team_member": True, "event_admin": True, "assign_roles": True},
    )
    client.force_login(admin)

    body = client.get(reverse(f"events:{url_name}", args=[event.pk])).content.decode()

    assert reverse("events:squad_manage", args=[event.pk]) in body
    assert "Back to Manage Squads" in body

"""Profile cards show when their data was last updated.

The own-profile page still renders the ZwiftPower and Zwift Racing pair. The public profile
no longer does: its three source cards were replaced by one consolidated card fed by
RiderProfile, which carries a single fetched_at. So the two pages legitimately show a
different number of dates, and each is asserted against what it actually renders.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.rider_data.models import RiderProfile
from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftracing.models import ZRRider


@pytest.fixture
def verified_rider(user_model, db):
    """Build a verified rider with both ZP and ZR rows.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username="rider", email="rider@example.test", first_name="Alice", last_name="Rider",
        zwid=555, zwid_verified=True, permission_overrides={"team_member": True},
    )
    ZPTeamRiders.objects.create(zwid=555, name="Alice Rider", div=30, ftp=250, rank=1234)
    ZRRider.objects.create(zwid=555, name="Alice Rider", race_current_category="B")
    return user


def _today() -> str:
    """Today's date as the templates render it.

    Returns:
        ``YYYY-MM-DD``.

    """
    return timezone.localtime(timezone.now()).strftime("%Y-%m-%d")


@pytest.mark.django_db
def test_own_profile_cards_show_the_update_date(client, verified_rider, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: False)
    client.force_login(verified_rider)

    body = client.get(reverse("accounts:profile")).content.decode()

    assert body.count(f"Updated {_today()}") >= 2


@pytest.mark.django_db
def test_public_profile_card_shows_the_update_date(client, verified_rider, team_member) -> None:
    """One card, one date -- sourced from RiderProfile.fetched_at, not the ZP/ZR rows."""
    # Needs something the card actually displays: a row carrying only a name renders the
    # "not synced" state, since `name` is never shown on the card.
    RiderProfile.objects.create(
        zwid=555, name="Alice Rider", category_open="B", ftp=250,
        fetched_at=timezone.now(), last_requested_at=timezone.now(),
    )
    client.force_login(team_member)

    body = client.get(reverse("accounts:public_profile", args=[verified_rider.pk])).content.decode()

    assert body.count(f"Updated {_today()}") == 1


@pytest.mark.django_db
def test_no_date_is_shown_without_the_underlying_row(client, user_model, team_member, monkeypatch) -> None:
    """A rider with no RiderProfile row must not grow an empty "Updated" line.

    Asserted against the cards' ISO format specifically: the page footer carries its own
    "Updated 2026/08/20" last-deploy stamp, which a looser check matches.
    """
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: None)
    bare = user_model.objects.create_user(
        username="bare", email="bare@example.test", zwid=999, zwid_verified=True,
    )
    client.force_login(team_member)

    body = client.get(reverse("accounts:public_profile", args=[bare.pk])).content.decode()

    assert f"Updated {_today()}" not in body

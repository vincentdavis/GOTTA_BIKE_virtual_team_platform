"""The Update button on the rider card, and the poll that collects its result.

Pressing Update cannot return new data in its own response -- zauth answers the trigger
immediately and fetches on its own worker -- so the card asks for a fresh copy of itself a few
times afterwards. That chain is carried in the URL rather than in a server-side "in progress"
flag, because the cache is per-process (LocMem) and a flag written by one web worker would be
invisible to the next request. The tests below pin both ends of it: that it starts, and that
it stops.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.views import RIDER_CARD_RECHECKS
from apps.rider_data.models import RiderProfile

QUEUED = {"reached": True, "statuses": {"zwiftpower": "queued", "zwiftracing": "queued"}, "queued": ["zwiftracing"]}
THROTTLED = {"reached": True, "statuses": {"zwiftpower": "skipped", "zwiftracing": "skipped"}, "queued": []}
UNREACHABLE = {"reached": False, "statuses": {"zwiftpower": "unknown", "zwiftracing": "unknown"}, "queued": []}


@pytest.fixture
def rider(user_model, db):
    """Build a verified rider a teammate can look at.

    Returns:
        The rider.

    """
    return user_model.objects.create_user(
        username="rider-refresh",
        email="rider-refresh@example.test",
        zwid=4242,
        zwid_verified=True,
        permission_overrides={"team_member": True},
    )


@pytest.fixture
def profile(rider):
    """Store the cached row behind the rider's card.

    Returns:
        The RiderProfile row.

    """
    now = timezone.now()
    return RiderProfile.objects.create(
        zwid=rider.zwid,
        name="Ada Racer",
        velo=1580.0,
        payload={"zwid": rider.zwid},
        fetched_at=now,
        last_requested_at=now,
    )


def _url(rider):
    """Build the refresh URL for a rider.

    Args:
        rider: The rider whose card is refreshed.

    Returns:
        The URL.

    """
    return reverse("accounts:refresh_rider_data", args=[rider.pk])


# --- the button ----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_button_and_its_tooltip_are_on_the_public_profile(client, team_member, rider, profile):
    """The tooltip is the only place the ~60s wait is explained, so it is part of the feature."""
    client.force_login(team_member)
    body = client.get(reverse("accounts:public_profile", args=[rider.pk])).content.decode()

    assert f'hx-post="{_url(rider)}"' in body
    assert "Checks ZwiftPower and ZwiftRacing for updates. Can take about 60 seconds." in body
    # A card nobody has asked to refresh must sit still rather than poll the site.
    assert "?check=" not in body


@pytest.mark.django_db
def test_the_button_is_on_the_rider_own_edit_page(client, rider, profile):
    """Same partial, same action -- a rider should not have to view themselves as a teammate to refresh."""
    client.force_login(rider)
    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert _url(rider) in body


@pytest.mark.django_db
def test_the_button_shows_for_a_rider_we_hold_no_data_for(client, team_member, rider):
    """"Not synced yet" is the state the button most needs to exist in; hiding it makes a dead end."""
    client.force_login(team_member)
    body = client.get(reverse("accounts:public_profile", args=[rider.pk])).content.decode()

    assert "not been synced" in body
    assert _url(rider) in body


@pytest.mark.django_db
def test_no_button_for_an_unverified_zwift_account(client, team_member, user_model):
    """An unverified zwid is a number the rider typed; refreshing it would assert an identity."""
    unverified = user_model.objects.create_user(
        username="unverified-rider",
        email="unverified-rider@example.test",
        zwid=9999,
        zwid_verified=False,
        permission_overrides={"team_member": True},
    )
    client.force_login(team_member)
    body = client.get(reverse("accounts:public_profile", args=[unverified.pk])).content.decode()

    assert _url(unverified) not in body


# --- pressing it ---------------------------------------------------------------------


@pytest.mark.django_db
def test_pressing_update_triggers_the_refresh_and_says_what_is_happening(client, team_member, rider, profile):
    """A rider watching an unchanged card needs to be told the work is elsewhere, not finished."""
    client.force_login(team_member)
    with patch("apps.accounts.views.request_profile_refresh", return_value=QUEUED) as trigger:
        body = client.post(_url(rider)).content.decode()

    trigger.assert_called_once_with(4242)
    assert "Checking ZwiftRacing for new data" in body
    assert f"{_url(rider)}?check=1" in body


@pytest.mark.django_db
def test_a_throttled_refresh_is_reported_honestly(client, team_member, rider, profile):
    """Nothing was queued upstream, and saying "checking ZwiftPower" would be a lie."""
    client.force_login(team_member)
    with patch("apps.accounts.views.request_profile_refresh", return_value=THROTTLED):
        body = client.post(_url(rider)).content.decode()

    assert "checked very recently" in body


@pytest.mark.django_db
def test_an_unreachable_service_stops_the_chain_instead_of_polling_at_it(client, team_member, rider, profile):
    """Nothing is in flight, so re-checking would be three more requests spelling the same error."""
    client.force_login(team_member)
    with patch("apps.accounts.views.request_profile_refresh", return_value=UNREACHABLE):
        body = client.post(_url(rider)).content.decode()

    assert "Could not reach the rider data service" in body
    assert "?check=" not in body


@pytest.mark.django_db
def test_an_unverified_rider_cannot_be_refreshed_by_a_tampered_post(client, team_member, user_model):
    """No button is rendered in this state, so reaching the view means the request was hand-made."""
    unverified = user_model.objects.create_user(
        username="unverified-post",
        email="unverified-post@example.test",
        zwid=9999,
        zwid_verified=False,
    )
    client.force_login(team_member)
    with patch("apps.accounts.views.request_profile_refresh") as trigger:
        body = client.post(_url(unverified)).content.decode()

    trigger.assert_not_called()
    assert "no verified Zwift account" in body


# --- the poll ------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_recheck_reads_the_card_without_triggering_anything(client, team_member, rider, profile):
    """The poll collects a result; triggering again on every tick would hammer the service."""
    client.force_login(team_member)
    with patch("apps.accounts.views.request_profile_refresh") as trigger:
        body = client.get(_url(rider), {"check": "1"}).content.decode()

    trigger.assert_not_called()
    assert "Racing &amp; Performance" in body  # the whole card came back, not just a status line
    assert f"{_url(rider)}?check=2" in body


@pytest.mark.django_db
def test_the_chain_stops_after_the_last_recheck(client, team_member, rider, profile):
    """Left open, this card would poll the site for as long as the tab stayed open."""
    client.force_login(team_member)
    body = client.get(_url(rider), {"check": str(RIDER_CARD_RECHECKS)}).content.decode()

    assert "?check=" not in body


@pytest.mark.django_db
def test_a_junk_check_value_does_not_500(client, team_member, rider, profile):
    """It arrives in a URL, so it is attacker-controlled text, not a trusted integer."""
    client.force_login(team_member)
    assert client.get(_url(rider), {"check": "banana"}).status_code == 200
    assert client.get(_url(rider), {"check": "-5"}).status_code == 200


# --- who may press it ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_rider_may_refresh_themselves_without_team_member(client, user_model):
    """The edit page is reachable without team_member, so the card on it has to work there."""
    loner = user_model.objects.create_user(
        username="loner",
        email="loner@example.test",
        zwid=1234,
        zwid_verified=True,
    )
    client.force_login(loner)
    with patch("apps.accounts.views.request_profile_refresh", return_value=QUEUED) as trigger:
        assert client.post(_url(loner)).status_code == 200
    trigger.assert_called_once()


@pytest.mark.django_db
def test_refreshing_a_teammate_needs_team_member(client, user, rider):
    """Same gate as viewing their profile at all -- the button must not be a way around it."""
    client.force_login(user)
    with patch("apps.accounts.views.request_profile_refresh") as trigger:
        assert client.post(_url(rider)).status_code == 403
    trigger.assert_not_called()


@pytest.mark.django_db
def test_refreshing_requires_a_login(client, rider):
    """Anonymous callers would otherwise be able to drive zauth fetches for arbitrary riders."""
    with patch("apps.accounts.views.request_profile_refresh") as trigger:
        assert client.post(_url(rider)).status_code == 302
    trigger.assert_not_called()

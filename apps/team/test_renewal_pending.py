"""A renewal awaiting review answers its expiry warning: no banner and no DM until it is decided.

The rider's banner and the expiry DM both ask them to renew. Once they have, the ask is noise
and invites a second submission, so a pending record of the same type -- or of either weight
type, where the category accepts either -- quiets it, but only when it would cover longer than
the record covering the type now. A rejection or a deletion brings the warning back. Guards
``services.renewal_pending_types`` and its two consumers, and the submit flow that takes the
banner down in place.
"""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.team.context_processors import expiring_verifications
from apps.team.forms import RaceReadyRecordForm
from apps.team.models import RaceReadyRecord
from apps.team.tasks import warn_expiring_verifications

PENDING = RaceReadyRecord.Status.PENDING
BANNER_UP = 'id="expiring-verification-banner" class="bg-warning'
BANNER_EMPTY = 'id="expiring-verification-banner"></div>'


@pytest.fixture(autouse=True)
def _clear_cache():
    """Start from an empty cache: per-user keys collide when rolled-back row ids are reused."""
    cache.clear()
    yield
    cache.clear()


def _banner(user):
    """Read the rider's banner payload the way base.html gets it.

    Returns:
        The payload, or None for no banner.

    """
    request = RequestFactory().get("/")
    request.user = user
    return expiring_verifications(request)["expiring_verifications"]


def _in_category(user, zp_team_rider_factory, div: int):
    """Put the rider in a ZwiftPower category.

    Returns:
        The rider.

    """
    user.zwid = 4242
    user.gender = "male"
    user.save(update_fields=["zwid", "gender"])
    zp_team_rider_factory(zwid=4242, div=div)
    return user


# --- The banner ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_pending_renewal_hides_the_warning(verification_factory, user):
    verification_factory(user, "weight_full", days_ago=110)  # 10 days left
    verification_factory(user, "weight_full", status=PENDING)  # today's evidence

    assert _banner(user) is None


@pytest.mark.django_db
def test_the_warning_returns_when_the_renewal_is_rejected(verification_factory, user):
    verification_factory(user, "weight_full", days_ago=110)
    renewal = verification_factory(user, "weight_full", status=PENDING)
    renewal.status = RaceReadyRecord.Status.REJECTED
    renewal.save()

    assert _banner(user)["soonest_type"] == "Weight Full"


@pytest.mark.django_db
def test_a_pending_record_of_another_type_leaves_the_warning(verification_factory, user):
    verification_factory(user, "weight_full", days_ago=110)
    verification_factory(user, "height", status=PENDING)

    assert _banner(user)["soonest_type"] == "Weight Full"


@pytest.mark.django_db
@pytest.mark.parametrize("days_ago", [110, 115])
def test_a_pending_duplicate_with_no_newer_evidence_leaves_the_warning(verification_factory, user, days_ago):
    """Verified, it would cover no longer than the record it duplicates, so it renews nothing."""
    verification_factory(user, "weight_full", days_ago=110)
    verification_factory(user, "weight_full", days_ago=days_ago, status=PENDING)

    assert _banner(user)["soonest_days"] == 10


@pytest.mark.django_db
def test_only_the_renewed_type_goes_quiet(verification_factory, user):
    verification_factory(user, "weight_full", days_ago=110)  # 10 days left
    verification_factory(user, "power", days_ago=358)  # 7 days left
    verification_factory(user, "power", status=PENDING)

    banner = _banner(user)

    assert (banner["count"], banner["soonest_type"], banner["soonest_days"]) == (1, "Weight Full", 10)


@pytest.mark.django_db
def test_where_either_weight_counts_a_pending_weight_of_the_other_kind_answers_it(
    verification_factory, zp_team_rider_factory, user
):
    _in_category(user, zp_team_rider_factory, div=40)  # D: weight_full or weight_light
    verification_factory(user, "weight_full", days_ago=110)  # 10 days left
    verification_factory(user, "weight_light", status=PENDING)  # 30 days, once verified

    assert _banner(user) is None


@pytest.mark.django_db
def test_where_only_full_weight_counts_a_pending_light_weight_does_not(
    verification_factory, zp_team_rider_factory, user
):
    _in_category(user, zp_team_rider_factory, div=10)  # A: weight_full only
    verification_factory(user, "weight_full", days_ago=110)
    verification_factory(user, "weight_light", status=PENDING)

    assert _banner(user)["soonest_type"] == "Weight Full"


# --- The expiry DM ---------------------------------------------------------------------------


def _discord_user(user):
    """Give the rider a Discord id, which the DM task needs.

    Returns:
        The rider.

    """
    user.discord_id = "555001"
    user.first_name = user.first_name or "Test"
    user.save(update_fields=["discord_id", "first_name"])
    return user


def _run_dm_task():
    """Run the DM task for the 1-day warning, sending nothing.

    Returns:
        The task's summary and the DM mock.

    """
    with patch("apps.team.tasks.send_discord_dm", return_value=True) as dm, patch("apps.team.tasks.time.sleep"):
        return warn_expiring_verifications.func(days=[1]), dm


@pytest.mark.django_db
def test_no_dm_while_a_renewal_awaits_review_and_the_owed_one_follows_a_rejection(verification_factory, user):
    _discord_user(user)
    expiring = verification_factory(user, "weight_full", days_ago=119)  # 1 day left
    renewal = verification_factory(user, "weight_full", status=PENDING)

    result, dm = _run_dm_task()

    assert dm.call_count == 0
    assert (result["warnings_sent"], result["skipped_renewal_pending"]) == (0, 1)
    expiring.refresh_from_db()
    assert expiring.last_warned_threshold is None  # not recorded as warned

    renewal.status = RaceReadyRecord.Status.REJECTED
    renewal.save()
    result, dm = _run_dm_task()

    assert dm.call_count == 1
    assert result["skipped_renewal_pending"] == 0


# --- Submitting and deleting on the verification page ----------------------------------------


@pytest.fixture
def rider(user_model):
    """Build a Zwift-verified rider, who may submit records.

    Returns:
        The rider.

    """
    return user_model.objects.create_user(
        username="rider",
        email="rider@example.test",
        gender="male",
        zwid=4242,
        zwid_verified=True,
        permission_overrides={"team_member": True},
    )


def _submit(client, verify_type: str):
    """Post today's evidence through the verification page's form, as htmx sends it.

    Returns:
        The response.

    """
    accepted = RaceReadyRecordForm.MEDIA_TYPES_BY_VERIFY_TYPE[verify_type]
    data = {
        "verify_type": verify_type,
        "media_type": "link" if "link" in accepted else "photo",
        "url": "https://example.test/evidence",
        "record_date": timezone.localdate().isoformat(),
    }
    if verify_type.startswith("weight"):
        data["weight"] = "71.5"
    elif verify_type == "height":
        data["height"] = "178"
    return client.post(reverse("accounts:submit_race_ready"), data, HTTP_HX_REQUEST="true")


def _page(client) -> str:
    return client.get(reverse("accounts:verification")).content.decode()


@pytest.mark.django_db
def test_submitting_a_renewal_takes_the_banner_down_at_once(client, rider, verification_factory):
    verification_factory(rider, "weight_light", days_ago=25)  # 5 days left
    client.force_login(rider)
    assert BANNER_UP in _page(client)  # and now cached

    body = _submit(client, "weight_light").content.decode()

    assert '<div id="expiring-verification-banner" hx-swap-oob="true"></div>' in body
    # The cached banner is keyed by the session stamp the submit moved, so the next page --
    # served by whichever web worker, each with its own cache -- reads the records again.
    assert BANNER_EMPTY in _page(client)


@pytest.mark.django_db
def test_submitting_another_type_leaves_the_banner_up(client, rider, verification_factory):
    verification_factory(rider, "weight_light", days_ago=25)
    client.force_login(rider)

    body = _submit(client, "height").content.decode()

    assert '<div id="expiring-verification-banner" hx-swap-oob="true" class="bg-warning' in body


@pytest.mark.django_db
def test_deleting_the_renewal_brings_the_banner_back(client, rider, verification_factory):
    verification_factory(rider, "weight_light", days_ago=25)
    renewal = verification_factory(rider, "weight_light", status=PENDING)
    client.force_login(rider)
    assert BANNER_EMPTY in _page(client)  # and now cached

    client.post(reverse("accounts:verification_delete"), {"record_ids": [renewal.pk]})

    assert BANNER_UP in _page(client)

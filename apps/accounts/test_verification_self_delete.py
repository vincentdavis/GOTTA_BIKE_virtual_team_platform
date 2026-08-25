"""A rider deleting their own verification records from /user/verification/.

These records hold body photos and measurements, so the person they are about should be
able to remove them without asking an admin. Deleting one is allowed to cost them their
Race Verified status -- that is the honest consequence of removing the evidence -- so the
page marks which records are currently carrying that status before they choose.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.team.models import RaceReadyRecord


@pytest.fixture
def rider(team_member):
    """Build a verified rider whose required types are the default weight_light + height.

    Returns:
        The rider.

    """
    team_member.zwid = 555
    team_member.zwid_verified = True
    team_member.save(update_fields=["zwid", "zwid_verified"])
    return team_member


def _record(user, verify_type, *, with_file=False, **kwargs):
    """Create a verified record for the rider.

    Returns:
        The record.

    """
    record = RaceReadyRecord.objects.create(
        user=user,
        verify_type=verify_type,
        media_type="photo" if with_file else "link",
        url="" if with_file else "https://example.test/evidence",
        status=RaceReadyRecord.Status.VERIFIED,
        **kwargs,
    )
    if with_file:
        record.media_file = SimpleUploadedFile("e.jpg", b"not-a-jpeg", content_type="image/jpeg")
        record.save(update_fields=["media_file"])
    return record


def _delete(client, ids):
    """POST a selection to the delete endpoint.

    Returns:
        The response.

    """
    return client.post(reverse("accounts:verification_delete"), {"record_ids": [str(i) for i in ids]})


@pytest.mark.django_db
def test_a_rider_can_delete_their_own_record_and_its_evidence(client, rider) -> None:
    """The row goes and so does the uploaded file -- not just the row."""
    record = _record(rider, "height", with_file=True)
    storage, path = record.media_file.storage, record.media_file.name
    assert storage.exists(path)
    client.force_login(rider)

    _delete(client, [record.pk])

    assert not RaceReadyRecord.objects.filter(pk=record.pk).exists()
    assert not storage.exists(path)


@pytest.mark.django_db
def test_a_rider_cannot_delete_someone_elses_record(client, rider, user_model) -> None:
    """Ids are filtered through the rider's own records, so a foreign id matches nothing."""
    other = user_model.objects.create_user(username="other", email="other@example.test")
    theirs = _record(other, "height")
    client.force_login(rider)

    _delete(client, [theirs.pk])

    assert RaceReadyRecord.objects.filter(pk=theirs.pk).exists()


@pytest.mark.django_db
def test_deleting_the_covering_record_revokes_race_verified(client, rider) -> None:
    """Removing the evidence removes the status it was supporting."""
    _record(rider, "weight_light", weight=70)
    height = _record(rider, "height", height=180)
    rider.refresh_race_ready()
    rider.refresh_from_db()
    assert rider.is_race_ready
    client.force_login(rider)

    response = _delete(client, [height.pk])

    rider.refresh_from_db()
    assert not rider.is_race_ready
    assert any("no longer Race Verified" in str(m) for m in response.wsgi_request._messages)


@pytest.mark.django_db
def test_deleting_an_unrelated_record_keeps_race_verified(client, rider) -> None:
    """A record that was not covering a required type costs the rider nothing."""
    _record(rider, "weight_light", weight=70)
    _record(rider, "height", height=180)
    spare = _record(rider, "power", ftp=250)
    rider.refresh_race_ready()
    rider.refresh_from_db()
    assert rider.is_race_ready
    client.force_login(rider)

    _delete(client, [spare.pk])

    rider.refresh_from_db()
    assert rider.is_race_ready


@pytest.mark.django_db
def test_the_page_marks_which_records_are_carrying_the_status(client, rider) -> None:
    """The rider has to be able to see what a deletion would cost before making it."""
    _record(rider, "weight_light", weight=70)
    _record(rider, "height", height=180)
    rider.refresh_race_ready()
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    assert "Counts now" in body
    assert 'name="record_ids"' in body


@pytest.mark.django_db
def test_selecting_nothing_deletes_nothing(client, rider) -> None:
    """An empty submit is a no-op, not a wipe."""
    record = _record(rider, "height")
    client.force_login(rider)

    client.post(reverse("accounts:verification_delete"), {})

    assert RaceReadyRecord.objects.filter(pk=record.pk).exists()


@pytest.mark.django_db
def test_the_delete_endpoint_refuses_get(client, rider) -> None:
    """Deletion must not be reachable by following a link."""
    client.force_login(rider)

    assert client.get(reverse("accounts:verification_delete")).status_code == 405


@pytest.mark.django_db
def test_losing_the_status_drops_the_discord_role_immediately(client, rider) -> None:
    """Otherwise the rider keeps the race-ready role until the nightly sweep notices."""
    _record(rider, "weight_light", weight=70)
    height = _record(rider, "height", height=180)
    rider.refresh_race_ready()
    client.force_login(rider)

    with patch("apps.team.tasks.notify_race_ready_change") as task:
        _delete(client, [height.pk])

    task.enqueue.assert_called_once()
    assert task.enqueue.call_args[1]["is_now_race_ready"] is False


@pytest.mark.django_db
def test_no_role_churn_when_the_status_did_not_change(client, rider) -> None:
    """A rider who was never race verified should not trigger a Discord write."""
    spare = _record(rider, "power", ftp=250)
    client.force_login(rider)

    with patch("apps.team.tasks.notify_race_ready_change") as task:
        _delete(client, [spare.pk])

    task.enqueue.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("junk", ["\u00b2", "\u0663", "9" * 40, "abc", "-1", "0", ""])
def test_a_hand_crafted_id_is_rejected_not_crashed_on(client, rider, junk) -> None:
    """isdigit() passed "\u00b2" (int() raises) and "\u0663" (int() yields 3, deleting pk 3)."""
    record = _record(rider, "height")
    client.force_login(rider)

    response = client.post(reverse("accounts:verification_delete"), {"record_ids": [junk]})

    assert response.status_code == 302
    assert RaceReadyRecord.objects.filter(pk=record.pk).exists()


@pytest.mark.django_db
def test_a_duplicate_of_the_same_type_is_not_badged(client, rider) -> None:
    """Deleting one of two live records of a type costs nothing, so it must not warn."""
    _record(rider, "weight_light", weight=70)
    _record(rider, "height", height=180)
    _record(rider, "height", height=181)
    rider.refresh_race_ready()
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    # weight_light is the only one of its type, so exactly one row should be badged.
    assert body.count("Counts now") == 1


@pytest.mark.django_db
def test_either_weight_type_satisfies_a_d_or_e_rider(client, rider) -> None:
    """Categories 40/50 accept either weight type, so neither alone is load-bearing."""
    d_or_e = ["weight_light", "weight_full", "height"]
    # The view binds the name at import time; calculate_race_ready imports it per call.
    with patch("apps.accounts.views.get_user_required_verification_types", return_value=d_or_e), patch(
        "apps.team.services.get_user_required_verification_types", return_value=d_or_e
    ):
        _record(rider, "weight_light", weight=70)
        _record(rider, "weight_full", weight=70)
        _record(rider, "height", height=180)
        rider.refresh_race_ready()
        client.force_login(rider)

        body = client.get(reverse("accounts:verification")).content.decode()

    # Height is load-bearing; neither weight record is, because the other still covers it.
    assert body.count("Counts now") == 1


@pytest.mark.django_db
def test_nothing_is_badged_when_the_rider_is_not_verified(client, rider) -> None:
    """A badge saying "counts now" is a lie when there is no status to count towards."""
    _record(rider, "weight_light", weight=70)  # height missing, so not race ready
    rider.refresh_race_ready()
    rider.refresh_from_db()
    assert not rider.is_race_ready
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    assert "Counts now" not in body


@pytest.mark.django_db
def test_losing_extra_verified_is_reported(client, rider) -> None:
    """It used to happen silently -- the rider saw only "Deleted 1 verification record"."""
    _record(rider, "weight_full", weight=70)
    _record(rider, "height", height=180)
    power = _record(rider, "power", ftp=250)
    rider.refresh_race_ready()
    rider.refresh_from_db()
    assert rider.is_extra_verified
    client.force_login(rider)

    response = _delete(client, [power.pk])

    rider.refresh_from_db()
    assert not rider.is_extra_verified
    assert any("no longer Extra Verified" in str(m) for m in response.wsgi_request._messages)


@pytest.mark.django_db
def test_a_stale_cached_status_is_not_blamed_on_the_deletion(client, rider) -> None:
    """An expiry the nightly sweep has not caught yet must not be reported as our doing."""
    _record(rider, "weight_light", weight=70, record_date=date.today() - timedelta(days=3650))
    _record(rider, "height", height=180)
    spare = _record(rider, "power", ftp=250)
    # Simulate the sweep not having run: the column still says race ready.
    rider.is_race_ready = True
    rider.save(update_fields=["is_race_ready"])
    client.force_login(rider)

    response = _delete(client, [spare.pk])

    assert not any("no longer Race Verified" in str(m) for m in response.wsgi_request._messages)

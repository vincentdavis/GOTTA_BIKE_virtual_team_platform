"""Tests for the permission-checked verification media endpoint.

The endpoint exists so the review page never embeds a presigned storage URL. Such a URL
carries its signature in the query string, which makes it a bearer token: forwardable,
session-less and unrevokable. These tests pin both halves of that -- that the page no
longer contains one, and that the endpoint replacing it refuses everyone the page would
have refused.
"""

import pytest
from django.urls import reverse

from apps.team.models import RaceReadyRecord
from apps.team.services import can_view_verification_media
from conftest import _make_user


@pytest.fixture
def rider(db, user_model):
    return _make_user(user_model, username="rider", permissions={"team_member": True}, gender="female")


@pytest.fixture
def reviewer(db, user_model):
    return _make_user(
        user_model,
        username="reviewer",
        permissions={"team_member": True, "approve_verification": True},
        gender="female",
    )


@pytest.fixture
def other_gender_reviewer(db, user_model):
    return _make_user(
        user_model,
        username="male_reviewer",
        permissions={"team_member": True, "approve_verification": True},
        gender="male",
    )


@pytest.fixture
def pvt_reviewer(db, user_model):
    return _make_user(
        user_model,
        username="pvt",
        permissions={
            "team_member": True,
            "approve_verification": True,
            "performance_verification_team": True,
        },
        gender="female",
    )


@pytest.fixture
def record_factory(db):
    def _make(owner, *, status=RaceReadyRecord.Status.PENDING, same_gender=False, with_media=True, media_type="photo"):
        from datetime import date

        record = RaceReadyRecord.objects.create(
            user=owner,
            verify_type="weight_light",
            media_type=media_type if with_media else "link",
            url="" if with_media else "https://example.test/e",
            status=status,
            record_date=date.today(),
            same_gender=same_gender,
        )
        if with_media:
            record.media_file.name = "verification/evidence.jpg"
            record.save(update_fields=["media_file"])
        return record

    return _make


def _media_url(record):
    return reverse("team:verification_record_media", args=[record.pk])


@pytest.mark.django_db
def test_reviewer_is_redirected_to_a_freshly_minted_url(client, reviewer, rider, record_factory):
    record = record_factory(rider)
    client.force_login(reviewer)
    response = client.get(_media_url(record))
    assert response.status_code == 302
    assert "verification/evidence.jpg" in response["Location"]


@pytest.mark.django_db
def test_redirect_is_never_cached(client, reviewer, rider, record_factory):
    """The Location header resolves to a credential, so no cache may retain it."""
    record = record_factory(rider)
    client.force_login(reviewer)
    response = client.get(_media_url(record))
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
def test_plain_team_member_cannot_fetch_media(client, team_member, rider, record_factory):
    record = record_factory(rider)
    client.force_login(team_member)
    assert client.get(_media_url(record)).status_code == 404


@pytest.mark.django_db
def test_anonymous_user_cannot_fetch_media(client, rider, record_factory):
    record = record_factory(rider)
    response = client.get(_media_url(record))
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"] or "login" in response["Location"]


@pytest.mark.django_db
def test_same_gender_restriction_blocks_a_different_gender_reviewer(
    client, other_gender_reviewer, rider, record_factory
):
    record = record_factory(rider, same_gender=True)
    client.force_login(other_gender_reviewer)
    assert client.get(_media_url(record)).status_code == 404


@pytest.mark.django_db
def test_same_gender_restriction_allows_a_matching_reviewer(client, reviewer, rider, record_factory):
    record = record_factory(rider, same_gender=True)
    client.force_login(reviewer)
    assert client.get(_media_url(record)).status_code == 302


@pytest.mark.django_db
def test_superuser_bypasses_the_same_gender_restriction(client, superuser, rider, record_factory):
    """Matches the review page, which also lets superusers through."""
    record = record_factory(rider, same_gender=True)
    client.force_login(superuser)
    assert client.get(_media_url(record)).status_code == 302


@pytest.mark.django_db
def test_decided_record_media_is_hidden_from_an_ordinary_reviewer(client, reviewer, rider, record_factory):
    record = record_factory(rider, status=RaceReadyRecord.Status.VERIFIED)
    client.force_login(reviewer)
    assert client.get(_media_url(record)).status_code == 404


@pytest.mark.django_db
def test_decided_record_media_stays_visible_to_the_verification_team(client, pvt_reviewer, rider, record_factory):
    record = record_factory(rider, status=RaceReadyRecord.Status.VERIFIED)
    client.force_login(pvt_reviewer)
    assert client.get(_media_url(record)).status_code == 302


@pytest.mark.django_db
def test_record_without_media_is_404_not_a_broken_redirect(client, reviewer, rider, record_factory):
    record = record_factory(rider, with_media=False)
    client.force_login(reviewer)
    assert client.get(_media_url(record)).status_code == 404


@pytest.mark.django_db
def test_missing_record_and_forbidden_record_are_indistinguishable(client, team_member, rider, record_factory):
    """Denials are 404 so the endpoint cannot confirm that a record id exists."""
    record = record_factory(rider)
    client.force_login(team_member)
    real = client.get(_media_url(record)).status_code
    absent = client.get(reverse("team:verification_record_media", args=[record.pk + 9999])).status_code
    assert real == absent == 404


@pytest.mark.django_db
def test_review_page_embeds_the_endpoint_and_not_a_storage_url(client, reviewer, rider, record_factory):
    """The regression that motivated this endpoint: no signed URL in the HTML."""
    record = record_factory(rider)
    client.force_login(reviewer)
    html = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()
    assert _media_url(record) in html
    assert "evidence.jpg" not in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("fixture_name", "status", "same_gender", "expected"),
    [
        ("reviewer", RaceReadyRecord.Status.PENDING, False, True),
        ("reviewer", RaceReadyRecord.Status.VERIFIED, False, False),
        ("pvt_reviewer", RaceReadyRecord.Status.VERIFIED, False, True),
        ("other_gender_reviewer", RaceReadyRecord.Status.PENDING, True, False),
        ("team_member", RaceReadyRecord.Status.PENDING, False, False),
    ],
)
def test_gate_and_endpoint_never_disagree(
    client, request, rider, record_factory, fixture_name, status, same_gender, expected
):
    """Whatever the shared gate says, the endpoint must do -- they cannot drift apart."""
    viewer = request.getfixturevalue(fixture_name)
    record = record_factory(rider, status=status, same_gender=same_gender)
    assert can_view_verification_media(viewer, record) is expected
    client.force_login(viewer)
    assert (client.get(_media_url(record)).status_code == 302) is expected


@pytest.fixture
def signing_storage(settings):
    """Swap default storage for the signing S3 backend production actually runs.

    Under local FileSystemStorage no signature exists, so an assertion that the page
    contains no signature would pass even if the template were reverted. These tests are
    only meaningful against a backend that really signs.
    """
    from django.core.files.storage import default_storage
    from django.utils.functional import empty

    settings.STORAGES = {
        **settings.STORAGES,
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": "test-bucket",
                "endpoint_url": "https://s3.example.test",
                "access_key": "AKIAEXAMPLE",
                "secret_key": "topsecret",
                "querystring_expire": 900,
            },
        },
    }
    default_storage._wrapped = empty
    yield
    default_storage._wrapped = empty


@pytest.mark.django_db
def test_endpoint_mints_a_signed_short_lived_url(client, reviewer, rider, record_factory, signing_storage):
    """Control for the test below: proves a signature IS detectable in this setup."""
    record = record_factory(rider)
    client.force_login(reviewer)
    location = client.get(_media_url(record))["Location"]
    assert "Signature" in location, "storage did not sign; the leak test below would be vacuous"

    # The URL must expire on the endpoint's short clock (300s), not the storage default the
    # settings file sets for ordinary media (900s).
    assert 240 <= _ttl_of(location) <= 360, "expected a ~300s window for a photo"


def _ttl_of(location: str) -> int:
    """Seconds a signed URL stays valid, for either signature version.

    Args:
        location: The Location header from the media endpoint.

    Returns:
        Remaining lifetime in seconds.

    """
    import re
    import time

    v4 = re.search(r"[?&]X-Amz-Expires=(\d+)", location)
    if v4:
        return int(v4.group(1))
    return int(re.search(r"[?&]Expires=(\d+)", location).group(1)) - int(time.time())


@pytest.mark.django_db
def test_video_links_outlive_a_viewing_session(client, reviewer, rider, record_factory, signing_storage):
    """A browser range-requests the resolved URL during playback, never returning here.

    A photo-length window would expire mid-review and stall the player with no error.
    """
    photo = record_factory(rider, media_type="photo")
    video = record_factory(rider, media_type="video")
    client.force_login(reviewer)
    photo_ttl = _ttl_of(client.get(_media_url(photo))["Location"])
    video_ttl = _ttl_of(client.get(_media_url(video))["Location"])
    assert video_ttl > photo_ttl
    assert 1700 <= video_ttl <= 1900


@pytest.mark.django_db
def test_no_signed_url_reaches_the_rendered_page(client, reviewer, rider, record_factory, signing_storage):
    """The whole point of the endpoint: the HTML must carry no bearer token."""
    record = record_factory(rider)
    client.force_login(reviewer)
    html = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()
    assert _media_url(record) in html
    # The record's own object key must appear nowhere: any presigned URL for it would carry it.
    assert "evidence.jpg" not in html
    assert "race_ready/" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("media_type", ["photo", "video", "link"])
def test_every_template_branch_links_to_the_endpoint(
    client, reviewer, rider, record_factory, signing_storage, media_type
):
    """The template has three media branches -- photo, video and download.

    Only the photo branch was exercised before, leaving four of the six converted URLs
    unprotected by any test. Each branch must point at the endpoint and carry no signature.
    """
    record = record_factory(rider, media_type=media_type)
    client.force_login(reviewer)
    html = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()
    assert _media_url(record) in html, f"{media_type} branch does not link to the endpoint"
    assert "evidence.jpg" not in html, f"{media_type} branch leaked a link to the file"
    assert "race_ready/" not in html


@pytest.mark.django_db
def test_django_admin_does_not_render_a_link_to_the_evidence(
    client, user_model, rider, record_factory, signing_storage
):
    """Admin would otherwise render a presigned URL in its file widget, bypassing the gate."""
    staff = _make_user(user_model, username="staffer", is_staff=True, is_superuser=True)
    record = record_factory(rider)
    client.force_login(staff)
    response = client.get(reverse("admin:team_racereadyrecord_change", args=[record.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "evidence.jpg" not in html
    assert "race_ready/" not in html

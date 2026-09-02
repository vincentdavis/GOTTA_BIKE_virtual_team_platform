"""Media upload limits on the Race Verified submission form.

Verification evidence is typically a phone video of a scale or a power meter, which runs
large. The ceiling is one constant, ``MAX_MEDIA_UPLOAD_MB``, read by three places that used to
be able to disagree: the validator, the help text under the field, and the browser-side check
that stops an oversized file before it is uploaded rather than after.

The size tests drive ``clean_media_file`` with a stub rather than a real file. Allocating
150 MB per assertion would dominate the suite, and the validator only ever reads ``.size`` and
``.name`` -- so a stub tests the actual rule, and a small real file covers the wiring.
"""

from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import ValidationError
from django.urls import reverse

from apps.team.forms import ALLOWED_MEDIA_EXTENSIONS, MAX_MEDIA_UPLOAD_MB, RaceReadyRecordForm
from apps.team.models import RaceReadyRecord

MB = 1024 * 1024


def _check(size_mb: float, name: str = "clip.mp4"):
    """Run the media validator against a file of the given size.

    Args:
        size_mb: Size in megabytes.
        name: File name, which decides the extension check.

    Returns:
        The validated stub.

    """
    form = RaceReadyRecordForm()
    form.cleaned_data = {"media_file": SimpleNamespace(size=int(size_mb * MB), name=name)}
    return form.clean_media_file()


def test_the_limit_is_150mb():
    """The number the user asked for, pinned so a refactor cannot quietly lower it."""
    assert MAX_MEDIA_UPLOAD_MB == 150


def test_a_file_at_the_limit_is_accepted():
    """150 MB exactly must pass -- the check is 'over the limit', not 'at' it."""
    assert _check(MAX_MEDIA_UPLOAD_MB) is not None


def test_a_file_over_the_limit_is_rejected():
    with pytest.raises(ValidationError):
        _check(MAX_MEDIA_UPLOAD_MB + 1)


def test_the_rejection_names_the_actual_size_and_the_limit():
    """"Too large" leaves a rider guessing; a number tells them how much to trim."""
    with pytest.raises(ValidationError) as caught:
        _check(187)

    message = str(caught.value)
    assert "187" in message
    assert str(MAX_MEDIA_UPLOAD_MB) in message


def test_a_disallowed_extension_is_rejected_by_name():
    with pytest.raises(ValidationError) as caught:
        _check(1, name="evidence.pdf")

    assert "evidence.pdf" in str(caught.value)


def test_every_allowed_extension_passes():
    """Guards the extension list against a typo that would silently reject a real format."""
    for ext in ALLOWED_MEDIA_EXTENSIONS:
        assert _check(1, name=f"evidence{ext}") is not None


# ---------------------------------------------------------------- the rendered form


def test_the_widget_carries_the_limit_for_the_browser_check():
    """The browser reads the ceiling from the widget rather than hardcoding its own copy."""
    attrs = RaceReadyRecordForm().fields["media_file"].widget.attrs

    assert attrs["data-max-mb"] == MAX_MEDIA_UPLOAD_MB
    assert attrs["accept"] == ",".join(ALLOWED_MEDIA_EXTENSIONS)


@pytest.mark.django_db
def test_the_help_text_states_the_real_limit(client, user_model):
    """The number shown to a rider is generated from the constant, not typed beside it."""
    rider = user_model.objects.create_user(
        username="rider", email="rider@example.test", gender="male",
        zwid=6164399, zwid_verified=True,  # the form only renders for a verified rider
        permission_overrides={"team_member": True},
    )
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    assert f"Max {MAX_MEDIA_UPLOAD_MB}MB" in body
    assert "Max 50MB" not in body


@pytest.mark.django_db
def test_the_form_renders_both_error_slots(client, user_model):
    """The client-side size error and the submit-failure banner each need somewhere to land.

    They are separate on purpose: one appears beside the file input as soon as a file is
    chosen, the other beside the submit button after a failed request.
    """
    rider = user_model.objects.create_user(
        username="rider", email="rider@example.test", gender="male",
        zwid=6164399, zwid_verified=True,  # the form only renders for a verified rider
        permission_overrides={"team_member": True},
    )
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    assert 'id="file-error"' in body
    assert 'id="submit-error"' in body


@pytest.mark.django_db
def test_a_file_larger_than_djangos_body_cap_still_uploads(client, user_model):
    """DATA_UPLOAD_MAX_MEMORY_SIZE is 2.5 MB, and it does NOT apply to file uploads.

    Worth pinning rather than trusting: if a future settings change made it apply, every
    verification video would fail with RequestDataTooBig and the form's own 150 MB limit would
    never be reached. 8 MB is comfortably past the cap and cheap enough to run.
    """
    rider = user_model.objects.create_user(
        username="rider", email="rider@example.test", gender="male",
        zwid=6164399, zwid_verified=True,  # the form only renders for a verified rider
        permission_overrides={"team_member": True},
    )
    client.force_login(rider)
    upload = SimpleUploadedFile("clip.mp4", b"x" * (8 * MB), content_type="video/mp4")

    response = client.post(
        reverse("accounts:submit_race_ready"),
        {
            "verify_type": "height",
            "media_type": "photo",
            "record_date": "2026-09-01",
            "height": "180",
            "media_file": upload,
        },
    )

    assert response.status_code == 302  # not a 400/413
    # The view redirects on success AND on the non-HTMX failure path, so the status alone
    # proves nothing -- the stored record is what shows the upload actually landed.
    assert RaceReadyRecord.objects.filter(user=rider, verify_type="height").exists()

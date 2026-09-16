"""Which kinds of evidence each Race Verified submission may use.

"Other" joins the list for all four verifications. Along the way the rule stopped living only
in the submission form's JavaScript: the picker narrowed the options, but ``clean()`` never
checked them, so a request that skipped the page's script could file a Weight Light as a
video. The rule is now one map on the form, enforced by ``clean()`` and read by the picker.
"""

import json
import re

import pytest
from django.urls import reverse

from apps.team.forms import RaceReadyRecordForm
from apps.team.models import RaceReadyRecord

LINK = "https://example.test/evidence"


def _form(verify_type, media_type, **overrides):
    """Bind the submission form the way the page would for that media type.

    A URL is the evidence for the named kinds, so no file is involved. Other carries neither a
    file nor a link and is described in the notes instead.

    Returns:
        The bound form.

    """
    data = {
        "verify_type": verify_type,
        "media_type": media_type,
        "record_date": "2026-09-01",
    }
    if media_type == "other":
        data["notes"] = "Weighed in person at the club night, witnessed by Sam."
    else:
        data["url"] = LINK
    data.update(overrides)
    if verify_type in ("weight_full", "weight_light"):
        data["weight"] = "72.5"
    if verify_type == "height":
        data["height"] = "178"
    return RaceReadyRecordForm(data=data)


# --- "Other" is offered everywhere ------------------------------------------------------------


def test_other_is_a_media_type():
    assert ("other", "Other") in RaceReadyRecord._meta.get_field("media_type").choices


@pytest.mark.django_db
@pytest.mark.parametrize("verify_type", ["weight_full", "weight_light", "height", "power"])
def test_every_verification_accepts_other(verify_type):
    form = _form(verify_type, "other")

    assert form.is_valid(), form.errors


# --- what each verification still accepts ------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("verify_type", ["weight_full", "height", "power"])
@pytest.mark.parametrize("media_type", ["video", "link"])
def test_the_full_checks_still_take_video_and_link(verify_type, media_type):
    """Unchanged: adding Other must not have narrowed what was already allowed."""
    assert _form(verify_type, media_type).is_valid()


@pytest.mark.django_db
def test_weight_light_still_takes_a_photo():
    assert _form("weight_light", "photo").is_valid()


# --- what each verification refuses, now on the server ---------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("media_type", ["video", "link"])
def test_weight_light_refuses_video_and_link(media_type):
    """The picker never offered these; now the server does not take them either."""
    form = _form("weight_light", media_type)

    assert not form.is_valid()
    assert "media_type" in form.errors


@pytest.mark.django_db
@pytest.mark.parametrize("verify_type", ["weight_full", "height", "power"])
def test_the_full_checks_refuse_a_photo(verify_type):
    """A still image is what Weight Light is for; the others need more than one frame."""
    form = _form(verify_type, "photo")

    assert not form.is_valid()
    assert "media_type" in form.errors


@pytest.mark.django_db
def test_the_refusal_says_what_would_have_been_accepted():
    """Named in the rider's words, not the stored values -- "weight_light" is not a sentence."""
    form = _form("weight_light", "video")
    form.is_valid()

    assert form.errors["media_type"] == ["Weight Light can be submitted as Photo, Other."]


# --- the picker and the check are one rule -----------------------------------------------------


def test_every_verification_has_an_entry():
    """A type missing from the map would fall back to offering everything, and pass nothing."""
    covered = set(RaceReadyRecordForm.MEDIA_TYPES_BY_VERIFY_TYPE)
    offered = {value for value, _ in RaceReadyRecordForm.ALL_VERIFY_TYPE_CHOICES}

    assert covered == offered


def test_the_map_names_only_real_media_types():
    """A typo here would offer an option the model refuses, or never offer one it takes."""
    real = {value for value, _ in RaceReadyRecord._meta.get_field("media_type").choices}

    for verify_type, media_types in RaceReadyRecordForm.MEDIA_TYPES_BY_VERIFY_TYPE.items():
        assert set(media_types) <= real, verify_type


@pytest.mark.django_db
def test_the_page_hands_the_picker_the_same_map(client, user_model):
    """The script reads this instead of keeping a copy of its own, so they cannot drift."""
    rider = user_model.objects.create_user(
        username="rider",
        email="rider@example.test",
        gender="male",
        zwid=6164399,
        zwid_verified=True,  # the form only renders for a verified rider
        permission_overrides={"team_member": True},
    )
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()
    payload = re.search(r'<script id="media-types-by-verify-type" type="application/json">(.*?)</script>', body)

    assert payload, "the picker's data is missing from the page"
    assert json.loads(payload.group(1)) == {
        key: list(value) for key, value in RaceReadyRecordForm.MEDIA_TYPES_BY_VERIFY_TYPE.items()
    }


@pytest.mark.django_db
def test_the_picker_no_longer_hardcodes_the_rule(client, user_model):
    """The old script filtered on 'photo' by name; that copy of the rule is what drifted."""
    rider = user_model.objects.create_user(
        username="rider",
        email="rider@example.test",
        gender="male",
        zwid=6164399,
        zwid_verified=True,
        permission_overrides={"team_member": True},
    )
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    assert "o.value === 'photo'" not in body
    assert "o.value !== 'photo'" not in body


# --- "Other" is neither a file nor a link ----------------------------------------------------


@pytest.mark.django_db
def test_other_is_refused_with_a_link():
    """The picker hides the field; the server has to refuse it too, since a POST can send it."""
    form = _form("height", "other", url=LINK)

    assert not form.is_valid()
    assert "url" in form.errors


@pytest.mark.django_db
def test_other_is_refused_with_a_file():
    from django.core.files.uploadedfile import SimpleUploadedFile

    form = RaceReadyRecordForm(
        data={
            "verify_type": "height",
            "media_type": "other",
            "record_date": "2026-09-01",
            "height": "178",
            "notes": "In person.",
        },
        files={"media_file": SimpleUploadedFile("scale.jpg", b"x", content_type="image/jpeg")},
    )

    assert not form.is_valid()
    assert "media_file" in form.errors


@pytest.mark.django_db
@pytest.mark.parametrize("notes", ["", "   "])
def test_other_needs_a_note(notes):
    """With no file or link, the note is the evidence -- a blank one is no evidence at all."""
    form = _form("height", "other", notes=notes)

    assert not form.is_valid()
    assert "notes" in form.errors


@pytest.mark.django_db
def test_other_with_a_note_and_nothing_else_is_accepted():
    """The shape the page sends once Other is chosen."""
    assert _form("height", "other").is_valid()


@pytest.mark.django_db
@pytest.mark.parametrize("media_type", ["video", "link"])
def test_the_named_kinds_still_need_a_file_or_a_link(media_type):
    """Unchanged, and now reached through a different branch -- so pinned."""
    form = _form("height", media_type, url="")

    assert not form.is_valid()
    assert "file upload or a URL" in str(form.non_field_errors())


@pytest.mark.django_db
def test_a_note_is_not_required_for_the_named_kinds():
    assert _form("height", "video", notes="").is_valid()


@pytest.mark.django_db
def test_the_model_holds_the_same_rule(user_model):
    """So the admin, which validates through the model, cannot save an Other with a link."""
    from django.core.exceptions import ValidationError

    rider = user_model.objects.create_user(username="rider", password="pw")  # noqa: S106
    record = RaceReadyRecord(
        user=rider, verify_type="height", media_type="other", url=LINK, notes="", height=178
    )

    with pytest.raises(ValidationError) as caught:
        record.clean()

    assert set(caught.value.message_dict) == {"url", "notes"}


@pytest.mark.django_db
def test_the_model_accepts_other_described_in_the_notes(user_model):
    rider = user_model.objects.create_user(username="rider", password="pw")  # noqa: S106

    RaceReadyRecord(user=rider, verify_type="height", media_type="other", notes="In person.", height=178).clean()


@pytest.mark.django_db
def test_the_page_has_what_the_picker_needs_to_switch(client, user_model):
    """The script finds these by id; a renamed wrapper would leave the file field showing."""
    rider = user_model.objects.create_user(
        username="rider",
        email="rider@example.test",
        gender="male",
        zwid=6164399,
        zwid_verified=True,
        permission_overrides={"team_member": True},
    )
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    for element_id in ("media-file-field", "url-field", "other-evidence-hint", "notes-label-required"):
        assert f'id="{element_id}"' in body, element_id


@pytest.mark.django_db
def test_the_missing_evidence_message_is_shown_once():
    """The form and the model both used to raise it, so every refusal printed it twice."""
    form = _form("height", "video", url="")
    form.is_valid()

    assert list(form.non_field_errors()).count("You must provide either a file upload or a URL (or both).") == 1

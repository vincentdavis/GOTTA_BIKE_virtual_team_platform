"""A rider's cached YouTube channel ID must not outlive the URL it was scraped from.

``youtube_channel_id`` is resolved once, by ``sync_youtube_channel_ids``, for riders who have
a URL and no ID. Nothing re-checked it, so a rider who switched channels -- or removed their
URL -- kept feeding the old channel's videos to their profile, the Team Feed and the bot.
``User.save`` now clears the ID, and the videos fetched under it, whenever the URL changes.
"""

from unittest.mock import patch

import pytest
from django.contrib import admin
from django.urls import reverse

from apps.accounts.models import User, YouTubeVideo
from apps.accounts.tasks import sync_youtube_channel_ids

OLD_URL = "https://www.youtube.com/@old-channel"
NEW_URL = "https://www.youtube.com/@new-channel"
OLD_ID = "UColdoldoldoldoldold"
NEW_ID = "UCnewnewnewnewnewnew"


@pytest.fixture
def youtuber(team_member):
    """Build a rider whose channel has been resolved and whose videos have been fetched.

    Returns:
        The rider, with a stored channel ID and one stored video.

    """
    team_member.youtube_channel = OLD_URL
    team_member.youtube_channel_id = OLD_ID
    team_member.save()
    YouTubeVideo.objects.create(user=team_member, video_id="old-video-1", title="Old channel")
    return team_member


@pytest.mark.django_db
def test_changing_the_url_clears_the_channel_id(youtuber):
    youtuber.youtube_channel = NEW_URL
    youtuber.save()

    youtuber.refresh_from_db()
    assert youtuber.youtube_channel_id == ""
    assert not YouTubeVideo.objects.filter(user=youtuber).exists()


@pytest.mark.django_db
def test_blanking_the_url_clears_the_channel_id(youtuber):
    youtuber.youtube_channel = ""
    youtuber.save()

    youtuber.refresh_from_db()
    assert youtuber.youtube_channel_id == ""
    assert not YouTubeVideo.objects.filter(user=youtuber).exists()


@pytest.mark.django_db
def test_saving_without_touching_the_url_keeps_the_channel_id(youtuber):
    youtuber.first_name = "Renamed"
    youtuber.save()

    youtuber.refresh_from_db()
    assert youtuber.youtube_channel_id == OLD_ID
    assert YouTubeVideo.objects.filter(user=youtuber).count() == 1


@pytest.mark.django_db
def test_update_fields_save_of_another_field_keeps_the_channel_id(youtuber):
    """A partial save that never writes the URL leaves the cached ID alone.

    ``refresh_race_ready`` and the Discord adapters save this way on every login, so a stale
    URL sitting in memory must not be read as a change.
    """
    youtuber.youtube_channel = NEW_URL  # never persisted: not in update_fields
    youtuber.is_race_ready = True
    youtuber.save(update_fields=["is_race_ready"])

    youtuber.refresh_from_db()
    assert youtuber.youtube_channel == OLD_URL
    assert youtuber.youtube_channel_id == OLD_ID
    assert YouTubeVideo.objects.filter(user=youtuber).count() == 1


@pytest.mark.django_db
def test_update_fields_save_of_the_url_clears_the_channel_id(youtuber):
    """Clearing the ID has to reach the database even on a narrow save."""
    youtuber.youtube_channel = NEW_URL
    youtuber.save(update_fields=["youtube_channel"])

    youtuber.refresh_from_db()
    assert youtuber.youtube_channel == NEW_URL
    assert youtuber.youtube_channel_id == ""


@pytest.mark.django_db
def test_an_explicitly_set_channel_id_survives_a_url_change(youtuber):
    """The admin can edit URL and ID together; the typed ID wins over the clear."""
    youtuber.youtube_channel = NEW_URL
    youtuber.youtube_channel_id = NEW_ID
    youtuber.save()

    youtuber.refresh_from_db()
    assert youtuber.youtube_channel_id == NEW_ID


@pytest.mark.django_db
def test_profile_form_clears_the_channel_id(client, youtuber):
    """The rider's own profile edit is the path that actually changes the URL."""
    client.force_login(youtuber)

    response = client.post(
        reverse("accounts:profile_edit"),
        {
            "first_name": "Test",
            "last_name": "Rider",
            "birth_year": "1990",
            "gender": "female",
            "timezone": "UTC",
            "country": "US",
            "trainer": "Smart trainer",
            "heartrate_monitor": "Chest strap",
            "unit_preference": "metric",
            "youtube_channel": NEW_URL,
        },
    )

    assert response.status_code in {200, 302}
    youtuber.refresh_from_db()
    assert youtuber.youtube_channel == NEW_URL
    assert youtuber.youtube_channel_id == ""
    assert not YouTubeVideo.objects.filter(user=youtuber).exists()


@pytest.mark.django_db
def test_admin_save_clears_the_channel_id(rf, superuser, youtuber):
    """UserAdmin saves through ``Model.save``, so it is covered by the same rule."""
    user_admin = admin.site.get_model_admin(User)
    request = rf.post("/admin/accounts/user/")
    request.user = superuser

    youtuber.youtube_channel = NEW_URL
    user_admin.save_model(request, youtuber, form=None, change=True)

    youtuber.refresh_from_db()
    assert youtuber.youtube_channel_id == ""
    assert not YouTubeVideo.objects.filter(user=youtuber).exists()


@pytest.mark.django_db
def test_sync_then_resolves_the_new_channel(youtuber):
    """The point of clearing: the next sync picks the rider up and reads the new channel."""
    youtuber.youtube_channel = NEW_URL
    youtuber.save()

    with (
        patch("apps.accounts.utils.extract_youtube_channel_id", return_value=NEW_ID) as extract,
        patch("apps.accounts.tasks.time.sleep"),
    ):
        result = sync_youtube_channel_ids.func()

    extract.assert_called_once_with(NEW_URL)
    assert result["success"] == 1
    youtuber.refresh_from_db()
    assert youtuber.youtube_channel_id == NEW_ID

"""Re-posting a published availability sheet to the squad's Discord channel.

The thing this feature actually does is @-mention a whole squad role, so most of what is
worth testing is about restraint: who may fire it, which sheets it makes sense for, and
what stops a second click from pinging everyone twice. The message itself only has to
differ from the original publish in one way -- it must not read as a second sheet.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.events.models import AVAILABILITY_REPOST_COOLDOWN, AvailabilityGrid, Event, Squad

CHANNEL = 555000111
ROLE = 777000222


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad wired to a Discord channel and role.

    Returns:
        The squad.

    """
    return Squad.objects.create(
        event=event, name="Eclipse", discord_channel_id=CHANNEL, team_discord_role=ROLE
    )


@pytest.fixture
def grid(squad) -> AvailabilityGrid:
    """Build a published sheet.

    Returns:
        The grid.

    """
    return AvailabilityGrid.objects.create(
        squad=squad,
        title="Week 3",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 7),
        start_time="19:00",
        end_time="21:00",
        slot_duration=60,
        grid_timezone="UTC",
        status=AvailabilityGrid.Status.PUBLISHED,
    )


def _url(grid) -> str:
    """Build the re-post URL for a grid.

    Args:
        grid: The availability grid.

    Returns:
        The URL.

    """
    return reverse(
        "events:availability_repost",
        kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
    )


# --- the message ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_reminder_pings_the_same_role_in_the_same_channel(client, event_admin, grid):
    """The point of a re-post is to reach exactly the people the first post reached."""
    client.force_login(event_admin)
    with patch("apps.events.views.send_discord_channel_message", return_value=True) as send:
        client.post(_url(grid))

    channel, body = send.call_args.args
    assert channel == CHANNEL
    assert f"<@&{ROLE}>" in body
    assert send.call_args.kwargs["allowed_role_ids"] == [str(ROLE)]


@pytest.mark.django_db
def test_the_reminder_does_not_read_as_a_second_sheet(client, event_admin, grid):
    """"New Availability Requested" twice would send riders hunting for a sheet that isn't there."""
    grid.last_notified_at = timezone.now() - AVAILABILITY_REPOST_COOLDOWN - timedelta(minutes=1)
    grid.save(update_fields=["last_notified_at"])
    client.force_login(event_admin)
    with patch("apps.events.views.send_discord_channel_message", return_value=True) as send:
        client.post(_url(grid))

    body = send.call_args.args[1]
    assert "New Availability Requested" not in body
    assert "Availability Reminder" in body
    assert "check your availability is still correct" in body


@pytest.mark.django_db
def test_the_reminder_still_links_to_the_sheet(client, event_admin, grid):
    """A reminder with no link is just noise -- the whole point is one tap back to the grid."""
    client.force_login(event_admin)
    with patch("apps.events.views.send_discord_channel_message", return_value=True) as send:
        client.post(_url(grid))

    assert str(grid.pk) in send.call_args.args[1]


# --- which sheets ---------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("status", [AvailabilityGrid.Status.DRAFT, AvailabilityGrid.Status.CLOSED])
def test_only_a_published_sheet_can_be_reposted(client, event_admin, grid, status):
    """A draft was never announced, and a closed sheet sends riders to a page they cannot act on."""
    grid.status = status
    grid.save(update_fields=["status"])
    client.force_login(event_admin)

    with patch("apps.events.views.send_discord_channel_message") as send:
        response = client.post(_url(grid))

    send.assert_not_called()
    assert response.status_code == 302


# --- the double-ping guard ------------------------------------------------------------


@pytest.mark.django_db
def test_a_second_click_inside_the_cooldown_sends_nothing(client, event_admin, grid):
    """A double-click is not a duplicate row somewhere quiet -- it is everyone's phone, twice."""
    client.force_login(event_admin)
    with patch("apps.events.views.send_discord_channel_message", return_value=True) as send:
        client.post(_url(grid))
        client.post(_url(grid))

    assert send.call_count == 1


@pytest.mark.django_db
def test_the_cooldown_lapses(client, event_admin, grid):
    """It is a debounce, not a one-shot -- a captain must be able to chase a thin sheet again."""
    grid.last_notified_at = timezone.now() - AVAILABILITY_REPOST_COOLDOWN - timedelta(minutes=1)
    grid.save(update_fields=["last_notified_at"])
    client.force_login(event_admin)

    with patch("apps.events.views.send_discord_channel_message", return_value=True) as send:
        client.post(_url(grid))

    assert send.call_count == 1


@pytest.mark.django_db
def test_publishing_starts_the_cooldown(client, event_admin, grid):
    """Publish & Notify is the same ping, so a re-post one second later would be the double."""
    grid.status = AvailabilityGrid.Status.DRAFT
    grid.save(update_fields=["status"])
    client.force_login(event_admin)
    status_url = reverse(
        "events:availability_status",
        kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
    )

    with patch("apps.events.views.send_discord_channel_message", return_value=True) as send:
        client.post(status_url, {"status": "published", "notify": "1"})
        client.post(_url(grid))

    assert send.call_count == 1


@pytest.mark.django_db
def test_a_failed_send_does_not_start_the_cooldown(client, event_admin, grid):
    """The message reached nobody, so making the captain wait 30 minutes would be punitive."""
    client.force_login(event_admin)
    with patch("apps.events.views.send_discord_channel_message", return_value=False):
        client.post(_url(grid))

    grid.refresh_from_db()
    assert grid.last_notified_at is None

    with patch("apps.events.views.send_discord_channel_message", return_value=True) as send:
        client.post(_url(grid))
    assert send.call_count == 1


# --- who may fire it ------------------------------------------------------------------


@pytest.mark.django_db
def test_a_plain_team_member_cannot_repost(client, team_member, grid):
    """Riders see the response page too; the button's gate has to hold at the view."""
    client.force_login(team_member)
    with patch("apps.events.views.send_discord_channel_message") as send:
        client.post(_url(grid))
    send.assert_not_called()


@pytest.mark.django_db
def test_a_squad_captain_can_repost(client, team_member, grid):
    """Chasing a thin sheet is a captain's job, not only an event admin's."""
    grid.squad.captains.add(team_member)
    client.force_login(team_member)

    with patch("apps.events.views.send_discord_channel_message", return_value=True) as send:
        client.post(_url(grid))

    assert send.call_count == 1


@pytest.mark.django_db
def test_repost_requires_a_login(client, grid):
    """Anonymous callers must not be able to make the bot ping a squad role."""
    with patch("apps.events.views.send_discord_channel_message") as send:
        assert client.post(_url(grid)).status_code == 302
    send.assert_not_called()


@pytest.mark.django_db
def test_get_is_rejected(client, event_admin, grid):
    """A link-preview crawler or a prefetch must never be able to fire a squad-wide ping."""
    client.force_login(event_admin)
    with patch("apps.events.views.send_discord_channel_message") as send:
        assert client.get(_url(grid)).status_code == 405
    send.assert_not_called()


# --- where it comes back to -----------------------------------------------------------


@pytest.mark.django_db
def test_return_to_respond_comes_back_to_the_response_page(client, event_admin, grid):
    """The button on the response page has to leave the captain where they were."""
    client.force_login(event_admin)
    with patch("apps.events.views.send_discord_channel_message", return_value=True):
        response = client.post(_url(grid), {"return_to": "respond"})

    assert response.status_code == 302
    assert str(grid.pk) in response.url


@pytest.mark.django_db
def test_an_unknown_return_to_falls_back_to_the_squad_page(client, event_admin, grid):
    """return_to is chosen by name, never used as a URL, so tampering cannot redirect off-site."""
    client.force_login(event_admin)
    with patch("apps.events.views.send_discord_channel_message", return_value=True):
        response = client.post(_url(grid), {"return_to": "https://evil.test/"})

    assert response.url == reverse(
        "events:squad_availability",
        kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id},
    )


# --- the buttons ----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_response_page_shows_the_button_to_a_captain(client, team_member, grid):
    """The captain notices a thin sheet on this page; sending from here saves a round trip."""
    grid.squad.captains.add(team_member)
    client.force_login(team_member)
    body = client.get(
        reverse(
            "events:availability_respond",
            kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
        )
    ).content.decode()

    assert _url(grid) in body
    assert "Re-Post to Discord" in body


@pytest.mark.django_db
def test_the_response_page_hides_the_button_from_a_rider(client, team_member, grid):
    """Offering a control that 403s on click is worse than not offering it."""
    client.force_login(team_member)
    body = client.get(
        reverse(
            "events:availability_respond",
            kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
        )
    ).content.decode()

    assert _url(grid) not in body


@pytest.mark.django_db
def test_no_button_without_a_discord_channel(client, event_admin, grid):
    """There is nowhere to post; the failure would only surface as a warning after the click."""
    grid.squad.discord_channel_id = 0
    grid.squad.save(update_fields=["discord_channel_id"])
    client.force_login(event_admin)
    body = client.get(
        reverse(
            "events:availability_respond",
            kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
        )
    ).content.decode()

    assert _url(grid) not in body


# --- regressions found in review ------------------------------------------------------


@pytest.mark.django_db
def test_two_overlapping_clicks_send_once(client, event_admin, grid):
    """The cooldown is claimed before the send, not after it.

    Reading the stamp and then calling Discord leaves the whole round trip -- hundreds of
    milliseconds -- unguarded: both requests read an empty stamp, both pass, both ping. The
    fake send re-enters the view to stand in for that overlap.
    """
    client.force_login(event_admin)
    sent = []

    def _send_then_reenter(*args, **kwargs):
        sent.append(args)
        if len(sent) == 1:
            # Arrives while the first request is still inside the Discord call.
            client.post(_url(grid))
        return True

    with patch("apps.events.views.send_discord_channel_message", side_effect=_send_then_reenter):
        client.post(_url(grid))

    assert len(sent) == 1


@pytest.mark.django_db
def test_a_silently_published_sheet_is_announced_not_re_reminded(client, event_admin, grid):
    """"Publish only" tells nobody, so the first re-post IS the announcement.

    Asking riders to "check your availability is still correct" on a sheet they have never
    seen is the same failure as calling a reminder a new sheet, pointed the other way.
    """
    grid.status = AvailabilityGrid.Status.DRAFT
    grid.save(update_fields=["status"])
    client.force_login(event_admin)
    status_url = reverse(
        "events:availability_status",
        kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
    )
    client.post(status_url, {"status": "published"})  # no notify -> nothing posted

    with patch("apps.events.views.send_discord_channel_message", return_value=True) as send:
        client.post(_url(grid))

    body = send.call_args.args[1]
    assert "New Availability Requested" in body
    assert "Availability Reminder" not in body


@pytest.mark.django_db
def test_the_wait_is_rounded_up(client, event_admin, grid):
    """Rounding down sends the captain back to a button that refuses them again."""
    grid.last_notified_at = timezone.now() - AVAILABILITY_REPOST_COOLDOWN + timedelta(seconds=100)
    grid.save(update_fields=["last_notified_at"])
    client.force_login(event_admin)

    with patch("apps.events.views.send_discord_channel_message"):
        response = client.post(_url(grid), follow=True)

    text = " ".join(str(m) for m in response.context["messages"])
    assert "2 minutes" in text  # 100s -> 2, not 1


# --- the cooldown is visible, not just enforced ---------------------------------------


@pytest.mark.django_db
def test_the_button_says_why_it_is_unavailable_during_the_cooldown(client, event_admin, grid):
    """The modal promises a ping; a live button that silently warns instead is a broken promise."""
    grid.last_notified_at = timezone.now()
    grid.save(update_fields=["last_notified_at"])
    client.force_login(event_admin)
    body = client.get(
        reverse(
            "events:availability_respond",
            kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
        )
    ).content.decode()

    assert "Re-Post sent recently" in body
    assert "repost-modal" not in body


# --- the squad availability page, the primary surface ---------------------------------


def _squad_url(grid) -> str:
    """Build the squad availability page URL.

    Args:
        grid: The availability grid.

    Returns:
        The URL.

    """
    return reverse(
        "events:squad_availability",
        kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id},
    )


@pytest.mark.django_db
def test_the_squad_page_offers_repost_for_a_published_sheet(client, event_admin, grid):
    """The primary surface: the one page where a captain manages every sheet at once."""
    client.force_login(event_admin)
    body = client.get(_squad_url(grid)).content.decode()

    assert _url(grid) in body
    assert "Re-Post to Discord" in body


@pytest.mark.django_db
def test_the_squad_page_hides_repost_for_a_draft(client, event_admin, grid):
    """A draft has not been announced; Publish & Notify is the control for that."""
    grid.status = AvailabilityGrid.Status.DRAFT
    grid.save(update_fields=["status"])
    client.force_login(event_admin)

    assert _url(grid) not in client.get(_squad_url(grid)).content.decode()


@pytest.mark.django_db
def test_the_squad_page_hides_repost_without_a_discord_channel(client, event_admin, grid):
    """There is nowhere to post it."""
    grid.squad.discord_channel_id = 0
    grid.squad.save(update_fields=["discord_channel_id"])
    client.force_login(event_admin)

    assert _url(grid) not in client.get(_squad_url(grid)).content.decode()

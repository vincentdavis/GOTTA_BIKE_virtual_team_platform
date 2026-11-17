"""Squads' weekly posting schedules: a sheet made, posted, reminded about and closed on time.

The clock is passed in, so every test says exactly when "now" is. The squad posts on Fridays at
18:00 London time, for the week starting seven days later, reminds on days 2 and 4 (Sunday and
Tuesday) and closes on day 6 (Thursday). London is on GMT in November, so local and UTC agree.
"""

import re
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.discord_service import ChannelPostResult
from apps.accounts.models import GuildMember
from apps.events import availability_schedules
from apps.events.availability_schedules import run_schedule, run_schedules, upcoming
from apps.events.models import (
    AvailabilityGrid,
    AvailabilityGridTemplate,
    AvailabilityResponse,
    AvailabilitySchedule,
    Event,
    Squad,
    SquadMember,
)

POSTED = ChannelPostResult(ok=True, status_code=200)
FAILED = ChannelPostResult(ok=False, status_code=500)
CHANNEL, ROLE = 111, 222

FRIDAY = date(2026, 11, 20)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    """Build a UTC time on a day.

    Returns:
        The time.

    """
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _post():
    return patch("apps.events.views.post_discord_channel_message", return_value=POSTED)


@pytest.fixture
def event(db) -> Event:
    """Build a league that runs into the new year.

    Returns:
        The event.

    """
    return Event.objects.create(title="Ladder", start_date=date(2026, 11, 1), end_date=date(2027, 1, 31), visible=True)


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad with a channel and a role.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="Alpha", discord_channel_id=CHANNEL, team_discord_role=ROLE)


@pytest.fixture
def template(squad) -> AvailabilityGridTemplate:
    """Build the squad's evening template: 19:00-21:00 London, hour slots, a week long.

    Returns:
        The template.

    """
    return AvailabilityGridTemplate.objects.create(
        squad=squad,
        name="Evenings",
        start_time="19:00",
        end_time="21:00",
        grid_timezone="Europe/London",
        slot_duration=60,
        default_length_days=7,
        max_races_question=True,
    )


@pytest.fixture
def schedule(squad, template) -> AvailabilitySchedule:
    """Build the squad's schedule, saved the day before its first post.

    Returns:
        The schedule.

    """
    return AvailabilitySchedule.objects.create(
        squad=squad,
        template=template,
        weekday=4,
        post_time="18:00",
        schedule_timezone="Europe/London",
        starts_after_days=7,
        reminder_days=[2, 4],
        close_after_days=6,
        not_before=at(FRIDAY - timedelta(days=1), 9),
        base_url="https://app.example.test/",
    )


def _rider(user_model, name: str, squad: Squad, *, discord_id: str = "") -> object:
    user = user_model.objects.create_user(
        username=name, email=f"{name}@example.test", first_name=name.title(), discord_id=discord_id
    )
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _fresh(schedule: AvailabilitySchedule) -> AvailabilitySchedule:
    return AvailabilitySchedule.objects.select_related("squad__event", "template").get(pk=schedule.pk)


# -- Posting -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_weeks_sheet_is_made_published_and_posted_once(schedule):
    with _post() as send:
        run_schedules(at(FRIDAY, 18, 5))
        run_schedules(at(FRIDAY, 18, 15))

    grid = AvailabilityGrid.objects.get()
    assert grid.schedule == schedule
    assert grid.scheduled_for == FRIDAY
    assert grid.status == AvailabilityGrid.Status.PUBLISHED
    assert (grid.start_date, grid.end_date) == (date(2026, 11, 27), date(2026, 12, 3))
    assert (grid.start_time, grid.end_time, grid.slot_duration) == ("19:00", "21:00", 60)
    assert grid.max_races_question
    assert grid.created_by is None

    send.assert_called_once()
    channel, body = send.call_args.args
    assert channel == CHANNEL
    assert "**New Availability Requested**" in body
    assert f"https://app.example.test/events/{schedule.squad.event_id}/squads/{schedule.squad_id}/availability/" in body
    assert f"<@&{ROLE}>" in body
    assert send.call_args.kwargs["allowed_role_ids"] == [str(ROLE)]
    assert "Published" in _fresh(schedule).last_note


@pytest.mark.django_db
def test_nothing_is_posted_before_the_time(schedule):
    with _post() as send:
        run_schedules(at(FRIDAY, 17, 55))

    assert not AvailabilityGrid.objects.exists()
    send.assert_not_called()


@pytest.mark.django_db
def test_a_post_missed_by_more_than_half_a_day_is_dropped(schedule):
    with _post() as send:
        run_schedules(at(FRIDAY, 18) + availability_schedules.CATCH_UP + timedelta(minutes=1))

    assert not AvailabilityGrid.objects.exists()
    send.assert_not_called()


@pytest.mark.django_db
def test_a_schedule_saved_after_this_weeks_time_waits_for_next_week(schedule):
    schedule.not_before = at(FRIDAY, 18, 30)
    schedule.save()

    with _post() as send:
        run_schedules(at(FRIDAY, 18, 40))
    assert not AvailabilityGrid.objects.exists()
    send.assert_not_called()

    with _post():
        run_schedules(at(FRIDAY + timedelta(days=7), 18, 5))
    assert AvailabilityGrid.objects.get().scheduled_for == FRIDAY + timedelta(days=7)


@pytest.mark.django_db
def test_a_skipped_week_makes_no_sheet(schedule):
    schedule.skip_date = FRIDAY
    schedule.save()

    with _post() as send:
        run_schedules(at(FRIDAY, 18, 5))

    assert not AvailabilityGrid.objects.exists()
    send.assert_not_called()
    assert _fresh(schedule).last_note == "Skipped the Fri 20 Nov sheet, as asked."


@pytest.mark.django_db
def test_a_week_that_already_has_a_sheet_made_by_hand_is_skipped(schedule, squad):
    by_hand = AvailabilityGrid.objects.create(
        squad=squad,
        title="Captain's own",
        start_date=date(2026, 11, 30),
        end_date=date(2026, 12, 6),
        start_time="19:00",
        end_time="21:00",
        slot_duration=60,
    )

    with _post() as send:
        run_schedules(at(FRIDAY, 18, 5))

    assert list(AvailabilityGrid.objects.all()) == [by_hand]
    send.assert_not_called()
    assert '"Captain\'s own"' in _fresh(schedule).last_note


@pytest.mark.django_db
def test_a_sheet_ending_the_day_before_is_not_a_clash(schedule, squad):
    AvailabilityGrid.objects.create(
        squad=squad,
        start_date=date(2026, 11, 20),
        end_date=date(2026, 11, 26),
        start_time="19:00",
        end_time="21:00",
        slot_duration=60,
    )

    with _post():
        run_schedules(at(FRIDAY, 18, 5))

    assert AvailabilityGrid.objects.filter(schedule=schedule).count() == 1


@pytest.mark.django_db
def test_draft_mode_makes_a_draft_and_dms_the_captains(schedule, squad, user_model):
    schedule.publish_mode = AvailabilitySchedule.PublishMode.DRAFT
    schedule.save()
    captain = user_model.objects.create_user(username="cap", email="cap@example.test", discord_id="9001")
    squad.captains.add(captain)

    with _post() as send, patch("apps.accounts.discord_service.send_discord_dm", return_value=True) as dm:
        run_schedules(at(FRIDAY, 18, 5))

    grid = AvailabilityGrid.objects.get()
    assert grid.status == AvailabilityGrid.Status.DRAFT
    send.assert_not_called()
    dm.assert_called_once()
    assert dm.call_args.args[0] == "9001"
    assert f"https://app.example.test/events/{squad.event_id}/squads/{squad.pk}/availability/" in dm.call_args.args[1]


@pytest.mark.django_db
def test_a_schedule_with_its_template_deleted_says_so(schedule, template):
    template.delete()

    with _post() as send:
        run_schedules(at(FRIDAY, 18, 5))

    send.assert_not_called()
    assert "template has been deleted" in _fresh(schedule).last_note


@pytest.mark.django_db
def test_a_paused_schedule_does_nothing(schedule):
    schedule.active = False
    schedule.save()

    with _post() as send:
        run_schedules(at(FRIDAY, 18, 5))

    assert not AvailabilityGrid.objects.exists()
    send.assert_not_called()


# -- Reminders and closing -------------------------------------------------------------------


@pytest.fixture
def posted(schedule) -> AvailabilityGrid:
    """Post this week's sheet.

    Returns:
        The sheet.

    """
    with _post():
        run_schedules(at(FRIDAY, 18, 5))
    return AvailabilityGrid.objects.get()


@pytest.mark.django_db
def test_a_reminder_mentions_only_the_riders_who_havent_answered(schedule, squad, posted, user_model):
    answered = _rider(user_model, "ana", squad, discord_id="1001")
    _rider(user_model, "bo", squad, discord_id="1002")
    _rider(user_model, "cy", squad, discord_id="1003")
    _rider(user_model, "di", squad)  # no Discord account to mention
    GuildMember.objects.create(discord_id="1003", username="cy", date_left=at(FRIDAY, 9))  # left the server
    AvailabilityResponse.objects.create(grid=posted, user=answered, available_cells=[])

    with _post() as send:
        run_schedules(at(FRIDAY + timedelta(days=2), 18, 5))
        run_schedules(at(FRIDAY + timedelta(days=2), 18, 15))

    send.assert_called_once()
    body = send.call_args.args[1]
    assert "**Availability Reminder**" in body
    assert "<@1002>" in body
    assert "<@1001>" not in body
    assert "<@1003>" not in body
    assert f"<@&{ROLE}>" not in body
    assert send.call_args.kwargs["allowed_user_ids"] == ["1002"]
    assert send.call_args.kwargs["allowed_role_ids"] is None
    posted.refresh_from_db()
    assert posted.reminders_sent == [2]
    assert _fresh(schedule).last_note == f'Reminded 1 rider who hadn\'t answered "{posted.title}".'


@pytest.mark.django_db
def test_several_reminders_due_at_once_send_only_the_latest(schedule, squad, posted, user_model):
    _rider(user_model, "bo", squad, discord_id="1002")

    with _post() as send:
        run_schedules(at(FRIDAY + timedelta(days=4), 18, 30))

    send.assert_called_once()
    posted.refresh_from_db()
    assert posted.reminders_sent == [2, 4]


@pytest.mark.django_db
def test_a_failed_reminder_is_tried_again(schedule, squad, posted, user_model):
    _rider(user_model, "bo", squad, discord_id="1002")

    with patch("apps.events.views.post_discord_channel_message", return_value=FAILED):
        run_schedules(at(FRIDAY + timedelta(days=2), 18, 5))
    posted.refresh_from_db()
    assert posted.reminders_sent == []
    assert "Couldn't post the reminder" in _fresh(schedule).last_note

    with _post() as send:
        run_schedules(at(FRIDAY + timedelta(days=2), 18, 15))
    send.assert_called_once()
    posted.refresh_from_db()
    assert posted.reminders_sent == [2]


@pytest.mark.django_db
def test_no_reminder_when_everyone_has_answered(schedule, squad, posted, user_model):
    rider = _rider(user_model, "ana", squad, discord_id="1001")
    AvailabilityResponse.objects.create(grid=posted, user=rider, available_cells=[])

    with _post() as send:
        run_schedules(at(FRIDAY + timedelta(days=2), 18, 5))

    send.assert_not_called()
    assert "everyone has answered" in _fresh(schedule).last_note


@pytest.mark.django_db
def test_the_sheet_closes_on_time_and_is_not_reminded_about_after(schedule, squad, posted, user_model):
    _rider(user_model, "bo", squad, discord_id="1002")
    posted.reminders_sent = [2, 4]
    posted.save()

    with _post() as send:
        run_schedules(at(FRIDAY + timedelta(days=6), 17, 55))
    posted.refresh_from_db()
    assert posted.status == AvailabilityGrid.Status.PUBLISHED

    schedule.reminder_days = [2, 4, 5]
    schedule.save()
    with _post() as send:
        run_schedules(at(FRIDAY + timedelta(days=6), 18, 5))
    posted.refresh_from_db()
    assert posted.status == AvailabilityGrid.Status.CLOSED
    send.assert_not_called()


@pytest.mark.django_db
def test_a_sheet_closed_by_hand_gets_no_reminders(schedule, squad, posted, user_model):
    _rider(user_model, "bo", squad, discord_id="1002")
    AvailabilityGrid.objects.filter(pk=posted.pk).update(status=AvailabilityGrid.Status.CLOSED)

    with _post() as send:
        run_schedule(_fresh(schedule), at(FRIDAY + timedelta(days=2), 18, 5))

    send.assert_not_called()


# -- The preview --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_preview_lists_the_coming_posts_with_their_days(schedule):
    coming = upcoming(schedule, at(FRIDAY, 12))

    assert [u.post_at for u in coming] == [
        at(FRIDAY, 18),
        at(FRIDAY + timedelta(days=7), 18),
        at(FRIDAY + timedelta(days=14), 18),
    ]
    first = coming[0]
    assert (first.first_day, first.last_day) == (date(2026, 11, 27), date(2026, 12, 3))
    assert first.reminders == (at(FRIDAY + timedelta(days=2), 18), at(FRIDAY + timedelta(days=4), 18))
    assert first.closes_at == at(FRIDAY + timedelta(days=6), 18)


@pytest.mark.django_db
def test_the_preview_follows_daylight_saving(schedule):
    """Denver leaves daylight saving on 1 Nov 2026; 18:00 is 00:00 UTC before and 01:00 after."""
    schedule.schedule_timezone = "America/Denver"
    schedule.not_before = datetime(2026, 10, 1, tzinfo=UTC)
    coming = upcoming(schedule, datetime(2026, 10, 20, tzinfo=UTC))

    assert [u.post_at.hour for u in coming] == [0, 0, 1]


# -- The page -----------------------------------------------------------------------------------


@pytest.fixture
def captain(squad, user_model):
    """Make a captain of the squad.

    Returns:
        The captain.

    """
    user = user_model.objects.create_user(
        username="cap", email="cap@example.test", permission_overrides={"team_member": True}
    )
    squad.captains.add(user)
    return user


@pytest.mark.django_db
def test_a_captain_sets_up_a_schedule(client, event, squad, template, captain):
    url = reverse("events:availability_schedule", args=[event.pk, squad.pk])
    client.force_login(captain)

    page = client.get(url).content.decode()
    assert "Set up a schedule" in page
    assert 'value="18:00"' in page
    assert "A sheet that would start after the event ends (Sun 31 Jan 2027) is never posted." in page

    response = client.post(
        url,
        {
            "template": template.pk,
            "weekday": 4,
            "post_time": "18:00",
            "schedule_timezone": "Europe/London",
            "starts_after_days": 7,
            "reminder_days": ["2", "4"],
            "close_after_days": 6,
            "publish_mode": "publish",
        },
    )

    assert response.status_code == 302
    schedule = AvailabilitySchedule.objects.get(squad=squad)
    assert schedule.template == template
    assert schedule.reminder_days == [2, 4]
    assert schedule.base_url == "http://testserver/"
    assert schedule.updated_by == captain
    assert schedule.active
    page = client.get(url).content.decode()
    assert "Next post:" in page
    assert "Coming sheets" in page


@pytest.mark.django_db
def test_a_reminder_after_closing_is_refused(client, event, squad, template, captain):
    client.force_login(captain)
    response = client.post(
        reverse("events:availability_schedule", args=[event.pk, squad.pk]),
        {
            "template": template.pk,
            "weekday": 4,
            "post_time": "18:00",
            "schedule_timezone": "Europe/London",
            "starts_after_days": 7,
            "reminder_days": ["2", "6"],
            "close_after_days": 6,
            "publish_mode": "publish",
        },
    )

    assert response.status_code == 200
    assert "remind before then" in response.content.decode()
    assert not AvailabilitySchedule.objects.exists()


@pytest.mark.django_db
def test_another_squads_unshared_template_cannot_be_used(client, event, squad, captain):
    other = Squad.objects.create(event=event, name="Bravo")
    private = AvailabilityGridTemplate.objects.create(
        squad=other, name="Theirs", start_time="19:00", end_time="21:00", slot_duration=60
    )
    client.force_login(captain)
    response = client.post(
        reverse("events:availability_schedule", args=[event.pk, squad.pk]),
        {
            "template": private.pk,
            "weekday": 4,
            "post_time": "18:00",
            "schedule_timezone": "UTC",
            "starts_after_days": 7,
            "publish_mode": "publish",
        },
    )

    assert response.status_code == 200
    assert not AvailabilitySchedule.objects.exists()


@pytest.mark.django_db
def test_someone_who_cant_manage_the_squad_is_turned_away(client, event, squad, team_member):
    client.force_login(team_member)
    response = client.get(reverse("events:availability_schedule", args=[event.pk, squad.pk]))

    assert response.status_code == 302
    assert response.url == reverse("events:event_detail", args=[event.pk])


@pytest.mark.django_db
def test_the_preview_redraws_from_the_unsaved_form(client, event, squad, template, captain):
    client.force_login(captain)
    response = client.post(
        reverse("events:availability_schedule_preview", args=[event.pk, squad.pk]),
        {
            "template": template.pk,
            "weekday": 0,
            "post_time": "09:30",
            "schedule_timezone": "UTC",
            "starts_after_days": 3,
            "reminder_days": ["1"],
            "publish_mode": "publish",
        },
    )

    body = response.content.decode()
    assert body.count("<tr") == 4  # header and three coming posts
    assert "Mon" in body
    assert "09:30" in body
    assert "Stays open" in body


@pytest.mark.django_db
def test_pause_skip_and_delete(client, event, squad, schedule, captain):
    client.force_login(captain)
    action = reverse("events:availability_schedule_action", args=[event.pk, squad.pk])

    client.post(action, {"action": "pause"})
    assert not _fresh(schedule).active

    client.post(action, {"action": "resume"})
    resumed = _fresh(schedule)
    assert resumed.active
    assert abs(resumed.not_before - timezone.now()) < timedelta(minutes=1)

    client.post(action, {"action": "skip"})
    skipped = _fresh(schedule)
    assert skipped.skip_date is not None
    assert skipped.skip_date.weekday() == 4

    client.post(action, {"action": "unskip"})
    assert _fresh(schedule).skip_date is None

    with _post():
        run_schedules(at(FRIDAY, 18, 5))
    made = AvailabilityGrid.objects.filter(schedule=schedule).count()
    response = client.post(action, {"action": "delete"})
    assert response.url == reverse("events:squad_availability", args=[event.pk, squad.pk])
    assert not AvailabilitySchedule.objects.exists()
    assert AvailabilityGrid.objects.count() == made  # the sheets it made stay


@pytest.mark.django_db
def test_the_squad_cards_say_where_each_schedule_stands(client, event, squad, schedule, superuser):
    paused = Squad.objects.create(event=event, name="Bravo")
    AvailabilitySchedule.objects.create(squad=paused, template=None, active=False)
    Squad.objects.create(event=event, name="Charlie")
    client.force_login(superuser)

    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    def line(name: str) -> str:
        pk = Squad.objects.get(name=name).pk
        found = re.search(rf'<p[^>]*id="squad-schedule-{pk}"[^>]*>(.*?)</p>', body, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", found.group(1)).split())

    assert line("Alpha").startswith("Auto Next ")
    assert line("Bravo").startswith("Paused")
    assert line("Charlie").startswith("No schedule")


@pytest.mark.django_db
def test_scheduled_sheets_are_marked_on_the_availability_page(client, event, squad, posted, captain):
    client.force_login(captain)
    body = client.get(reverse("events:squad_availability", args=[event.pk, squad.pk])).content.decode()

    assert "Made by the posting schedule" in body
    assert "Edit schedule" in body

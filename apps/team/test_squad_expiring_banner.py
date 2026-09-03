"""The captain banner: squad-mates whose Race Verified status is expiring.

Almost all of the behaviour worth testing here is what the banner must NOT show. It is
opt-in per squad, scoped to events that have not ended, limited to actual squad members, and
limited to the squads the viewer actually captains -- and each of those is a way for one
captain to be shown people who are not their problem, or worse, not theirs to see.

The expiry window itself is deliberately not redefined here: it comes from
services.is_expiring_soon, the same definition the rider's own banner and the DM task use, so
a captain can never be shown a warning the rider themselves does not have.
"""

from datetime import date, timedelta

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, Squad, SquadMember
from apps.team.models import RaceReadyRecord


@pytest.fixture(autouse=True)
def _clear_cache():
    """Drop the per-user banner cache between tests.

    The context processor caches for six minutes, so without this a test's count leaks
    into the next one and the failures land on whichever test happens to run second.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def event(db) -> Event:
    """Build a running event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today - timedelta(days=7), end_date=today + timedelta(days=30), visible=True
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad that has opted in.

    Returns:
        The squad.

    """
    return Squad.objects.create(
        event=event, name="Affinity", notify_captain_expiring_verification=True
    )


@pytest.fixture
def captain(user_model, squad):
    """Build the squad's captain.

    Returns:
        The captain.

    """
    user = user_model.objects.create_user(
        username="cap", email="cap@example.test", first_name="Cap", last_name="Tain",
        permission_overrides={"team_member": True},
    )
    squad.captains.add(user)
    return user


def _expiring_member(user_model, squad, name: str, days: int, *, status=SquadMember.Status.MEMBER):
    """Add a squad member whose weight verification expires in ``days`` days.

    Args:
        user_model: The active user model.
        squad: The squad to join.
        name: First name, also the username.
        days: Days until the verification lapses.
        status: The membership status to record.

    Returns:
        The member.

    """
    user = user_model.objects.create_user(
        username=name.lower(), email=f"{name.lower()}@example.test", first_name=name, last_name="Rider"
    )
    SquadMember.objects.create(squad=squad, user=user, status=status)
    RaceReadyRecord.objects.create(
        user=user,
        verify_type="weight_light",
        status=RaceReadyRecord.Status.VERIFIED,
        record_date=timezone.localdate() - timedelta(days=30 - days),
    )
    return user


def _banner(client, viewer) -> str:
    """Render any page and return the body, so the banner can be inspected.

    Args:
        client: Test client.
        viewer: The signed-in user.

    Returns:
        The response body.

    """
    client.force_login(viewer)
    return client.get(reverse("accounts:profile")).content.decode()


def _warm_config(user) -> None:
    """Run one summary so Constance has seeded its defaults before anything is measured.

    The first call in a test inserts a ``constance_constance`` row per key it reads, four
    statements each. Left unwarmed that lands ~20 queries of slack on whichever capture runs
    first -- and in a ``few <= many`` comparison it lands on the SMALLER side, which is
    exactly enough headroom to hide one reintroduced query per rider.

    Must be called AFTER the squad has members: the function returns early on an empty squad
    before it reads any config, so warming on an empty one warms nothing.

    Args:
        user: The captain to compute for.

    """
    _summary(user)


def _summary(user) -> dict:
    """Compute the summary directly, bypassing the banner cache.

    Args:
        user: The captain.

    Returns:
        The summary dict.

    """
    from apps.team.services import squad_expiring_summary

    return squad_expiring_summary(user)


# --- the opt-in ------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_banner_shows_for_a_captain_of_an_opted_in_squad(client, user_model, squad, captain):
    """The whole feature: a captain is told how many squad-mates need a nudge."""
    _expiring_member(user_model, squad, "Ana", 5)

    body = _banner(client, captain)

    assert "Remind your Squad-mates: 1 Expiring" in body


@pytest.mark.django_db
def test_no_banner_when_the_squad_has_not_opted_in(client, user_model, squad, captain):
    """Default off -- a squad that is not chasing verifications does not want a banner."""
    squad.notify_captain_expiring_verification = False
    squad.save(update_fields=["notify_captain_expiring_verification"])
    _expiring_member(user_model, squad, "Ana", 5)

    assert "Remind your Squad-mates" not in _banner(client, captain)


@pytest.mark.django_db
def test_the_option_defaults_to_off(event):
    """Stated in the request, and it decides whether this feature is opt-in or opt-out."""
    assert Squad.objects.create(event=event, name="Fresh").notify_captain_expiring_verification is False


# --- events that have ended ------------------------------------------------------------


@pytest.mark.django_db
def test_no_banner_once_the_event_has_ended(client, user_model, squad, captain, event):
    """A finished event cannot be raced, so chasing verifications for it is pure noise."""
    event.end_date = date.today() - timedelta(days=1)
    event.save(update_fields=["end_date"])
    _expiring_member(user_model, squad, "Ana", 5)

    assert "Remind your Squad-mates" not in _banner(client, captain)


@pytest.mark.django_db
def test_the_banner_shows_on_the_event_final_day(client, user_model, squad, captain, event):
    """The last day is still raceable -- an exclusive bound would drop the banner a day early."""
    event.end_date = date.today()
    event.save(update_fields=["end_date"])
    _expiring_member(user_model, squad, "Ana", 5)

    assert "Remind your Squad-mates: 1 Expiring" in _banner(client, captain)


# --- who counts as a squad-mate --------------------------------------------------------


@pytest.mark.django_db
def test_a_pending_applicant_is_not_a_squad_mate(client, user_model, squad, captain):
    """They have not joined; a captain chasing their verification is chasing a stranger."""
    _expiring_member(user_model, squad, "Ana", 5, status=SquadMember.Status.PENDING)

    assert "Remind your Squad-mates" not in _banner(client, captain)


@pytest.mark.django_db
def test_a_member_whose_verification_is_not_expiring_is_not_counted(client, user_model, squad, captain):
    """Otherwise the count is squad size, not work to do."""
    _expiring_member(user_model, squad, "Ana", 5)
    healthy = user_model.objects.create_user(username="fine", email="fine@example.test", first_name="Fine")
    SquadMember.objects.create(squad=squad, user=healthy, status=SquadMember.Status.MEMBER)
    RaceReadyRecord.objects.create(
        user=healthy,
        verify_type="weight_light",
        status=RaceReadyRecord.Status.VERIFIED,
        record_date=timezone.localdate(),
    )

    assert "Remind your Squad-mates: 1 Expiring" in _banner(client, captain)


@pytest.mark.django_db
def test_an_already_lapsed_rider_is_counted(client, user_model, squad, captain):
    """The worst case, not an excluded one: they cannot race at all.

    The rider's own banner still excludes lapsed records -- "renew this" and "you have lost
    Race Verified" are different things to tell the person themselves -- but to a captain
    chasing a roster they are one list, so the captain view uses a wider predicate.
    """
    _expiring_member(user_model, squad, "Ana", -5)

    assert "Remind your Squad-mates: 1 Expiring" in _banner(client, captain)


@pytest.mark.django_db
def test_a_lapsed_rider_reads_as_expired_not_as_negative_days(client, user_model, squad, captain):
    """"-5 days" is the value that reads as a bug rather than a state."""
    _expiring_member(user_model, squad, "Ana", -5)
    client.force_login(captain)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()

    assert "expired 5 days ago" in body
    assert "-5 day" not in body


@pytest.mark.django_db
def test_lapsed_riders_sort_above_the_merely_expiring(client, user_model, squad, captain):
    """Chase the rider who cannot race before the one who still can."""
    _expiring_member(user_model, squad, "Soon", 2)
    _expiring_member(user_model, squad, "Gone", -8)
    client.force_login(captain)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()

    assert body.index("Gone Rider") < body.index("Soon Rider")


@pytest.mark.django_db
def test_the_rider_own_banner_still_excludes_lapsed_records(user_model, squad):
    """Widening the captain view must not widen the rider's, or the DM task starts lying.

    is_expiring_soon is the shared definition behind the rider banner AND
    warn_expiring_verifications; if this ever starts returning True for a lapsed record,
    riders get DMs telling them to renew something that has already gone.
    """
    from apps.team.services import is_expiring_soon, needs_captain_attention

    assert needs_captain_attention(-5) is True
    assert is_expiring_soon(-5) is False

    # The window's own edge, which nothing else pins. warn_within is passed explicitly so the
    # assertion does not depend on whatever EXPIRE_WARNING_DAYS happens to be configured as.
    assert needs_captain_attention(15, warn_within=15) is True
    assert needs_captain_attention(16, warn_within=15) is False


@pytest.mark.django_db
def test_a_verification_that_never_expires_is_not_flagged(user_model, squad, captain):
    """A None expiry means "configured never to lapse", not "lapsed long ago"."""
    from apps.team.services import needs_captain_attention

    assert needs_captain_attention(None) is False


# --- whose squads ----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_vice_captain_gets_the_banner_too(client, user_model, squad, captain):
    """Chasing a thin roster is a vice-captain's job as much as the captain's."""
    vc = user_model.objects.create_user(
        username="vc", email="vc@example.test", permission_overrides={"team_member": True}
    )
    squad.vice_captains.add(vc)
    _expiring_member(user_model, squad, "Ana", 5)

    assert "Remind your Squad-mates: 1 Expiring" in _banner(client, vc)


@pytest.mark.django_db
def test_a_plain_member_does_not_see_another_squads_business(client, user_model, squad, captain):
    """A roster of other people's deadlines belongs only to whoever leads them."""
    rider = _expiring_member(user_model, squad, "Ana", 5)
    rider.permission_overrides = {"team_member": True}
    rider.save(update_fields=["permission_overrides"])

    assert "Remind your Squad-mates" not in _banner(client, rider)


@pytest.mark.django_db
def test_a_captain_of_one_squad_does_not_see_another(user_model, event, captain):
    """Two squads on one event must not bleed into each other's banners."""
    other = Squad.objects.create(
        event=event, name="Other", notify_captain_expiring_verification=True
    )
    _expiring_member(user_model, other, "Ana", 5)

    assert _summary(captain)["rider_count"] == 0


@pytest.mark.django_db
def test_a_rider_in_two_of_my_squads_counts_once(user_model, event, captain, squad):
    """The banner counts people to remind, and that is one person however many squads they are in."""
    second = Squad.objects.create(
        event=event, name="Second", notify_captain_expiring_verification=True
    )
    second.captains.add(captain)
    rider = _expiring_member(user_model, squad, "Ana", 5)
    SquadMember.objects.create(squad=second, user=rider, status=SquadMember.Status.MEMBER)

    summary = _summary(captain)

    assert summary["rider_count"] == 1
    assert len(summary["squads"]) == 2  # listed under both, since both are theirs to chase


# --- the modal -------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_modal_lists_names_days_and_a_profile_link(client, user_model, squad, captain):
    """The request's shape: name linking to the profile, and days left."""
    rider = _expiring_member(user_model, squad, "Ana", 5)
    client.force_login(captain)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()

    assert "Ana Rider" in body
    assert reverse("accounts:public_profile", args=[rider.pk]) in body
    assert "5 days" in body
    assert "Affinity" in body


@pytest.mark.django_db
def test_the_modal_sorts_most_urgent_first_within_a_squad(client, user_model, squad, captain):
    """The list exists to answer "who do I chase today"."""
    _expiring_member(user_model, squad, "Later", 9)
    _expiring_member(user_model, squad, "Sooner", 2)
    client.force_login(captain)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()

    assert body.index("Sooner Rider") < body.index("Later Rider")


@pytest.mark.django_db
def test_a_verification_expiring_today_reads_as_today(client, user_model, squad, captain):
    """"0 days" is the one value that reads as a bug rather than a deadline."""
    _expiring_member(user_model, squad, "Ana", 0)
    client.force_login(captain)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()

    assert "today" in body
    assert "0 day" not in body


@pytest.mark.django_db
def test_the_modal_shows_nothing_for_a_member_of_no_such_squad(client, team_member):
    """The query is the gate -- an ordinary member must not be able to read a captain's list."""
    client.force_login(team_member)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()

    assert "everyone is up to date" in body


@pytest.mark.django_db
def test_the_modal_requires_a_login(client):
    """It lists other riders' verification deadlines."""
    assert client.get(reverse("team:squad_expiring_modal")).status_code == 302


# --- regressions found in review ------------------------------------------------------


@pytest.mark.django_db
def test_the_query_count_does_not_grow_with_squad_size(user_model, squad, captain):
    """The real cost here is Constance, not the ORM, and it used to scale per record.

    ``RaceReadyRecord.days_remaining`` goes through ``validity_days``, which reads all four
    expiry windows from Constance on every access, and no Constance cache backend is
    configured -- so each access is a SELECT. This runs in a context processor on every
    authenticated render, so leaving the property to do the reads cost hundreds of queries a
    pageview for a captain of a full squad -- measured at ~790 Constance SELECTs for a
    60-rider squad, now 5, flat.

    Config is warmed first and the assertion is EQUALITY. An earlier version compared an
    unwarmed 2-rider baseline against a warm 20-rider one with <=, which left ~20 queries of
    slack on the smaller side -- enough that reintroducing one config read per rider still
    passed.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for i in range(2):
        _expiring_member(user_model, squad, f"Small{i}", 5)
    _warm_config(captain)
    with CaptureQueriesContext(connection) as small:
        _summary(captain)

    for i in range(18):
        _expiring_member(user_model, squad, f"Big{i}", 5)
    with CaptureQueriesContext(connection) as big:
        _summary(captain)

    # EQUALITY, not <=. Ten times the riders must cost the same, because a single
    # reintroduced per-rider read is the whole regression -- and a <= comparison with an
    # unwarmed baseline tolerates exactly that.
    assert len(big.captured_queries) == len(small.captured_queries), (
        f"{len(small.captured_queries)} queries for 2 riders, {len(big.captured_queries)} for 20"
    )


@pytest.mark.django_db
def test_a_playing_captain_is_not_in_their_own_reminder_list(client, user_model, squad, captain):
    """They have their own banner for their own verification; counting it twice is one problem, twice."""
    SquadMember.objects.create(squad=squad, user=captain, status=SquadMember.Status.MEMBER)
    RaceReadyRecord.objects.create(
        user=captain, verify_type="weight_light", status=RaceReadyRecord.Status.VERIFIED,
        record_date=timezone.localdate() - timedelta(days=28),
    )

    assert _summary(captain)["rider_count"] == 0
    assert "Remind your Squad-mates" not in _banner(client, captain)


@pytest.mark.django_db
def test_a_hidden_event_does_not_banner_its_captains(client, user_model, squad, captain, event):
    """Hidden from riders, so it should not be pushing work at captains either.

    Every other active-event query in the project filters event__visible; diverging here
    would make this the one surface that resurrects a hidden event.
    """
    event.visible = False
    event.save(update_fields=["visible"])
    _expiring_member(user_model, squad, "Ana", 5)

    assert "Remind your Squad-mates" not in _banner(client, captain)


@pytest.mark.django_db
def test_no_banner_for_a_user_the_modal_would_refuse(client, user_model, squad):
    """A banner whose modal 403s leaves the dialog spinning forever -- worse than no banner."""
    lapsed_member = user_model.objects.create_user(
        username="norole", email="norole@example.test"
    )
    squad.captains.add(lapsed_member)
    _expiring_member(user_model, squad, "Ana", 5)

    assert "Remind your Squad-mates" not in _banner(client, lapsed_member)


@pytest.mark.django_db
def test_the_modal_refuses_an_authenticated_non_team_member(client, user):
    """The view's own gate, tested directly rather than assumed from the banner's."""
    client.force_login(user)

    assert client.get(reverse("team:squad_expiring_modal")).status_code == 403


@pytest.mark.django_db
def test_reopening_the_dialog_cannot_show_the_previous_list(client, user_model, squad, captain):
    """Without a reset, a second open shows last time's names until the fetch returns."""
    _expiring_member(user_model, squad, "Ana", 5)
    body = _banner(client, captain)

    trigger = body[body.index('hx-get="/team/verification/squad-expiring/"') - 400 :]
    trigger = trigger[: trigger.index("</button>")]
    assert "squad-expiring-body" in trigger
    assert "loading-spinner" in trigger  # reset to the spinner before opening


# --- riders with nothing verified -----------------------------------------------------


def _bare_member(user_model, squad, name: str):
    """Add a squad member holding no verified record at all.

    Args:
        user_model: The active user model.
        squad: The squad to join.
        name: First name, also the username.

    Returns:
        The member.

    """
    user = user_model.objects.create_user(
        username=name.lower(), email=f"{name.lower()}@example.test", first_name=name, last_name="Rider"
    )
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


@pytest.mark.django_db
def test_a_member_with_nothing_verified_is_counted(client, user_model, squad, captain):
    """The rider who never started is at least as far from racing as the one who lapsed."""
    _bare_member(user_model, squad, "Ana")

    assert "Remind your Squad-mates: 1 Expiring" in _banner(client, captain)


@pytest.mark.django_db
def test_nothing_verified_reads_as_a_state_not_a_deadline(client, user_model, squad, captain):
    """There is no number of days to show, so the row must not pretend there is one."""
    _bare_member(user_model, squad, "Ana")
    client.force_login(captain)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()

    # Scoped to the row: "nothing verified" also appears in the modal's intro sentence, so an
    # unscoped check passes even when no row rendered at all.
    row = body[body.index("Ana Rider") : body.index("</li>", body.index("Ana Rider"))]
    assert "nothing verified" in row
    assert "badge-error" in row
    assert "None day" not in row
    assert "0 day" not in row


@pytest.mark.django_db
def test_a_pending_submission_still_counts_as_nothing_verified(client, user_model, squad, captain):
    """Submitted is not verified -- a rider awaiting review still cannot race."""
    rider = _bare_member(user_model, squad, "Ana")
    RaceReadyRecord.objects.create(
        user=rider, verify_type="weight_light", status=RaceReadyRecord.Status.PENDING,
        record_date=timezone.localdate(),
    )

    assert _summary(captain)["rider_count"] == 1
    client.force_login(captain)
    assert "nothing verified" in client.get(reverse("team:squad_expiring_modal")).content.decode()


@pytest.mark.django_db
def test_nothing_verified_sorts_above_lapsed_and_expiring(client, user_model, squad, captain):
    """Worst first, all three states in one ordering."""
    _expiring_member(user_model, squad, "Soon", 4)
    _expiring_member(user_model, squad, "Gone", -6)
    _bare_member(user_model, squad, "Never")
    client.force_login(captain)
    body = client.get(reverse("team:squad_expiring_modal")).content.decode()

    assert body.index("Never Rider") < body.index("Gone Rider") < body.index("Soon Rider")


@pytest.mark.django_db
def test_a_verification_configured_never_to_expire_is_not_flagged(user_model, squad, captain):
    """A rider holding a non-expiring record has verified something; they are not "nothing"."""
    from constance.test import override_config

    rider = _bare_member(user_model, squad, "Ana")
    RaceReadyRecord.objects.create(
        user=rider, verify_type="height", status=RaceReadyRecord.Status.VERIFIED,
        record_date=timezone.localdate() - timedelta(days=900),
    )

    with override_config(HEIGHT_VERIFICATION_DAYS=0):
        assert _summary(captain)["rider_count"] == 0


@pytest.mark.django_db
def test_adding_the_state_did_not_add_queries(user_model, squad, captain):
    """The state is set arithmetic on data already fetched; it must stay that way.

    Shape, not an absolute count: the first call in a process also pays one-time Constance
    setup, so a fixed ceiling would pin that rather than the thing under test.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for i in range(2):
        _bare_member(user_model, squad, f"Few{i}")
    _warm_config(captain)
    with CaptureQueriesContext(connection) as few:
        assert _summary(captain)["rider_count"] == 2

    for i in range(18):
        _bare_member(user_model, squad, f"Many{i}")
    with CaptureQueriesContext(connection) as many:
        assert _summary(captain)["rider_count"] == 20

    assert len(many.captured_queries) == len(few.captured_queries), (
        f"{len(few.captured_queries)} queries for 2 bare riders, {len(many.captured_queries)} for 20"
    )

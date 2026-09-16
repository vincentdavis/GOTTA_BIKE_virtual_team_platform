"""Who may review a verification record, as one rule.

A record submitted as "Other" may be approved or rejected only by an Admin (``app_admin``,
which comes from the Discord roles in PERM_APP_ADMIN_ROLES) or a superuser. Other reviewers
may still open it and see the evidence.

That joined two existing rules -- same-gender, which the rider asks for, and Power, which
``POWER_REQUIRES_PER_VER`` hands to the performance team -- and all three now live in
``services.review_requirements``. Before, same-gender was copied into the list, the review
page, the media gate and the sidebar badge, and the Power rule lived in the review page alone,
so the list offered records the page then refused to let anyone decide. Much of this file is
about those places still agreeing.

The enforcement tests post to the decision endpoint directly. A hidden button stops nobody.
"""

import itertools

import pytest
from constance.test import override_config
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import reverse

from apps.team.context_processors import pending_verification_count
from apps.team.models import RaceReadyRecord
from apps.team.services import (
    ADMIN_REVIEW,
    PERFORMANCE_TEAM_REVIEW,
    SAME_GENDER_REVIEW,
    can_decide_verification_record,
    can_open_verification_record,
    records_decidable_by,
    review_requirements,
)
from conftest import _make_user


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the cache: the badge caches per user, and pytest-django never clears it."""
    cache.clear()
    yield
    cache.clear()


# --- people ------------------------------------------------------------------------------------


def _person(user_model, name, gender="male", **permissions):
    return _make_user(
        user_model,
        username=name,
        gender=gender,
        permissions={"team_member": True, "approve_verification": True, **permissions},
    )


@pytest.fixture
def reviewer(db, user_model):
    """Build a verification reviewer with no other role.

    Returns:
        The user.

    """
    return _person(user_model, "reviewer")


@pytest.fixture
def admin(db, user_model):
    """Build a reviewer who also holds app_admin -- the Discord Admin role.

    Returns:
        The user.

    """
    return _person(user_model, "admin", app_admin=True)


@pytest.fixture
def team_member_pvt(db, user_model):
    """Build a reviewer on the performance verification team, who is not an admin.

    Returns:
        The user.

    """
    return _person(user_model, "pvt", performance_verification_team=True)


@pytest.fixture
def root(db, user_model):
    """Build a superuser.

    Returns:
        The user.

    """
    user = _person(user_model, "root")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    return user


@pytest.fixture
def rider(db, user_model):
    """Build the rider whose records are being reviewed.

    Returns:
        The user.

    """
    return _make_user(user_model, username="rider", gender="female", permissions={"team_member": True})


def _record(rider, *, media_type="link", verify_type="weight_full", same_gender=False, status="pending"):
    return RaceReadyRecord.objects.create(
        user=rider,
        verify_type=verify_type,
        media_type=media_type,
        url="https://example.test/evidence",
        weight=72 if verify_type.startswith("weight") else None,
        record_date="2026-09-01",
        same_gender=same_gender,
        status=status,
    )


# --- the rule --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_other_record_carries_the_admin_requirement(rider):
    assert review_requirements(_record(rider, media_type="other")) == [ADMIN_REVIEW]


@pytest.mark.django_db
@pytest.mark.parametrize("media_type", ["video", "link"])
def test_the_named_kinds_carry_none(rider, media_type):
    """Only Other: the requirement must not creep onto ordinary evidence."""
    assert review_requirements(_record(rider, media_type=media_type)) == []


@pytest.mark.django_db
def test_a_record_can_carry_several(rider):
    record = _record(rider, media_type="other", same_gender=True)

    assert review_requirements(record) == [SAME_GENDER_REVIEW, ADMIN_REVIEW]


@pytest.mark.django_db
@override_config(POWER_REQUIRES_PER_VER=True)
def test_power_carries_the_team_requirement_only_when_the_setting_is_on(rider):
    assert review_requirements(_record(rider, verify_type="power", media_type="video")) == [PERFORMANCE_TEAM_REVIEW]


@pytest.mark.django_db
@override_config(POWER_REQUIRES_PER_VER=False)
def test_power_carries_nothing_when_the_setting_is_off(rider):
    assert review_requirements(_record(rider, verify_type="power", media_type="video")) == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("who", "may_decide"),
    [("reviewer", False), ("team_member_pvt", False), ("admin", True), ("root", True)],
)
def test_only_admins_and_superusers_decide_an_other_record(request, rider, who, may_decide):
    """The performance team is deliberately not enough -- that is the requirement."""
    record = _record(rider, media_type="other")

    assert can_decide_verification_record(request.getfixturevalue(who), record) is may_decide


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["reviewer", "team_member_pvt", "admin", "root"])
def test_every_reviewer_may_still_open_an_other_record(request, rider, who):
    """Looking is not deciding: a reviewer can still triage or flag it."""
    assert can_open_verification_record(request.getfixturevalue(who), _record(rider, media_type="other"))


@pytest.mark.django_db
def test_being_an_admin_does_not_override_the_riders_same_gender_request(user_model, rider):
    """The rider asked who may see their evidence; a role does not quietly overrule that."""
    male_admin = _person(user_model, "male_admin", gender="male", app_admin=True)
    record = _record(rider, media_type="other", same_gender=True)

    assert not can_open_verification_record(male_admin, record)
    assert not can_decide_verification_record(male_admin, record)


@pytest.mark.django_db
def test_an_admin_of_the_riders_gender_clears_both(user_model, rider):
    female_admin = _person(user_model, "female_admin", gender="female", app_admin=True)

    assert can_decide_verification_record(female_admin, _record(rider, media_type="other", same_gender=True))


@pytest.mark.django_db
@override_config(POWER_REQUIRES_PER_VER=True)
@pytest.mark.parametrize(
    ("who", "may_decide"),
    [("reviewer", False), ("team_member_pvt", True), ("admin", True), ("root", True)],
)
def test_the_power_rule_is_unchanged(request, rider, who, may_decide):
    """Admins could already decide Power records through the status-change path; still can."""
    record = _record(rider, verify_type="power", media_type="video")

    assert can_decide_verification_record(request.getfixturevalue(who), record) is may_decide


def test_admin_alone_is_not_a_reviewer(db, user_model, rider):
    """The queue needs approve_verification; the Admin role is added on top, not instead."""
    admin_only = _make_user(user_model, username="admin_only", permissions={"app_admin": True})

    assert not can_decide_verification_record(admin_only, _record(rider, media_type="other"))


# --- the database twin agrees ------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("power_rule", [True, False])
def test_the_queryset_and_the_function_give_the_same_answer_everywhere(
    user_model, rider, reviewer, admin, team_member_pvt, root, power_rule
):
    """records_decidable_by cannot call the function, so it is held to it here instead.

    Every combination of media, type, same-gender and status, for every kind of reviewer --
    including one of each gender, since same-gender turns on it.
    """
    female_reviewer = _person(user_model, "female_reviewer", gender="female")
    records = [
        _record(rider, media_type=media, verify_type=kind, same_gender=same, status=status)
        for media, kind, same, status in itertools.product(
            ["video", "link", "other"],
            ["weight_full", "power"],
            [False, True],
            ["pending", "verified"],
        )
    ]
    with override_config(POWER_REQUIRES_PER_VER=power_rule):
        for user in (reviewer, admin, team_member_pvt, root, female_reviewer):
            in_db = set(records_decidable_by(user, RaceReadyRecord.objects.all()).values_list("pk", flat=True))
            in_python = {r.pk for r in records if can_decide_verification_record(user, r)}
            assert in_db == in_python, user.username


# --- the decision is enforced on the server ------------------------------------------------


def _decide(client, user, record, action="verify"):
    client.force_login(user)
    client.post(reverse("team:verification_record_detail", args=[record.pk]), {"action": action})
    record.refresh_from_db()
    return record


@pytest.mark.django_db
def test_a_reviewer_cannot_approve_an_other_record_by_posting(client, reviewer, rider):
    """The button is gone for them, but the endpoint is what matters."""
    record = _decide(client, reviewer, _record(rider, media_type="other"))

    assert record.status == "pending"


@pytest.mark.django_db
def test_a_reviewer_cannot_reject_one_either(client, reviewer, rider):
    record = _decide(client, reviewer, _record(rider, media_type="other"), action="reject")

    assert record.status == "pending"


@pytest.mark.django_db
def test_the_performance_team_cannot_approve_one_through_the_status_path(client, team_member_pvt, rider):
    """The team may change ANY record's status, which would have walked straight past the rule."""
    record = _decide(client, team_member_pvt, _record(rider, media_type="other"))

    assert record.status == "pending"


@pytest.mark.django_db
def test_the_performance_team_cannot_overturn_a_decided_one(client, team_member_pvt, rider):
    """Resetting or reversing an admin's decision is a decision too."""
    record = _decide(client, team_member_pvt, _record(rider, media_type="other", status="verified"), action="reject")

    assert record.status == "verified"


@pytest.mark.django_db
def test_an_admin_can_approve_one(client, admin, rider):
    record = _decide(client, admin, _record(rider, media_type="other"))

    assert record.status == "verified"
    assert record.reviewed_by == admin


@pytest.mark.django_db
def test_a_superuser_can_approve_one(client, root, rider):
    assert _decide(client, root, _record(rider, media_type="other")).status == "verified"


@pytest.mark.django_db
def test_an_ordinary_record_is_still_the_reviewers_to_decide(client, reviewer, rider):
    """The rule must not have taken ordinary reviewing away with it."""
    assert _decide(client, reviewer, _record(rider, media_type="video")).status == "verified"


# --- what the pages say -----------------------------------------------------------------------


def _text(markup):
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


@pytest.mark.django_db
def test_the_review_page_tells_a_reviewer_why_it_is_not_theirs(client, reviewer, rider):
    record = _record(rider, media_type="other")
    client.force_login(reviewer)

    response = client.get(reverse("team:verification_record_detail", args=[record.pk]))
    text = _text(response.content.decode())

    assert response.status_code == 200
    assert "the decision belongs to the people named above" in text
    # Named once, in the banner -- not repeated in the line beneath it.
    assert text.count("an Admin or Super Admin has to approve or reject it") == 1
    assert "Only Team Captains" not in text


@pytest.mark.django_db
def test_the_review_page_gives_admins_the_buttons_and_the_reason(client, admin, rider):
    record = _record(rider, media_type="other")
    client.force_login(admin)

    body = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()

    assert "openReviewModal('verify')" in body
    assert "Who can decide this record" in _text(body)


@pytest.mark.django_db
def test_the_list_labels_an_other_record_in_words(client, reviewer, rider):
    """A chip with text, where the same-gender badge used to be an icon and a tooltip."""
    _record(rider, media_type="other")
    client.force_login(reviewer)

    text = _text(client.get(reverse("team:verification_records")).content.decode())

    assert "Admin review" in text


@pytest.mark.django_db
def test_the_list_tells_a_reviewer_it_is_admins_only_but_still_lets_them_look(client, reviewer, rider):
    record = _record(rider, media_type="other")
    client.force_login(reviewer)

    body = client.get(reverse("team:verification_records")).content.decode()

    assert "Admins only" in _text(body)
    assert reverse("team:verification_record_detail", args=[record.pk]) in body


@pytest.mark.django_db
def test_the_list_does_not_tell_an_admin_it_is_admins_only(client, admin, rider):
    _record(rider, media_type="other")
    client.force_login(admin)

    assert "Admins only" not in _text(client.get(reverse("team:verification_records")).content.decode())


@pytest.mark.django_db
def test_a_same_gender_row_names_its_own_reason(client, reviewer, rider):
    """Not "Restricted" with a tooltip -- and not blamed on the wrong rule either."""
    record = _record(rider, media_type="video", same_gender=True)
    client.force_login(reviewer)

    body = client.get(reverse("team:verification_records")).content.decode()

    assert "Same gender only" in _text(body)
    assert reverse("team:verification_record_detail", args=[record.pk]) not in body
    assert "Same-gender reviewer required" not in body


@pytest.mark.django_db
def test_admins_see_the_queue_only_they_can_clear(client, admin, rider):
    _record(rider, media_type="other")
    _record(rider, media_type="other")
    _record(rider, media_type="other", status="verified")  # decided -- not in the queue
    client.force_login(admin)

    assert "2 waiting on an admin" in _text(client.get(reverse("team:verification_records")).content.decode())


@pytest.mark.django_db
def test_other_reviewers_are_not_shown_that_queue(client, reviewer, rider):
    _record(rider, media_type="other")
    client.force_login(reviewer)

    assert "waiting on an admin" not in _text(client.get(reverse("team:verification_records")).content.decode())


def _link(record):
    """Return the record's review link, which identifies its row whatever the rider's name.

    The list shows a full name or a Discord name, and test riders carry neither -- so asserting
    on a username passes whether the row is there or not.

    Returns:
        The URL of the record's review page.

    """
    return reverse("team:verification_record_detail", args=[record.pk])


@pytest.mark.django_db
def test_the_mine_filter_leaves_out_what_a_reviewer_cannot_decide(client, reviewer, rider):
    other = _record(rider, media_type="other")
    video = _record(rider, media_type="video")
    client.force_login(reviewer)

    unfiltered = client.get(reverse("team:verification_records")).content.decode()
    mine = client.get(reverse("team:verification_records") + "?reviewer=mine").content.decode()

    # The reviewer CAN open the Other record, so it is on the unfiltered list -- which is what
    # makes its absence below mean something.
    assert _link(other) in unfiltered
    assert _link(video) in mine
    assert _link(other) not in mine


@pytest.mark.django_db
def test_the_admin_filter_shows_only_other_records(client, admin, rider):
    other = _record(rider, media_type="other")
    video = _record(rider, media_type="video")
    client.force_login(admin)

    body = client.get(reverse("team:verification_records") + "?reviewer=admin").content.decode()

    assert _link(other) in body
    assert _link(video) not in body


# --- the sidebar badge --------------------------------------------------------------------------


def _badge(user):
    request = RequestFactory().get("/")
    request.user = user
    return pending_verification_count(request)["pending_verification_count"]


@pytest.mark.django_db
def test_the_badge_does_not_ask_a_reviewer_to_clear_what_they_cannot(reviewer, rider):
    """A count they can never bring down is noise, not a notification."""
    _record(rider, media_type="other")
    _record(rider, media_type="video")

    assert _badge(reviewer) == 1


@pytest.mark.django_db
def test_the_badge_counts_other_records_for_admins(admin, rider):
    _record(rider, media_type="other")
    _record(rider, media_type="video")

    assert _badge(admin) == 2

"""What a rider can learn about their own verification evidence.

RecordView has always logged who opened a record and whether the evidence was shown, but the
trail was visible only to reviewers -- leaving the data subject as the one person unable to
see who had looked at photographs of their own body. These tests cover that trail, the
retention dates shown beside it, and the count of who can currently open the file.

The retention half matters as much as the access half: a page that states a removal date no
sweep enforces is a promise about someone's data that cannot be kept, which is worse than
saying nothing.
"""

from datetime import date, timedelta

import pytest
from constance.test import override_config
from django.urls import reverse
from django.utils import timezone

from apps.team.models import RaceReadyRecord, RecordView
from apps.team.services import (
    media_access_summary,
    media_retention_rules,
    record_view_trail,
    viewer_pseudonym,
)
from conftest import _make_user


@pytest.fixture
def rider(db, user_model):
    return _make_user(user_model, username="cardrider", permissions={"team_member": True}, gender="female")


@pytest.fixture
def record_factory(db, rider):
    def _make(status=RaceReadyRecord.Status.PENDING, *, with_media=True, same_gender=False, reviewed_days_ago=None):
        record = RaceReadyRecord.objects.create(
            user=rider,
            verify_type="weight_light",
            media_type="photo" if with_media else "link",
            url="" if with_media else "https://example.test/e",
            status=status,
            record_date=date.today(),
            same_gender=same_gender,
        )
        if with_media:
            record.media_file.name = "race_ready/evidence.jpg"
            record.save(update_fields=["media_file"])
        if reviewed_days_ago is not None:
            record.reviewed_date = timezone.now() - timedelta(days=reviewed_days_ago)
            record.save(update_fields=["reviewed_date"])
        return record

    return _make


@pytest.mark.django_db
def test_a_pending_record_admits_it_has_no_removal_date(record_factory):
    """Nothing sweeps pending evidence, so a date here would be invented."""
    rules = media_retention_rules(record_factory(RaceReadyRecord.Status.PENDING))
    assert len(rules) == 1
    assert rules[0].due is None
    assert rules[0].automatic is False


@pytest.mark.django_db
def test_a_rejected_record_marks_its_rule_as_not_automatic(record_factory):
    """The 30-day rejected purge runs from an admin button, not a schedule."""
    rules = media_retention_rules(
        record_factory(RaceReadyRecord.Status.REJECTED, reviewed_days_ago=5)
    )
    assert len(rules) == 1
    assert rules[0].due == (timezone.now() - timedelta(days=5) + timedelta(days=30)).date()
    assert rules[0].automatic is False, "nothing schedules this; saying otherwise overpromises"


@pytest.mark.django_db
@override_config(VERIFICATION_MEDIA_MAX_DAYS=365, WEIGHT_LIGHT_DAYS=30)
def test_a_verified_record_lists_both_sweeps_soonest_first(record_factory):
    """Validity and retention are different clocks, and the earlier one is what bites."""
    rules = media_retention_rules(record_factory(RaceReadyRecord.Status.VERIFIED))
    assert len(rules) == 2
    assert all(rule.automatic for rule in rules)
    assert rules[0].due <= rules[1].due


@pytest.mark.django_db
@override_config(VERIFICATION_MEDIA_MAX_DAYS=0)
def test_a_disabled_retention_cap_is_not_reported_as_a_rule(record_factory):
    """Zero disables the sweep, so listing it would name a date that never arrives."""
    labels = [r.label for r in media_retention_rules(record_factory(RaceReadyRecord.Status.VERIFIED))]
    assert "Retention limit" not in labels


@pytest.mark.django_db
def test_the_access_count_uses_the_real_gate(record_factory, user_model):
    """Counted through can_view_verification_media, so it cannot drift from what is allowed."""
    reviewer_perms = {"team_member": True, "approve_verification": True}
    _make_user(user_model, username="rev_f", permissions=reviewer_perms, gender="female")
    _make_user(user_model, username="rev_m", permissions=reviewer_perms, gender="male")
    _make_user(user_model, username="plain", permissions={"team_member": True})

    from django.core.cache import cache

    cache.clear()
    open_record = media_access_summary(record_factory(RaceReadyRecord.Status.PENDING))
    assert open_record["count"] == 2, "both reviewers, not the plain member"

    cache.clear()
    restricted = media_access_summary(record_factory(RaceReadyRecord.Status.PENDING, same_gender=True))
    assert restricted["count"] == 1, "same-gender request must narrow the count"
    assert restricted["same_gender_only"] is True


@pytest.mark.django_db
def test_the_trail_lists_only_views_that_showed_the_evidence(record_factory, user_model):
    """Opening the page without passing the media gate is not 'they saw your photo'."""
    record = record_factory()
    looked = _make_user(user_model, username="looked", permissions={"team_member": True})
    passed_by = _make_user(user_model, username="passed", permissions={"team_member": True})
    RecordView.objects.create(record=record, user=looked, view_count=2, media_view_count=2)
    RecordView.objects.create(record=record, user=passed_by, view_count=1, media_view_count=0)

    trail = record_view_trail(record)
    assert len(trail) == 1
    assert trail[0]["media_view_count"] == 2


@pytest.mark.django_db
def test_a_viewer_tag_is_stable_for_the_owner_and_differs_between_owners():
    """One reviewer reads the same across a rider's records, but not across two riders'."""
    assert viewer_pseudonym(1, 42) == viewer_pseudonym(1, 42)
    assert viewer_pseudonym(1, 42) != viewer_pseudonym(2, 42)
    assert viewer_pseudonym(1, 42) != viewer_pseudonym(1, 43)


@pytest.mark.django_db
def test_a_viewer_tag_never_contains_the_viewers_id():
    """The point is that it is not the id."""
    tag = viewer_pseudonym(1, 987654)
    assert "987654" not in tag
    assert len(tag) == 6


@pytest.mark.django_db
def test_the_rider_sees_their_own_access_trail_on_the_page(client, rider, record_factory, user_model):
    """The gap the audit named: the data subject was the one person excluded from this."""
    record = record_factory()
    reviewer = _make_user(user_model, username="areviewer", permissions={"team_member": True})
    RecordView.objects.create(record=record, user=reviewer, view_count=1, media_view_count=1)

    client.force_login(rider)
    html = client.get(reverse("accounts:verification")).content.decode()
    assert viewer_pseudonym(rider.pk, reviewer.pk) in html
    assert "Opened by" in html


@pytest.mark.django_db
def test_another_rider_cannot_see_that_trail(client, rider, record_factory, user_model):
    """The page is per-user, and the tag is keyed to the owner, so it is meaningless elsewhere."""
    record = record_factory()
    reviewer = _make_user(user_model, username="reviewer2", permissions={"team_member": True})
    RecordView.objects.create(record=record, user=reviewer, view_count=1, media_view_count=1)
    other = _make_user(user_model, username="stranger", permissions={"team_member": True})

    client.force_login(other)
    html = client.get(reverse("accounts:verification")).content.decode()
    assert viewer_pseudonym(rider.pk, reviewer.pk) not in html

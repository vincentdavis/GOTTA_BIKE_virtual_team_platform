"""Following a submission DM to a record that has since gone.

The power-submission DM links straight at /team/verification/<pk>/, and the rider can delete
or replace the record before a reviewer opens it. That produced a bare 404 in production
(record 1404), which reads as the app having lost the submission rather than as the rider
having withdrawn it.

The second half is about being able to answer "where did it go" next time: the rider's own
deletion logged a COUNT, so nothing in the logs could be matched against the id in the DM.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.team.models import RaceReadyRecord
from apps.team.services import delete_verification_records


@pytest.fixture
def approver(user_model):
    """Build a reviewer who can open verification records.

    Returns:
        The reviewer.

    """
    return user_model.objects.create_user(
        username="approver",
        email="approver@example.test",
        permission_overrides={"team_member": True, "approve_verification": True},
    )


# --- the reviewer's experience ---------------------------------------------------------


@pytest.mark.django_db
def test_a_deleted_record_explains_itself_instead_of_404ing(client, approver, user, verification_factory):
    """The reviewer followed a real notification; a bare 404 blames the wrong thing."""
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    pk = record.pk
    record.delete()
    client.force_login(approver)

    response = client.get(reverse("team:verification_record_detail", args=[pk]), follow=True)

    assert response.status_code == 200
    text = " ".join(str(m) for m in response.context["messages"])
    assert f"#{pk} no longer exists" in text
    assert response.redirect_chain[-1][0] == reverse("team:verification_records")


@pytest.mark.django_db
def test_a_record_that_never_existed_says_the_same_thing(client, approver):
    """A mistyped or ancient id is the same situation from the reviewer's side."""
    client.force_login(approver)

    response = client.get(reverse("team:verification_record_detail", args=[999999]), follow=True)

    assert response.status_code == 200
    assert "no longer exists" in " ".join(str(m) for m in response.context["messages"])


@pytest.mark.django_db
def test_an_existing_record_still_opens(client, approver, user, verification_factory):
    """The graceful path must not swallow the normal one."""
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    client.force_login(approver)

    assert client.get(reverse("team:verification_record_detail", args=[record.pk])).status_code == 200


@pytest.mark.django_db
def test_a_reviewer_without_permission_is_still_refused_for_a_missing_record(client, team_member):
    """The permission check must stay ahead of the existence check.

    Otherwise the message becomes an oracle: anyone could probe which record ids have ever
    existed by watching which ones say "no longer exists".
    """
    client.force_login(team_member)

    response = client.get(reverse("team:verification_record_detail", args=[999999]), follow=True)

    text = " ".join(str(m) for m in response.context["messages"])
    assert "don't have permission" in text
    assert "no longer exists" not in text


# --- being able to answer it next time -------------------------------------------------


@pytest.mark.django_db
def test_the_rider_self_delete_logs_which_records_went(user, verification_factory):
    """A count cannot be matched against the id in a notification; the ids can.

    The rows are removed with a queryset delete, so the in-memory instances keep their pks --
    switching to per-object deletion would silently start logging None.
    """
    first = verification_factory(user, "height", status=RaceReadyRecord.Status.VERIFIED, height=175)
    second = verification_factory(user, "weight_light", status=RaceReadyRecord.Status.VERIFIED, weight=70)

    with patch("apps.team.services.logfire.info") as log:
        delete_verification_records(user, [first.pk, second.pk])

    logged = [c for c in log.call_args_list if c.args and "deleted their own" in c.args[0]]
    assert logged, "the deletion was not logged at all"
    ids = logged[-1].kwargs["record_ids"]
    assert ids == sorted([first.pk, second.pk])
    assert None not in ids

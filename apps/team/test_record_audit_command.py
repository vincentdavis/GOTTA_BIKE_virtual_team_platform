"""The verification-record audit command.

It exists because nothing in the app records who deletes a RaceReadyRecord: the row goes,
its RecordView trail cascades with it, and there is no history model. django_admin_log is
the only actor-bearing trace the database keeps, and only for /admin/ deletions -- so the
command's real job is to say clearly which of those two worlds the answer is in.
"""

from io import StringIO

import pytest
from django.contrib.admin.models import ADDITION, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command

from apps.team.models import RaceReadyRecord


def _run(*args) -> str:
    """Run the command and capture stdout.

    Args:
        *args: Command arguments.

    Returns:
        The captured output.

    """
    out = StringIO()
    call_command("verification_record_audit", *args, stdout=out, no_color=True)
    return out.getvalue()


@pytest.mark.django_db
def test_an_existing_record_is_reported_as_present(user, verification_factory):
    """If the row is still there the 404 had a different cause, and the audit should say so."""
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)

    out = _run(str(record.pk))

    assert "EXISTS" in out
    assert "Nothing was deleted" in out


@pytest.mark.django_db
def test_an_admin_deletion_names_the_actor(db, user, user_model, verification_factory):
    """The one case the database can actually answer."""
    record = verification_factory(user, "power", status=RaceReadyRecord.Status.PENDING)
    pk = record.pk
    admin = user_model.objects.create_user(username="deleter", email="deleter@example.test")
    LogEntry.objects.create(
        user=admin,
        content_type=ContentType.objects.get_for_model(RaceReadyRecord),
        object_id=str(pk),
        object_repr=f"Power record for {user.username}",
        action_flag=DELETION,
    )
    record.delete()

    out = _run(str(pk))

    assert "NOT FOUND" in out
    assert "DELETED" in out
    assert "deleter" in out
    assert "That names who deleted it" in out


@pytest.mark.django_db
def test_no_admin_entry_says_where_else_to_look(user, verification_factory):
    """The common case -- and the answer is then in Logfire, not here. Say which is which."""
    record = verification_factory(user, "power", status=RaceReadyRecord.Status.PENDING)
    pk = record.pk
    record.delete()

    out = _run(str(pk))

    assert "NOT deleted through /admin/" in out
    assert "coalition-platform" in out
    assert f"record_id={pk}" in out


@pytest.mark.django_db
def test_an_unrelated_admin_entry_is_not_mistaken_for_a_deletion(db, user, user_model, verification_factory):
    """An ADDITION entry on the same id must not read as somebody deleting it."""
    record = verification_factory(user, "power", status=RaceReadyRecord.Status.PENDING)
    pk = record.pk
    editor = user_model.objects.create_user(username="editor", email="editor@example.test")
    LogEntry.objects.create(
        user=editor,
        content_type=ContentType.objects.get_for_model(RaceReadyRecord),
        object_id=str(pk),
        object_repr="Power record",
        action_flag=ADDITION,
    )
    record.delete()

    out = _run(str(pk))

    assert "DELETED" not in out.split("All /admin/ deletions")[0]


@pytest.mark.django_db
def test_the_rider_cross_check_shows_a_resubmission(user, verification_factory):
    """Delete-and-resubmit is the likeliest explanation, and the rider's own list shows it."""
    gone = verification_factory(user, "power", status=RaceReadyRecord.Status.PENDING)
    pk = gone.pk
    gone.delete()
    replacement = verification_factory(user, "power", status=RaceReadyRecord.Status.PENDING)

    out = _run(str(pk), "--user", user.username)

    assert f"#{replacement.pk}" in out
    assert "deleted and resubmitted" in out


@pytest.mark.django_db
def test_an_unknown_rider_is_reported_rather_than_crashing(user, verification_factory):
    """The name comes off a Discord DM by hand; a typo should not be a traceback."""
    record = verification_factory(user, "power", status=RaceReadyRecord.Status.PENDING)
    pk = record.pk
    record.delete()

    assert "No user matched" in _run(str(pk), "--user", "nobody-by-that-name")


@pytest.mark.django_db
def test_a_never_existing_id_is_handled(db):
    """The id is copied out of a DM; it may be wrong or ancient."""
    out = _run("999999")

    assert "NOT FOUND" in out


# --- was the id ever issued? -----------------------------------------------------------


@pytest.mark.django_db
def test_an_id_above_the_highest_says_no_record_ever_existed(user, verification_factory):
    """The other answer to "did someone delete it": nobody did, the link is wrong."""
    verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)

    out = _run("999999")

    assert "is ABOVE it" in out
    assert "nothing" in out and "deleted it" in out


@pytest.mark.django_db
def test_a_gap_between_neighbours_shows_the_id_was_used(user, verification_factory):
    """A missing id with live neighbours is a deleted row, not an unused number."""
    first = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    middle = verification_factory(user, "power", status=RaceReadyRecord.Status.PENDING)
    verification_factory(user, "weight_light", status=RaceReadyRecord.Status.PENDING, weight=70)
    gone = middle.pk
    middle.delete()

    out = _run(str(gone))

    assert "MISSING" in out
    assert "the row is gone" in out
    assert str(first.pk) in out  # the neighbourhood is actually shown


@pytest.mark.django_db
def test_the_rolled_back_insert_caveat_is_stated(user, verification_factory):
    """A gap is strong evidence, not proof; saying otherwise would mislead an investigation."""
    first = verification_factory(user, "height", status=RaceReadyRecord.Status.PENDING, height=175)
    verification_factory(user, "power", status=RaceReadyRecord.Status.PENDING)
    gone = first.pk
    first.delete()

    assert "rolled-back INSERT" in _run(str(gone))

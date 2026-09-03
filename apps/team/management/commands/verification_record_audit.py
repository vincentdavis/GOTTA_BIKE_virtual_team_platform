"""Report everything the database knows about a verification record id.

Written for the case a reviewer follows a submission DM to a record that no longer exists.
Nothing in the app records who deletes a ``RaceReadyRecord``: the row goes, its ``RecordView``
audit trail goes with it (CASCADE), and there is no ``HistoricalRecords`` on the model. What
survives is ``django_admin_log`` -- but only for deletions made through ``/admin/`` -- and
whatever reached Logfire, which is a different system.

So this answers the narrower question the database can actually answer: was it deleted
through the Django admin, and if so by whom; and does the rider have a newer record that
would explain a delete-and-resubmit.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandParser
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.team.models import RaceReadyRecord


class Command(BaseCommand):
    """Audit a verification record id against every trace the database keeps."""

    help = "Report what the database knows about a RaceReadyRecord id, including admin deletions."

    def add_arguments(self, parser: CommandParser) -> None:
        """Declare the command's arguments.

        Args:
            parser: The argument parser.

        """
        parser.add_argument("record_id", type=int, help="The RaceReadyRecord primary key from the DM link.")
        parser.add_argument(
            "--user",
            default="",
            help="Rider to cross-check (id, username or discord_username), when the DM named them.",
        )
        parser.add_argument("--days", type=int, default=30, help="How far back to list admin deletions.")

    def handle(self, *args: object, **options: object) -> None:
        """Run the audit.

        Args:
            *args: Unused.
            **options: Parsed command arguments.

        """
        record_id = int(options["record_id"])
        days = int(options["days"])
        content_type = ContentType.objects.get_for_model(RaceReadyRecord)

        record = RaceReadyRecord.objects.select_related("user").filter(pk=record_id).first()
        self.stdout.write(self.style.MIGRATE_HEADING(f"Record {record_id}"))
        if record is not None:
            self.stdout.write(
                f"  EXISTS — {record.verify_type}, status={record.status}, "
                f"user={record.user.username} (id {record.user_id}), created={record.date_created:%Y-%m-%d %H:%M}"
            )
            self.stdout.write("  Nothing was deleted; the 404 must have another cause.")
            return
        self.stdout.write(self.style.WARNING("  NOT FOUND — the row is gone."))

        # 1. Django admin is the only deletion path that writes an actor to the database.
        entries = (
            LogEntry.objects.filter(content_type=content_type, object_id=str(record_id))
            .select_related("user")
            .order_by("-action_time")
        )
        self.stdout.write(self.style.MIGRATE_HEADING(f"\ndjango_admin_log for this id ({entries.count()})"))
        if entries:
            for entry in entries:
                verb = "DELETED" if entry.action_flag == DELETION else entry.get_action_flag_display().upper()
                actor = entry.user.username if entry.user else "(unknown)"
                self.stdout.write(f"  {entry.action_time:%Y-%m-%d %H:%M}  {verb:<8} by {actor}  {entry.object_repr}")
            self.stdout.write(self.style.SUCCESS("\n  ^ That names who deleted it."))
        else:
            self.stdout.write("  (none)")
            self.stdout.write(
                "  So it was NOT deleted through /admin/. That leaves: the rider deleting their\n"
                "  own record, an in-app admin delete, or an account deletion cascade. Those are\n"
                "  only in Logfire (service coalition-platform) — search record_id="
                f"{record_id} for\n"
                "  'Verification record deleted', and the rider's id for\n"
                "  'Rider deleted their own verification records'."
            )

        # 2. Other admin deletions nearby, in case the id in the DM was not what was removed.
        since = timezone.now() - timedelta(days=days)
        nearby = (
            LogEntry.objects.filter(content_type=content_type, action_flag=DELETION, action_time__gte=since)
            .select_related("user")
            .order_by("-action_time")[:25]
        )
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nAll /admin/ deletions of verification records, last {days}d"))
        if nearby:
            for entry in nearby:
                actor = entry.user.username if entry.user else "(unknown)"
                self.stdout.write(
                    f"  {entry.action_time:%Y-%m-%d %H:%M}  #{entry.object_id:<8} by {actor}  {entry.object_repr}"
                )
        else:
            self.stdout.write("  (none — nobody has deleted one of these through /admin/ in this window)")

        # 3. The likeliest explanation is delete-and-resubmit, which the rider's own records show.
        if options["user"]:
            self._show_rider(str(options["user"]))
        else:
            self.stdout.write(
                "\nPass --user <id|username|discord_username> (the DM names the submitter) to see\n"
                "whether they have a newer record, which would mean they deleted and resubmitted."
            )

    def _show_rider(self, needle: str) -> None:
        """Print a rider's verification records, newest first.

        Args:
            needle: An id, username or discord_username identifying the rider.

        """
        query = Q(username=needle) | Q(discord_username=needle)
        if needle.isdigit():
            query |= Q(pk=int(needle))
        rider = User.objects.filter(query).first()
        if rider is None:
            self.stdout.write(self.style.ERROR(f"\nNo user matched {needle!r}."))
            return

        records = rider.race_ready_records.order_by("-date_created")
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"\n{rider.username} (id {rider.pk}) — {records.count()} record(s)")
        )
        for record in records:
            self.stdout.write(
                f"  #{record.pk:<8} {record.date_created:%Y-%m-%d %H:%M}  "
                f"{record.verify_type:<13} {record.status}"
            )
        self.stdout.write(
            "\n  A record created shortly after the missing one, of the same type, means the\n"
            "  rider deleted and resubmitted — so the answer to 'who deleted it' is the rider."
        )

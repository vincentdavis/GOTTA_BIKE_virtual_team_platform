"""Team kit management at /site/config/team_kit/.

The section page itself is rendered by ``config_section_page`` (like Compliance and the
other special sections); these are the POST actions it posts to. Gated by
``can_manage_team_kit``: app admins and superusers as for the rest of /site/config/, plus
membership admins, for this section only.
"""

from __future__ import annotations

import csv
import secrets
from typing import TYPE_CHECKING, ClassVar

import logfire
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from apps.team.kit_csv import (
    MAX_IMPORT_BYTES,
    SESSION_KEY,
    apply_import,
    build_import_plan,
    export_filename,
    export_table,
    selected_changes,
)
from apps.team.kits import can_manage_team_kit, kit_member_rows, member_filters
from apps.team.models import TeamKit

if TYPE_CHECKING:
    from django.http import HttpRequest

# Errors listed on the import preview before the rest are summarised as a count. A wrong file
# fails on every row, and a thousand identical lines help nobody.
_PREVIEW_ERROR_LIMIT = 50


def _require_config_access(request: HttpRequest) -> None:
    """Apply the team kit gate -- app admins, superusers and membership admins.

    Args:
        request: The HTTP request.

    Raises:
        PermissionDenied: If the user may not manage team kits.

    """
    if not can_manage_team_kit(request.user):
        logfire.warning("Unauthorized team kit config action", user_id=request.user.id, path=request.path)
        raise PermissionDenied("You don't have permission to manage team kits.")


def _back() -> HttpResponse:
    """Return to the team kit config section.

    Returns:
        A redirect.

    """
    return redirect("config_section_page", section_key="team_kit")


class TeamKitForm(forms.ModelForm):
    """Add or edit a kit. The slug is only accepted when adding -- see ``TeamKit``."""

    class Meta:
        """Form metadata."""

        model = TeamKit
        fields: ClassVar[list[str]] = ["name", "slug", "description", "sort_order"]

    def __init__(self, *args, **kwargs) -> None:
        """Make the slug optional on add and absent on edit.

        Args:
            *args: Passed to the parent.
            **kwargs: Passed to the parent.

        """
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Every rider's status for this kit is stored under the slug. Refusing it here,
            # not just hiding the input, is what stops a crafted POST from orphaning them.
            del self.fields["slug"]
        else:
            self.fields["slug"].required = False

    def clean_slug(self) -> str:
        """Default the slug from the name, and refuse one already taken.

        Returns:
            The slug to store.

        Raises:
            forms.ValidationError: If the name yields no usable slug, or it is in use.

        """
        slug = self.cleaned_data.get("slug") or slugify(self.cleaned_data.get("name") or "")
        if not slug:
            raise forms.ValidationError("Enter a name (or a slug) that contains letters or numbers.")
        # Case-insensitively: "Race-2027" beside "race-2027" would be two keys a person reads
        # as one, and the CSV import matches kit columns without regard to case.
        existing = TeamKit.objects.filter(slug__iexact=slug).first()
        if existing is not None:
            raise forms.ValidationError(f'A kit with the slug "{existing.slug}" already exists.')
        return slug


def _form_error_message(form: forms.Form) -> str:
    """Flatten a form's errors into one readable line for a message.

    Args:
        form: A bound, invalid form.

    Returns:
        The errors, joined.

    """
    return " ".join(str(error) for errors in form.errors.values() for error in errors)


@login_required
@require_POST
def team_kit_add(request: HttpRequest) -> HttpResponse:
    """Create a kit, optionally making it the current one.

    Args:
        request: The HTTP request.

    Returns:
        Redirect to the team kit section.

    """
    _require_config_access(request)
    form = TeamKitForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"Kit not added: {_form_error_message(form)}")
        return _back()

    kit = form.save()
    if request.POST.get("make_current") == "1":
        kit.make_current()
    logfire.info(
        "Team kit added",
        kit_id=kit.pk,
        slug=kit.slug,
        is_current=kit.is_current,
        user_id=request.user.id,
    )
    messages.success(
        request,
        f'Added "{kit.name}"' + (" and made it the current kit." if kit.is_current else "."),
    )
    return _back()


@login_required
@require_POST
def team_kit_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Update a kit's name, description or sort order. The slug cannot change.

    Args:
        request: The HTTP request.
        pk: The kit's primary key.

    Returns:
        Redirect to the team kit section.

    """
    _require_config_access(request)
    kit = get_object_or_404(TeamKit, pk=pk)
    form = TeamKitForm(request.POST, instance=kit)
    if not form.is_valid():
        messages.error(request, f'"{kit.name}" not saved: {_form_error_message(form)}')
        return _back()

    form.save()
    logfire.info("Team kit edited", kit_id=kit.pk, slug=kit.slug, user_id=request.user.id)
    messages.success(request, f'Saved "{kit.name}".')
    return _back()


@login_required
@require_POST
def team_kit_make_current(request: HttpRequest, pk: int) -> HttpResponse:
    """Make a kit the current one, clearing the previous.

    Args:
        request: The HTTP request.
        pk: The kit's primary key.

    Returns:
        Redirect to the team kit section.

    """
    _require_config_access(request)
    kit = get_object_or_404(TeamKit, pk=pk)
    previous = TeamKit.objects.filter(is_current=True).exclude(pk=kit.pk).first()
    kit.make_current()
    logfire.info(
        "Team kit made current",
        kit_id=kit.pk,
        slug=kit.slug,
        previous_slug=previous.slug if previous else None,
        user_id=request.user.id,
    )
    messages.success(
        request,
        f'"{kit.name}" is now the current kit' + (f' (was "{previous.name}").' if previous else "."),
    )
    return _back()


@login_required
@require_POST
def team_kit_toggle_active(request: HttpRequest, pk: int) -> HttpResponse:
    """Retire a kit, or bring a retired one back.

    Retiring hides a kit from riders without erasing anything they recorded against it. The
    current kit cannot be retired -- that would hide the kit the team is chasing from the
    riders it is meant to reach -- and the database refuses it too; this says why first.

    Args:
        request: The HTTP request.
        pk: The kit's primary key.

    Returns:
        Redirect to the team kit section.

    """
    _require_config_access(request)
    kit = get_object_or_404(TeamKit, pk=pk)
    if kit.is_current and kit.active:
        messages.error(request, f'"{kit.name}" is the current kit. Make another kit current before retiring it.')
        return _back()

    kit.active = not kit.active
    kit.save(update_fields=["active"])
    logfire.info("Team kit active toggled", kit_id=kit.pk, slug=kit.slug, active=kit.active, user_id=request.user.id)
    messages.success(request, f'"{kit.name}" ' + ("restored." if kit.active else "retired."))
    return _back()


@login_required
@require_GET
def team_kit_export(request: HttpRequest) -> HttpResponse:
    """Download the team member list as CSV, with every rider's status for every kit.

    Takes the page's own filters, so "Export CSV" downloads the list on screen -- filter to
    "needs the kit" and the file is the list to send to Zwift.

    Args:
        request: The HTTP request.

    Returns:
        A CSV attachment.

    """
    _require_config_access(request)
    kits = list(TeamKit.objects.all())
    current = next((kit for kit in kits if kit.is_current), None)
    filters = member_filters(request.GET, current)
    rows = kit_member_rows(kit=current, **filters._asdict())
    header, data = export_table(rows, kits)

    filename = export_filename(filters, today=timezone.localdate())
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # A byte-order mark, or Excel reads the file as the local code page and mangles every
    # accented or emoji Discord name. Sheets and the import both skip it.
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(data)

    logfire.info(
        "Team kit CSV exported",
        user_id=request.user.id,
        row_count=len(data),
        kit_count=len(kits),
        verified_only=filters.verified_only,
        race_verified_only=filters.race_verified_only,
        statuses=list(filters.statuses),
    )
    return response


@login_required
@require_POST
def team_kit_import(request: HttpRequest) -> HttpResponse:
    """Read an uploaded CSV and show what it would change. Nothing is written here.

    The plan is kept in the session under a fresh token that the confirm button posts back.
    Without the token, a second upload in another tab would silently replace the plan behind
    the first tab's preview, and confirming there would apply a file the person never saw.

    Args:
        request: The HTTP request, with ``csv_file``.

    Returns:
        The preview page, or a redirect back to the kit page with a message.

    """
    _require_config_access(request)
    upload = request.FILES.get("csv_file")
    if upload is None:
        messages.error(request, "Choose a CSV file to import.")
        return _back()
    if upload.size > MAX_IMPORT_BYTES:
        messages.error(request, f"That file is too large to import (the limit is {MAX_IMPORT_BYTES // 1024} KB).")
        return _back()

    plan = build_import_plan(upload.read(), list(TeamKit.objects.all()))
    if plan.fatal:
        logfire.info("Team kit CSV import refused", user_id=request.user.id, filename=upload.name, reason=plan.fatal)
        messages.error(request, f"Import not read: {plan.fatal}")
        return _back()

    token = ""
    if plan.changes:
        token = secrets.token_urlsafe(16)
        request.session[SESSION_KEY] = {
            "token": token,
            "filename": upload.name,
            "changes": [change.for_session() for change in plan.changes],
        }
    else:
        # A preview with nothing to apply must not leave an older one waiting to be confirmed.
        request.session.pop(SESSION_KEY, None)

    logfire.info(
        "Team kit CSV import previewed",
        user_id=request.user.id,
        filename=upload.name,
        change_count=len(plan.changes),
        member_count=plan.member_count,
        unchanged_count=plan.unchanged,
        kept_newer_count=plan.kept_newer,
        conflict_count=len(plan.conflicts),
        refused_row_count=plan.refused_row_count,
        kits=[kit.slug for kit in plan.kits],
    )
    return render(
        request,
        "team/team_kit_import_preview.html",
        {
            "plan": plan,
            "filename": upload.name,
            "token": token,
            "shown_errors": plan.errors[:_PREVIEW_ERROR_LIMIT],
            "more_errors": max(0, len(plan.errors) - _PREVIEW_ERROR_LIMIT),
        },
    )


@login_required
@require_POST
def team_kit_import_confirm(request: HttpRequest) -> HttpResponse:
    """Apply the rows the person ticked on the import they just previewed.

    Args:
        request: The HTTP request, with the preview's ``token`` and the ticked rows' ``apply``
            positions.

    Returns:
        Redirect to the team kit section.

    """
    _require_config_access(request)
    pending = request.session.get(SESSION_KEY)
    if not pending or pending.get("token") != request.POST.get("token"):
        # Left in place when the token does not match: it belongs to a newer preview, which
        # its own tab can still confirm.
        messages.error(
            request,
            "That preview is no longer waiting: it was already applied, or replaced by a newer upload. "
            "Upload the file again to see what it would change now.",
        )
        return _back()
    del request.session[SESSION_KEY]

    chosen = selected_changes(pending["changes"], request.POST.getlist("apply"))
    unticked = len(pending["changes"]) - len(chosen)
    if not chosen:
        logfire.info(
            "Team kit CSV import applied nothing",
            user_id=request.user.id,
            filename=pending.get("filename", ""),
            unticked_count=unticked,
        )
        messages.info(request, "No rows were ticked, so nothing was changed.")
        return _back()

    applied, skipped = apply_import(chosen)
    # Every change, so "who set my kit to submitted?" has an answer later.
    logfire.info(
        "Team kit CSV import applied",
        user_id=request.user.id,
        filename=pending.get("filename", ""),
        applied_count=len(applied),
        skipped_count=skipped,
        unticked_count=unticked,
        changes=applied,
    )
    members = len({change["user_id"] for change in applied})
    message = (
        f"Updated {len(applied)} kit status{'es' if len(applied) != 1 else ''}"
        f" for {members} member{'s' if members != 1 else ''}."
    )
    if unticked:
        message += (
            f" {unticked} unticked row{'s were' if unticked != 1 else ' was'}"
            f" left as {'they were' if unticked != 1 else 'it was'}."
        )
    if skipped:
        message += (
            f" {skipped} {'were' if skipped != 1 else 'was'} skipped because {'they' if skipped != 1 else 'it'}"
            " changed after the preview -- export again to see them as they are now."
        )
    (messages.warning if skipped else messages.success)(request, message)
    return _back()

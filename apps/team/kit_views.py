"""Team kit management at /site/config/team_kit/.

The section page itself is rendered by ``config_section_page`` (like Compliance and the
other special sections); these are the POST actions it posts to. Same gate as the rest of
/site/config/: app_admin or superuser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import logfire
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.team.models import TeamKit

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


def _require_config_access(request: HttpRequest) -> None:
    """Apply the /site/config/ gate.

    Args:
        request: The HTTP request.

    Raises:
        PermissionDenied: If the user is neither a superuser nor an app admin.

    """
    if not (request.user.is_superuser or request.user.is_app_admin):
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
        if TeamKit.objects.filter(slug=slug).exists():
            raise forms.ValidationError(f'A kit with the slug "{slug}" already exists.')
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
        f'"{kit.name}" is now the current kit'
        + (f' (was "{previous.name}").' if previous else "."),
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

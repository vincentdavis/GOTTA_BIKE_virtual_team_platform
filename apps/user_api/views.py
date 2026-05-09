"""Views for managing per-user API keys."""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING

import logfire
from constance import config
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import team_member_required
from apps.user_api.models import UserApiKey
from apps.user_api.services import issue_api_key, user_can_use_api


def _api_use_required(view_func):
    """Reject users who do not satisfy ``user_can_use_api``.

    Enforces the AND gate documented on ``PERM_ROLES_REQUIRED_USE_API``:
    user is active, has the ``team_member`` permission, and holds every
    Discord role configured in the setting (or none if the list is empty).

    Stack after ``@team_member_required()`` — the team-member redirect fires
    first for non-members; this decorator catches the missing-role case.

    Args:
        view_func: The view to wrap.

    Returns:
        The wrapped view that redirects ineligible users to the home page
        with a flash message.

    """

    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if not user_can_use_api(request.user):
            messages.error(
                request,
                "Your account does not currently meet the requirements to manage API keys. "
                "Contact a team admin if you believe this is wrong.",
            )
            return redirect("home")
        return view_func(request, *args, **kwargs)

    return _wrapped


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


@login_required
@team_member_required()
@_api_use_required
def api_keys_list(request: HttpRequest) -> HttpResponse:
    """List the user's API keys and offer a create form.

    Args:
        request: The HTTP request.

    Returns:
        Rendered API keys management page.

    """
    keys = list(UserApiKey.objects.filter(user=request.user))
    active_count = sum(1 for k in keys if k.is_active)
    max_keys = config.USER_API_MAX_KEYS_PER_USER
    new_key_id = request.GET.get("new")
    # Always pop the stashed key so it can never linger in the session past one
    # render. Show it only when the user is following the post-creation redirect.
    stashed_key = request.session.pop("new_api_key", None)
    new_raw_key = stashed_key if new_key_id else None

    return render(
        request,
        "user_api/api_keys.html",
        {
            "keys": keys,
            "active_count": active_count,
            "max_keys": max_keys,
            "can_create": active_count < max_keys,
            "new_raw_key": new_raw_key,
            "new_key_id": new_key_id,
        },
    )


@login_required
@team_member_required()
@_api_use_required
@require_POST
def api_keys_create(request: HttpRequest) -> HttpResponse:
    """Issue a new 30-day API key for the current user.

    The raw key is stashed once in the session so the next page render can show
    it; it is never persisted to the model.

    Args:
        request: The HTTP request with a ``name`` POST field.

    Returns:
        Redirect back to the management page with ``?new=<id>``.

    """
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "Please give the key a name.")
        return redirect("user_api:api_keys_list")

    active_count = UserApiKey.objects.filter(
        user=request.user,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).count()
    if active_count >= config.USER_API_MAX_KEYS_PER_USER:
        messages.error(
            request,
            f"You already have {active_count} active keys (max {config.USER_API_MAX_KEYS_PER_USER}). "
            "Revoke one before creating another.",
        )
        return redirect("user_api:api_keys_list")

    api_key, raw_key = issue_api_key(request.user, name)
    request.session["new_api_key"] = raw_key
    logfire.info(
        "user api key issued",
        user_id=request.user.pk,
        key_id=api_key.pk,
        prefix=api_key.prefix,
    )
    messages.success(request, f'API key "{api_key.name}" created.')
    return redirect(f"{reverse('user_api:api_keys_list')}?new={api_key.pk}")


@login_required
@team_member_required()
@_api_use_required
@require_POST
def api_keys_revoke(request: HttpRequest, key_id: int) -> HttpResponse:
    """Revoke a key the current user owns.

    Args:
        request: The HTTP request.
        key_id: Primary key of the ``UserApiKey`` row to revoke.

    Returns:
        Redirect back to the management page.

    """
    api_key = get_object_or_404(UserApiKey, pk=key_id, user=request.user)
    if api_key.revoked_at is None:
        api_key.revoked_at = timezone.now()
        api_key.save(update_fields=["revoked_at"])
        logfire.info(
            "user api key revoked",
            user_id=request.user.pk,
            key_id=api_key.pk,
            prefix=api_key.prefix,
        )
        messages.success(request, f'API key "{api_key.name}" revoked.')
    return redirect("user_api:api_keys_list")

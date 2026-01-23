"""Permission decorators for view access control."""

from collections.abc import Callable, Iterable
from typing import TypeVar

import logfire
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied

from apps.accounts.models import Permissions

F = TypeVar("F", bound=Callable)


def discord_permission_required(
    permission: str | Iterable[str],
    login_url: str | None = None,
    raise_exception: bool = False,
) -> Callable[[F], F]:
    """Check Discord-based permissions for views.

    Uses the User.has_permission() method which checks:
    1. Superuser status
    2. Manual permission overrides
    3. Discord role mappings from Constance
    4. Legacy app roles (backward compatibility)

    Note: For authenticated users without permission, this raises PermissionDenied
    to avoid redirect loops (user is sent to login, but already logged in, so sent
    back to the protected page, etc.).

    Args:
        permission: Permission name or iterable of permission names.
            If multiple are provided, user needs ANY of them (OR logic).
        login_url: URL to redirect to if not logged in.
        raise_exception: If True, raise PermissionDenied instead of redirecting.
            Note: This is now the default behavior for authenticated users.

    Returns:
        Decorator function.

    Example:
        @login_required
        @discord_permission_required("team_captain")
        def verify_record(request):
            ...

        @login_required
        @discord_permission_required(["team_captain", "vice_captain"])
        def view_records(request):  # User needs ANY of these permissions
            ...

    """
    perms = (permission,) if isinstance(permission, str) else tuple(permission)

    def check_perms(user) -> bool:
        """Check if user has any of the required permissions.

        Args:
            user: The user to check.

        Returns:
            True if user has permission, False otherwise.

        Raises:
            PermissionDenied: If user is authenticated but lacks permission.

        """
        if not user.is_authenticated:
            logfire.debug(
                "Permission check skipped - user not authenticated",
                required_permissions=perms,
            )
            return False
        # Check if user has ANY of the required permissions
        if any(user.has_permission(p) for p in perms):
            logfire.debug(
                "Permission check passed",
                user_id=user.id,
                discord_id=user.discord_id,
                required_permissions=perms,
            )
            return True
        # Always raise PermissionDenied for authenticated users to avoid redirect loop
        # (otherwise user_passes_test redirects to login, but user is already logged in)
        logfire.warning(
            "Permission denied via decorator",
            user_id=user.id,
            discord_id=user.discord_id,
            required_permissions=perms,
            user_discord_roles=list(user.discord_roles.keys()) if user.discord_roles else [],
        )
        raise PermissionDenied

    return user_passes_test(check_perms, login_url=login_url)


def any_captain_required(
    login_url: str | None = None,
    raise_exception: bool = False,
) -> Callable[[F], F]:
    """Shortcut decorator for views requiring captain or vice captain permission.

    Args:
        login_url: URL to redirect to if not logged in.
        raise_exception: If True, raise PermissionDenied instead of redirecting.

    Returns:
        Decorator function.

    """
    return discord_permission_required(
        [Permissions.TEAM_CAPTAIN, Permissions.VICE_CAPTAIN],
        login_url=login_url,
        raise_exception=raise_exception,
    )


def any_admin_required(
    login_url: str | None = None,
    raise_exception: bool = False,
) -> Callable[[F], F]:
    """Shortcut decorator for views requiring any admin permission.

    Args:
        login_url: URL to redirect to if not logged in.
        raise_exception: If True, raise PermissionDenied instead of redirecting.

    Returns:
        Decorator function.

    """
    return discord_permission_required(
        [
            Permissions.APP_ADMIN,
            Permissions.LINK_ADMIN,
            Permissions.MEMBERSHIP_ADMIN,
            Permissions.RACING_ADMIN,
        ],
        login_url=login_url,
        raise_exception=raise_exception,
    )


def team_member_required(
    login_url: str | None = None,
    raise_exception: bool = False,
) -> Callable[[F], F]:
    """Require team member permission for a view.

    Without this permission, users can only access the index page and their
    personal profile pages (including verifications). All other pages require
    this permission.

    Args:
        login_url: URL to redirect to if not logged in.
        raise_exception: If True, raise PermissionDenied instead of redirecting.

    Returns:
        Decorator function.

    """
    return discord_permission_required(
        Permissions.TEAM_MEMBER,
        login_url=login_url,
        raise_exception=raise_exception,
    )

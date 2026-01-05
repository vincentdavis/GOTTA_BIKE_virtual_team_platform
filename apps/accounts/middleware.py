"""Middleware for accounts app."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from django.shortcuts import redirect
from django.urls import reverse

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse


class ProfileCompletionMiddleware:
    """Middleware to enforce profile completion for authenticated users.

    Redirects authenticated users with incomplete profiles to the profile
    edit page. Exempts certain URLs that users need to access during the
    completion process.
    """

    # URL patterns that are exempt from the profile completion check
    EXEMPT_URL_PATTERNS: ClassVar[list[str]] = [
        # Profile-related URLs (user needs to complete profile)
        r"^/user/profile/edit/$",
        r"^/user/profile/verify-zwift/$",
        r"^/user/profile/unverify-zwift/$",
        # Auth URLs (login, logout, etc.)
        r"^/accounts/",
        # API endpoints (bot API, cron API)
        r"^/api/",
        # Admin (superusers/staff may need access)
        r"^/admin/",
        # Static/media files
        r"^/static/",
        r"^/media/",
        # Debug toolbar (development only)
        r"^/__debug__/",
        r"^/__reload__/",
        # Magic links (legacy auth)
        r"^/m/",
    ]

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Initialize middleware.

        Args:
            get_response: The next middleware or view in the chain.

        """
        self.get_response = get_response
        # Compile regex patterns once at startup for efficiency
        self._exempt_patterns = [re.compile(pattern) for pattern in self.EXEMPT_URL_PATTERNS]

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Process the request.

        Args:
            request: The HTTP request.

        Returns:
            The HTTP response, either from the next handler or a redirect.

        """
        # Only check for authenticated users
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Skip check for superusers (they always have access)
        if request.user.is_superuser:
            return self.get_response(request)

        # Check if URL is exempt
        if self._is_exempt_url(request.path):
            return self.get_response(request)

        # Check if profile is complete
        if not request.user.is_profile_complete:
            return redirect(reverse("accounts:profile_edit"))

        return self.get_response(request)

    def _is_exempt_url(self, path: str) -> bool:
        """Check if the URL path is exempt from profile completion check.

        Args:
            path: The URL path to check.

        Returns:
            True if the URL is exempt, False otherwise.

        """
        return any(pattern.match(path) for pattern in self._exempt_patterns)

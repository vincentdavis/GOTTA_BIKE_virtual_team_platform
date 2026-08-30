"""Startup checks that must run when the app serves, and only then.

Kept out of ``settings.py`` on purpose. Settings are imported by every management command,
including the ``collectstatic`` and ``tailwind`` steps the Docker build runs before any
secret exists -- a guard there fails the image build rather than the deployment. WSGI and
ASGI are imported when the app is about to answer requests, which is exactly when a signing
key matters.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

INSECURE_PREFIX = "django-insecure"


def check_secret_key() -> None:
    """Refuse to serve with the published default SECRET_KEY.

    The default lives in ``config.py`` in a public repository, so a deployment that forgets
    to set it signs sessions and password-reset tokens with a value anyone can read. That
    matters more here than for a closed codebase, because other teams run their own copies.

    Raises:
        ImproperlyConfigured: If serving with DEBUG off and the default key.

    """
    if not settings.DEBUG and settings.SECRET_KEY.startswith(INSECURE_PREFIX):
        raise ImproperlyConfigured(
            "SECRET_KEY is still the development default. Set the SECRET_KEY environment "
            "variable to a unique random value before serving with DEBUG=False. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(50))"'
        )

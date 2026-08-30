"""WSGI config for gotta_bike_platform project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

from gotta_bike_platform.startup_checks import check_secret_key

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "gotta_bike_platform.settings"
)

application = get_wsgi_application()

# After the application is built, so settings are loaded: refuse to serve with the
# published default signing key. Here rather than in settings.py, which every
# management command imports -- including the Docker build's collectstatic step.
check_secret_key()

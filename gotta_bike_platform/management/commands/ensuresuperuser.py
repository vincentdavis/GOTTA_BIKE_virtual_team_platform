"""Management command to ensure a superuser exists."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from gotta_bike_platform.config import settings


class Command(BaseCommand):
    """Ensure a superuser exists, creating one from env vars if needed."""

    help = "Ensure a superuser exists. Creates one from environment variables if none exists."

    def handle(self, *args, **options) -> None:
        """Execute the command."""
        User = get_user_model()

        # Check if any superuser exists
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write(self.style.SUCCESS("Superuser already exists."))
            return

        # Check if we have credentials configured
        username = settings.superuser_username
        password = settings.superuser_password
        email = settings.superuser_email or ""

        if not username or not password:
            self.stdout.write(
                self.style.WARNING(
                    "No superuser exists and SUPERUSER_USERNAME/SUPERUSER_PASSWORD not set. "
                    "Skipping superuser creation."
                )
            )
            return

        # Create the superuser
        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )
        self.stdout.write(
            self.style.SUCCESS(f"Superuser '{username}' created successfully.")
        )

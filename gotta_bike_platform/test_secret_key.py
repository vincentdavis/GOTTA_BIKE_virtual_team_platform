"""The published SECRET_KEY default must not be usable in production.

The default lives in config.py in a public repository, so a deployment that forgets to set
SECRET_KEY signs its sessions and password-reset tokens with a value anyone can read. That
matters more here than for a closed codebase, because other teams run their own copies.
"""

import subprocess  # noqa: S404
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_INSECURE = "django-insecure-change-me-in-production"
_REAL = "a-unique-key-for-this-deployment"


def _boot(debug: str, secret_key: str):
    """Start Django in a subprocess with the given settings.

    A subprocess because the guard runs at settings-import time, which has already happened
    in this one.

    Args:
        debug: Value for the DEBUG env var.
        secret_key: Value for the SECRET_KEY env var.

    Returns:
        The completed process.

    """
    import os

    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "gotta_bike_platform.settings",
        "DEBUG": debug,
        "SECRET_KEY": secret_key,
    }
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_production_refuses_the_default_key():
    result = _boot("false", _INSECURE)
    assert result.returncode != 0
    assert "SECRET_KEY is still the development default" in result.stderr


def test_production_accepts_a_real_key():
    assert _boot("false", _REAL).returncode == 0


def test_development_still_runs_on_the_default():
    """Otherwise a fresh clone cannot start without inventing a key first."""
    assert _boot("true", _INSECURE).returncode == 0

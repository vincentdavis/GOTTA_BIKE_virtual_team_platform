"""The published SECRET_KEY default must not be usable when serving.

The default lives in config.py in a public repository, so a deployment that forgets to set
SECRET_KEY signs its sessions and password-reset tokens with a value anyone can read. That
matters more here than for a closed codebase, because other teams run their own copies.

The check lives in wsgi.py and asgi.py, not settings.py. Settings are imported by every
management command, including the collectstatic and tailwind steps the Docker build runs
before any secret exists -- a guard there fails the image build rather than the deployment,
which is exactly what happened the first time this shipped.
"""

import os
import subprocess  # noqa: S404
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_INSECURE = "django-insecure-change-me-in-production"
_REAL = "a-unique-key-for-this-deployment"
_MESSAGE = "SECRET_KEY is still the development default"


def _run(argv: list[str], secret_key: str | None) -> subprocess.CompletedProcess:
    """Run a command in a subprocess with a controlled environment.

    A subprocess because the check runs at import time, which has already happened here.
    DEBUG is forced off because the repo's own .env turns it on, and production has it off.

    Args:
        argv: The command to run, after the interpreter.
        secret_key: Value for SECRET_KEY, or None to leave it unset as the build does.

    Returns:
        The completed process.

    """
    env = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "DEBUG")}
    env["DJANGO_SETTINGS_MODULE"] = "gotta_bike_platform.settings"
    env["DEBUG"] = "false"
    if secret_key is not None:
        env["SECRET_KEY"] = secret_key
    return subprocess.run([sys.executable, *argv], cwd=_ROOT, env=env, capture_output=True, text=True)  # noqa: S603


def test_serving_refuses_the_default_key():
    result = _run(["-c", "import gotta_bike_platform.wsgi"], _INSECURE)
    assert result.returncode != 0
    assert _MESSAGE in result.stderr


def test_asgi_refuses_it_too():
    """Both entry points, so the guard cannot be sidestepped by the server choice."""
    result = _run(["-c", "import gotta_bike_platform.asgi"], _INSECURE)
    assert result.returncode != 0
    assert _MESSAGE in result.stderr


def test_serving_accepts_a_real_key():
    assert _run(["-c", "import gotta_bike_platform.wsgi"], _REAL).returncode == 0


def test_the_docker_build_steps_run_without_a_secret_key():
    """The regression this file exists for.

    The image is built before any secret is available, and the build runs management
    commands. Guarding in settings.py broke the build rather than the deploy.
    """
    for command in (
        ["manage.py", "check"],
        ["manage.py", "collectstatic", "--noinput", "--dry-run"],
    ):
        result = _run(command, None)
        assert _MESSAGE not in result.stderr, f"{' '.join(command)} was blocked during build"
        assert result.returncode == 0, f"{' '.join(command)} failed: {result.stderr[-400:]}"

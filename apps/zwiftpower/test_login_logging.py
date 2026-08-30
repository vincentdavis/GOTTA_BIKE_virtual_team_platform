"""The ZwiftPower login must not put the team's credentials in the log.

Scraping ZwiftPower with a shared Zwift login is accepted: there is no official API and no
alternative. That decision does not extend to copying the login into Logfire on every run,
which is a separate thing and costs nothing to stop.
"""

import re
from pathlib import Path

_CLIENT = Path(__file__).resolve().parent / "zp_client.py"


def test_no_log_line_interpolates_the_credentials():
    """Catches the f-string form specifically, which is how it got there."""
    source = _CLIENT.read_text()

    logged = re.findall(r"logfire\.\w+\(([^)]*)\)", source, re.S)
    for call in logged:
        assert "_username" not in call, f"login email reaches a log line: {call.strip()[:90]}"
        assert "_password" not in call, f"password reaches a log line: {call.strip()[:90]}"


def test_the_login_is_still_logged_without_the_identifier():
    """Removing the whole line would lose a useful diagnostic; only the identifier goes."""
    assert "Attempting ZwiftPower login" in _CLIENT.read_text()

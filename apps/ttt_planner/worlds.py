"""Canonical list of Zwift worlds, shared by routes and segments.

Loaded from ``data/zwift_worlds.json`` so the controlled vocabulary lives in one
place. Used as the ``world`` field choices on both ``Route`` and ``Segment`` (via
the ``world_choices`` callable, so updating the JSON doesn't require a migration).
"""

import json
from pathlib import Path

_DATA_FILE = Path(__file__).resolve().parent / "data" / "zwift_worlds.json"
_WORLDS = json.loads(_DATA_FILE.read_text())

#: World display names in dataset order.
WORLD_NAMES: list[str] = [w["name"] for w in _WORLDS]

#: Map whatsonzwift slug -> display name (used when importing by URL).
SLUG_TO_NAME: dict[str, str] = {w["slug"]: w["name"] for w in _WORLDS}


def world_choices() -> list[tuple[str, str]]:
    """Return ``(value, label)`` choices for a world field.

    Returns:
        A list of ``(name, name)`` tuples for every Zwift world.

    """
    return [(name, name) for name in WORLD_NAMES]

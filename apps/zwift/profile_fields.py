"""Profile fields Zwift also knows about: country and gender.

Zwift reports both on the racing profile, and riders often have them set there and
not here. These helpers fill a *blank* platform field from Zwift and never overwrite
one the rider has set -- a rider's own answer outranks Zwift's, and the profile card
flags a disagreement rather than resolving it silently.

The two fields disagree for different reasons and want different handling by whoever
sees the flag: a Zwift country is frequently wrong (riders pick it once and forget),
while a Zwift gender is the one they actually race under.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django_countries import countries

if TYPE_CHECKING:
    from apps.accounts.models import User

# Fields this module is allowed to touch, so a save can never widen past them.
FILLABLE = ("country", "gender")


def zwift_country(racing_profile: dict | None) -> str:
    """Read the rider's country from a racing profile as an ISO alpha-2 code.

    Zwift sends ``countryAlpha3`` ("usa"); the platform stores alpha-2 ("US").

    Args:
        racing_profile: The zauth racing-profile dict, or None.

    Returns:
        The alpha-2 code, or "" when absent or not a country we recognise.

    """
    alpha3 = ((racing_profile or {}).get("data") or {}).get("countryAlpha3") or ""
    if not alpha3:
        return ""
    # Unknown codes come back as "" rather than raising, so junk simply means "no value".
    return countries.alpha2(alpha3) or ""


def zwift_gender(racing_profile: dict | None) -> str:
    """Read the rider's gender from a racing profile as a platform gender value.

    Zwift sends a boolean ``male``. False means female -- it is a real answer, not a
    missing one, so absence has to be distinguished from it explicitly.

    Args:
        racing_profile: The zauth racing-profile dict, or None.

    Returns:
        "male", "female", or "" when Zwift did not report it.

    """
    male = ((racing_profile or {}).get("data") or {}).get("male")
    if male is None:
        return ""
    return "male" if male else "female"


def fill_missing(user: User, racing_profile: dict | None) -> list[str]:
    """Fill blank country/gender on a user from their Zwift racing profile.

    Only ever writes a field that is currently empty. A field the rider has already
    answered is left alone even when Zwift disagrees, and the disagreement is surfaced
    on the profile card instead.

    Args:
        user: The rider whose profile may be filled.
        racing_profile: Their zauth racing-profile dict, or None.

    Returns:
        The names of the fields that were filled, empty when nothing changed.

    """
    if not racing_profile:
        return []

    filled = []
    if not user.country:
        country = zwift_country(racing_profile)
        if country:
            user.country = country
            filled.append("country")
    if not user.gender:
        gender = zwift_gender(racing_profile)
        if gender:
            user.gender = gender
            filled.append("gender")

    if filled:
        # Narrow save: this runs on a page the rider may have open alongside their
        # profile form, and must not write back stale values for anything else.
        user.save(update_fields=filled)
        logfire.info(
            "Filled blank profile fields from Zwift",
            user_id=user.pk,
            fields=filled,
        )
    return filled

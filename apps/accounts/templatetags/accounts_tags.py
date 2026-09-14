"""Template tags and filters for accounts app."""

import json
from typing import TYPE_CHECKING

import markdown
from django import template
from django.templatetags.static import static
from django.utils.safestring import mark_safe

from apps.accounts.markdown_safe import render_untrusted_markdown
from apps.accounts.permission_registry import get_permission_help

if TYPE_CHECKING:
    from decimal import Decimal

register = template.Library()

ZP_CATEGORY_EMOJI_FIELDS = {
    "A+": "zp_a_plus_emoji",
    "A": "zp_a_emoji",
    "B": "zp_b_emoji",
    "C": "zp_c_emoji",
    "D": "zp_d_emoji",
    "E": "zp_e_emoji",
}

ZR_CATEGORY_EMOJI_FIELDS = {
    "Diamond": "zr_diamond_emoji",
    "Ruby": "zr_ruby_emoji",
    "Emerald": "zr_emerald_emoji",
    "Sapphire": "zr_sapphire_emoji",
    "Amethyst": "zr_amethyst_emoji",
    "Platinum": "zr_platinum_emoji",
    "Gold": "zr_gold_emoji",
    "Silver": "zr_silver_emoji",
    "Bronze": "zr_bronze_emoji",
    "Copper": "zr_copper_emoji",
}

AGE_EMOJI_FIELDS = {
    "Jnr": "age_jnr_emoji",
    "U23": "age_u23_emoji",
    "Snr": "age_snr_emoji",
    "Vet": "age_vet_emoji",
    "Mas": "age_mas_emoji",
    "50+": "age_50plus_emoji",
    "60+": "age_60plus_emoji",
    "70+": "age_70plus_emoji",
}

# The bundled artwork behind each bracket. Age is the only family that ships its own set, so
# a rider always gets a mark; an upload for that bracket simply takes precedence.
AGE_DEFAULT_ICONS = {
    "Jnr": "accounts/age/age-jnr.svg",
    "U23": "accounts/age/age-u23.svg",
    "Snr": "accounts/age/age-snr.svg",
    "Vet": "accounts/age/age-vet.svg",
    "Mas": "accounts/age/age-mas.svg",
    "50+": "accounts/age/age-50plus.svg",
    "60+": "accounts/age/age-60plus.svg",
    "70+": "accounts/age/age-70plus.svg",
}

PHENOTYPE_EMOJI_FIELDS = {
    "All-Rounder": "phenotype_allrounder_emoji",
    "Climber": "phenotype_climber_emoji",
    "Puncheur": "phenotype_puncheur_emoji",
    "Time Trialist": "phenotype_tt_emoji",
    "Sprinter": "phenotype_sprinter_emoji",
    "Pursuiter": "phenotype_pursuiter_emoji",
}


@register.filter
def permission_help(constance_key: str) -> dict | None:
    """Get permission help data for a Constance key.

    Args:
        constance_key: The Constance setting key (e.g., "PERM_APP_ADMIN_ROLES")

    Returns:
        Dict with name, description, views or None if not found.

    """
    return get_permission_help(constance_key)


@register.filter
def render_markdown(value: str) -> str:
    """Render markdown text as HTML.

    Args:
        value: Markdown text to render.

    Returns:
        Rendered HTML marked as safe.

    """
    if not value:
        return ""
    # Convert markdown to HTML, enabling useful extensions
    html = markdown.markdown(
        value,
        extensions=[
            "nl2br",       # Convert newlines to <br>
            "sane_lists",  # Better list handling
            "tables",      # Support tables
        ],
    )
    return mark_safe(html)  # noqa: S308  # trusted admin-authored markdown (CMS/announcements)


@register.filter
def render_markdown_untrusted(value: str) -> str:
    """Render markdown written by an ordinary user, sanitising the HTML it produces.

    Use this -- never :func:`render_markdown` -- for any text a rider can type
    (ticket details and resolutions, membership-application messages,
    availability-grid descriptions). Python-Markdown passes raw HTML straight
    through, so the unsanitised filter would let a rider store a script in a page
    an admin later opens. See :mod:`apps.accounts.markdown_safe` for the allowlist.

    Args:
        value: Markdown text to render.

    Returns:
        Sanitised HTML marked as safe.

    """
    return mark_safe(render_untrusted_markdown(value))  # noqa: S308  # sanitised by markdown_safe allowlist


@register.filter
def render_markdown_inline(value: str) -> str:
    """Render markdown but unwrap a single top-level paragraph.

    Same extensions as :func:`render_markdown`, but a lone wrapping ``<p>`` is
    stripped so the result sits inline inside a form label or table cell (where a
    block ``<p>`` would break the layout). Multi-block content (lists, several
    paragraphs) is left untouched. Intended for short, admin-authored strings
    such as signup-question labels and helper text.

    Args:
        value: Markdown text to render.

    Returns:
        Rendered inline HTML marked as safe.

    """
    if not value:
        return ""
    html = markdown.markdown(
        value,
        extensions=[
            "nl2br",       # Convert newlines to <br>
            "sane_lists",  # Better list handling
            "tables",      # Support tables
        ],
    ).strip()
    if html.startswith("<p>") and html.endswith("</p>") and html.count("<p>") == 1:
        html = html[len("<p>") : -len("</p>")]
    return mark_safe(html)  # noqa: S308  # trusted admin-authored markdown (signup questions)


@register.filter
def parse_json_list(value: str) -> list:
    """Parse a JSON string to a list.

    Args:
        value: JSON string representing a list.

    Returns:
        Parsed list, or empty list if parsing fails.

    """
    if not value:
        return []
    try:
        result = json.loads(value)
        if isinstance(result, list):
            return result
        return []
    except (json.JSONDecodeError, TypeError):
        return []


@register.filter
def get_item(dictionary: dict, key: str) -> list:
    """Get item from dictionary by key.

    Args:
        dictionary: The dictionary to look up.
        key: The key to retrieve.

    Returns:
        The value for the key, or empty list if not found.

    """
    if dictionary is None:
        return []
    return dictionary.get(key, [])


@register.filter
def kg_to_lbs(kg: Decimal | float | None) -> str:
    """Convert kg to lbs and format.

    Args:
        kg: Weight in kilograms.

    Returns:
        Weight in pounds as string, or empty string if None.

    """
    if kg is None:
        return ""
    return str(round(float(kg) * 2.20462, 1))


@register.filter
def cm_to_inches(cm: int | None) -> str:
    """Convert cm to inches and format.

    Args:
        cm: Height in centimeters.

    Returns:
        Height in inches as string, or empty string if None.

    """
    if cm is None:
        return ""
    return str(round(float(cm) * 0.393701, 1))


@register.filter
def weight_dual(kg: Decimal | float | None) -> str:
    """Format weight with both kg and lbs.

    Args:
        kg: Weight in kilograms.

    Returns:
        Formatted string like '72.5 kg (159.8 lbs)'.

    """
    if kg is None:
        return "-"
    lbs = round(float(kg) * 2.20462, 1)
    return f"{kg} kg ({lbs} lbs)"


@register.filter
def height_dual(cm: int | None) -> str:
    """Format height with both cm and inches.

    Args:
        cm: Height in centimeters.

    Returns:
        Formatted string like '175 cm (68.9 in)'.

    """
    if cm is None:
        return "-"
    inches = round(float(cm) * 0.393701, 1)
    return f"{cm} cm ({inches} in)"


@register.filter
def weight_diff(record_weight: Decimal | float | None, zp_weight: Decimal | float | None) -> str:
    """Calculate weight difference between record and ZwiftPower.

    Args:
        record_weight: Weight from the verification record (kg).
        zp_weight: Current weight from ZwiftPower (kg).

    Returns:
        Formatted difference string with arrow indicator.

    """
    if record_weight is None or zp_weight is None:
        return ""
    diff = float(record_weight) - float(zp_weight)
    if abs(diff) < 0.05:
        return "no change"
    if diff > 0:
        return f"+{diff:.1f} kg"
    return f"{diff:.1f} kg"


@register.simple_tag(takes_context=True)
def zp_category_badge(context, category, is_women=False):
    """Render a ZP category as emoji image or badge.

    If a ZP category emoji is uploaded in SiteSettings, renders an img tag.
    Otherwise falls back to the standard badge span.

    Args:
        context: Template context (for site_settings access).
        category: The ZP category letter (e.g., "A", "B", "C").
        is_women: Whether to use badge-secondary styling (women's category).

    Returns:
        HTML string for the category display.

    """
    if not category or category == "-":
        return mark_safe('<span class="text-base-content/30">-</span>')

    from django.utils.html import escape

    escaped_category = escape(category)

    site_settings = context.get("site_settings")
    if site_settings:
        field_name = ZP_CATEGORY_EMOJI_FIELDS.get(category)
        if field_name:
            emoji_file = getattr(site_settings, field_name, None)
            if emoji_file:
                return mark_safe(  # noqa: S308
                    f'<img src="{escape(emoji_file.url)}" alt="{escaped_category}" '
                    f'class="h-7 w-7" title="Category {escaped_category}">'
                )

    badge_class = "badge-secondary" if is_women else "badge-primary"
    return mark_safe(f'<span class="badge {badge_class} badge-sm">{escaped_category}</span>')  # noqa: S308


@register.simple_tag(takes_context=True)
def zr_category_badge(context, category):
    """Render a ZR category as emoji image or badge.

    If a ZR category emoji is uploaded in SiteSettings, renders an img tag.
    Otherwise falls back to the standard badge span.

    Args:
        context: Template context (for site_settings access).
        category: The ZR category name (e.g., "Gold", "Silver").

    Returns:
        HTML string for the category display.

    """
    if not category:
        return mark_safe('<span class="text-base-content/30">-</span>')

    from django.utils.html import escape

    escaped_category = escape(category)

    site_settings = context.get("site_settings")
    if site_settings:
        field_name = ZR_CATEGORY_EMOJI_FIELDS.get(category)
        if field_name:
            emoji_file = getattr(site_settings, field_name, None)
            if emoji_file:
                return mark_safe(  # noqa: S308
                    f'<img src="{escape(emoji_file.url)}" alt="{escaped_category}" '
                    f'class="h-7 w-7" title="{escaped_category}">'
                )

    return mark_safe(f'<span class="badge badge-secondary badge-sm">{escaped_category}</span>')  # noqa: S308


@register.simple_tag(takes_context=True)
def phenotype_icon(context, phenotype):
    """Render a phenotype as an emoji image if a custom icon is uploaded.

    Returns empty string if no custom icon exists for the phenotype.

    Args:
        context: Template context (for site_settings access).
        phenotype: The phenotype name (e.g., "Sprinter", "Climber").

    Returns:
        HTML img tag string, or empty string if no icon uploaded.

    """
    if not phenotype:
        return ""

    site_settings = context.get("site_settings")
    if not site_settings:
        return ""

    field_name = PHENOTYPE_EMOJI_FIELDS.get(phenotype)
    if not field_name:
        return ""

    emoji_file = getattr(site_settings, field_name, None)
    if not emoji_file:
        return ""

    from django.utils.html import escape

    return mark_safe(  # noqa: S308
        f'<img src="{escape(emoji_file.url)}" alt="{escape(phenotype)}" '
        f'class="h-5 w-5" title="{escape(phenotype)}">'
    )


# The three maps above, keyed so one tag can serve all of them.
_ICON_MAPS = {
    "category": ZP_CATEGORY_EMOJI_FIELDS,
    "zr": ZR_CATEGORY_EMOJI_FIELDS,
    "phenotype": PHENOTYPE_EMOJI_FIELDS,
    "age": AGE_EMOJI_FIELDS,
}

# Kinds that ship artwork of their own, used when nothing has been uploaded.
_DEFAULT_ICONS = {"age": AGE_DEFAULT_ICONS}


@register.simple_tag(takes_context=True)
def site_icon_url(context, kind: str, value: str) -> str:
    """Return the URL of the uploaded icon for a category, tier or phenotype.

    The URL rather than an ``<img>``, unlike the three badge tags beside it, so a caller can
    put the icon INSIDE its own badge next to the text label. That matters twice over: the
    badge tags' fallback uses ``badge-secondary`` and ``badge-primary``, which this project's
    own accessibility notes record as failing contrast, and an icon that replaces the label
    rather than joining it leaves colour carrying the meaning on its own.

    Args:
        context: Template context, for ``site_settings``.
        kind: "category", "zr" or "phenotype".
        value: The category, tier or phenotype name.

    Returns:
        The uploaded icon's URL, else the bundled default for kinds that ship one, else "".

    """
    if not value:
        return ""

    site_settings = context.get("site_settings")
    field_name = _ICON_MAPS.get(kind, {}).get(value)
    if site_settings and field_name:
        icon = getattr(site_settings, field_name, None)
        if icon:
            return icon.url

    # No upload: fall back to bundled artwork where the kind ships some. Resolved through
    # static() rather than hardcoded, so it survives WhiteNoise's hashed filenames.
    bundled = _DEFAULT_ICONS.get(kind, {}).get(value)
    return static(bundled) if bundled else ""


# ZwiftPower sends a few ISO 3166-2 subdivisions where everything else is ISO 3166-1, and
# django_countries knows nothing about them. They are mapped to the parent country's flag,
# with the nation's own name kept as the label -- so a Welsh rider shows the Union Flag and
# reads "Wales", rather than showing a broken image or losing the detail entirely.
_SUBDIVISIONS = {
    "GB-ENG": ("GB", "England"),
    "GB-WLS": ("GB", "Wales"),
    "GB-SCT": ("GB", "Scotland"),
    "GB-NIR": ("GB", "Northern Ireland"),
}


@register.simple_tag
def country_flag(code: str) -> dict:
    """Resolve an upstream country code to a flag image and a readable name.

    ``Country(code).flag`` builds a URL from the string it is given WITHOUT checking that the
    country exists, so an unrecognised code yields a link to a missing image rather than an
    error. Everything here is therefore validated against the real country list first, and a
    code that is not one falls back to showing the code itself.

    Args:
        code: The country code as stored, in whatever case upstream used.

    Returns:
        ``url`` and ``name`` for a known country, or an empty dict.

    """
    from django_countries import countries
    from django_countries.fields import Country

    raw = (code or "").strip().upper()
    if not raw:
        return {}

    country_code, name = _SUBDIVISIONS.get(raw, (raw, ""))
    if country_code not in countries:
        return {}

    country = Country(country_code)
    return {"url": country.flag, "name": name or country.name}


@register.simple_tag
def team_kit_rows(user) -> list[dict]:
    """Return a rider's team kit statuses for display, one row per active kit.

    A tag rather than view context so any template showing a rider -- the profile today, the
    roster or an export view later -- can render it without each view remembering to.

    Args:
        user: The rider.

    Returns:
        Rows with ``kit``, ``status``, ``label`` and ``badge``; empty when no kits are defined.

    """
    from apps.team.kits import kit_rows

    return kit_rows(user)

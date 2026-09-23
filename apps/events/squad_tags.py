"""Squad tags: the labels an event offers, and the subset each of its squads carries.

``Event.squad_tags`` is the only source of truth. ``Squad.tags`` is a subset of it, picked on
the squad form by anyone who may edit the squad. Saving the event runs :func:`prune_squad_tags`,
so a tag removed from the event leaves every squad, and a tag whose case the admin changed is
rewritten on every squad to the event's new spelling.

Tags are admin-authored text: templates render them auto-escaped, never ``|safe`` and never as
markdown, and Logfire gets counts, never the tags themselves.
"""

import re

import logfire
from django.core.exceptions import ValidationError

MAX_TAG_LENGTH = 40
MAX_SQUAD_TAGS = 30

_WHITESPACE = re.compile(r"\s+")


def normalize_tags(values: object) -> list[str]:
    """Tidy a list of tags without changing what the admin meant.

    Each tag is stripped and its internal whitespace collapsed to one space; empty tags are
    dropped, and a tag that differs from an earlier one only in case is dropped too, keeping the
    first spelling. Case is kept (unlike the timezone chips, which uppercase).

    The list comes back in alphabetical order, ignoring case. Every surface reads its order
    from here -- the event's chips, the squad form's checkboxes, the event page's tag filter
    and each squad's badges -- so a tag is always in the place a reader expects, whenever it
    was added.

    Anything that is not a list or tuple reads as no tags, and non-string items are skipped, so
    a malformed stored value can never be iterated character by character. The event form
    type-checks first and says so, rather than relying on this.

    Args:
        values: The tags as submitted or stored.

    Returns:
        The normalised tags.

    """
    if not isinstance(values, list | tuple):
        return []
    seen: set[str] = set()
    tags: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        tag = _WHITESPACE.sub(" ", value).strip()
        key = tag.casefold()
        if not tag or key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    tags.sort(key=str.casefold)
    return tags


def clean_tag_list(value: object) -> list[str]:
    """Type-check and normalise a tag list as submitted through a JSON box.

    An empty box, or ``null``, reads as no tags: both columns are ``NOT NULL``, so letting
    ``None`` through would fail the save with an ``IntegrityError`` rather than a message.

    Args:
        value: The parsed JSON value (None when the input was empty).

    Returns:
        The normalised tags.

    Raises:
        ValidationError: If the value is not a list of strings.

    """
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError("Squad tags must be a list of text labels.", code="invalid_type")
    return normalize_tags(value)


def clean_event_tags(value: object) -> list[str]:
    """Validate an event's squad tags as submitted, for the event form and the admin.

    Args:
        value: The parsed JSON value (None when the input was empty).

    Returns:
        The normalised tags.

    Raises:
        ValidationError: If the value is not a list of strings, a tag is longer than
            ``MAX_TAG_LENGTH`` or there are more than ``MAX_SQUAD_TAGS`` tags. Nothing is
            truncated: the admin is told and decides.

    """
    tags = clean_tag_list(value)
    too_long = [tag for tag in tags if len(tag) > MAX_TAG_LENGTH]
    if too_long:
        raise ValidationError(
            "A squad tag can be at most %(max)d characters; shorten %(tags)s.",
            code="tag_too_long",
            params={"max": MAX_TAG_LENGTH, "tags": ", ".join(f"“{tag}”" for tag in too_long)},
        )
    if len(tags) > MAX_SQUAD_TAGS:
        raise ValidationError(
            "An event can have at most %(max)d squad tags; this list has %(count)d. Remove some.",
            code="too_many_tags",
            params={"max": MAX_SQUAD_TAGS, "count": len(tags)},
        )
    return tags


def event_tag_spellings(event_tags: object) -> dict[str, str]:
    """Map each of the event's tags, casefolded, to the event's spelling.

    Args:
        event_tags: ``Event.squad_tags``.

    Returns:
        ``{casefolded tag: tag}``, in the event's order.

    """
    return {tag.casefold(): tag for tag in normalize_tags(event_tags)}


def tags_in_event(tags: object, event_tags: object) -> list[str]:
    """Keep only the tags the event offers, in the event's spelling and order.

    Args:
        tags: A squad's tags.
        event_tags: ``Event.squad_tags``.

    Returns:
        The squad's tags that the event still lists.

    """
    carried = {tag.casefold() for tag in normalize_tags(tags)}
    return [tag for key, tag in event_tag_spellings(event_tags).items() if key in carried]


def prune_squad_tags(event) -> int:
    """Bring every squad of the event back to a subset of the event's tags.

    A tag the event no longer lists is dropped; one it lists in a different case is rewritten
    to the event's spelling; the rest are reordered as the event lists them. Only squads whose
    tags actually change are written, and each such write touches only ``tags`` and
    ``updated_at`` -- naming ``updated_at`` is what makes its ``auto_now`` fire at all, since
    Django runs ``pre_save`` only for the fields in ``update_fields``, and a squad whose tags
    were rewritten really was modified.

    Args:
        event: The event whose squads to prune, with ``squad_tags`` as just saved.

    Returns:
        The number of squads changed.

    """
    changed = 0
    for squad in event.squads.only("pk", "tags", "updated_at"):
        kept = tags_in_event(squad.tags, event.squad_tags)
        if kept != squad.tags:
            squad.tags = kept
            squad.save(update_fields=["tags", "updated_at"])
            changed += 1
    # Counts only: tags are free text.
    logfire.info("Squad tags pruned to the event's list", event_id=event.pk, squads_changed=changed)
    return changed

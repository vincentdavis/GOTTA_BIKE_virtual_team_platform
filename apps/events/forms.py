"""Forms for events app."""

import json
from typing import ClassVar

from django import forms
from django.contrib.auth import get_user_model

# GRID_DEFAULT_SETTINGS comes from grid_defaults so the form and the enforcement
# logic cannot drift on which toggles an event governs.
from apps.events.grid_defaults import SETTINGS as GRID_DEFAULT_SETTINGS
from apps.events.models import SQUAD_GENDER_CHOICES, Event, EventSignup, SignupQuestion, Squad
from apps.events.signup_questions import MAX_OPTIONS_PER_QUESTION
from apps.team.models import DiscordChannel, DiscordRole

User = get_user_model()


def _get_channel_choices() -> list:
    """Build choices list for Discord channel Select widget.

    Returns grouped choices with optgroups by category name.
    Text-like channel types (text, announcement, forum) are included.

    Returns:
        List of choices with (value, label) tuples and optgroup tuples.

    """
    channels = DiscordChannel.objects.filter(
        channel_type__in=[
            DiscordChannel.ChannelType.TEXT,
            DiscordChannel.ChannelType.ANNOUNCEMENT,
            DiscordChannel.ChannelType.FORUM,
        ]
    ).order_by("category_name", "position")

    choices: list = [("0", "(none)")]

    groups: dict[str, list[tuple[str, str]]] = {}
    for ch in channels:
        group = ch.category_name or "Uncategorized"
        groups.setdefault(group, []).append((ch.channel_id, f"#{ch.name}"))

    for group_label, group_choices in groups.items():
        choices.append((group_label, group_choices))

    return choices


def _get_voice_channel_choices() -> list:
    """Build choices list for Discord voice/stage channel Select widget.

    Returns grouped choices with optgroups by category name.
    Only voice and stage channel types are included.

    Returns:
        List of choices with (value, label) tuples and optgroup tuples.

    """
    channels = DiscordChannel.objects.filter(
        channel_type__in=[
            DiscordChannel.ChannelType.VOICE,
            DiscordChannel.ChannelType.STAGE,
        ]
    ).order_by("category_name", "position")

    choices: list = [("0", "(none)")]

    groups: dict[str, list[tuple[str, str]]] = {}
    for ch in channels:
        group = ch.category_name or "Uncategorized"
        type_label = "Stage" if ch.channel_type == DiscordChannel.ChannelType.STAGE else "Voice"
        groups.setdefault(group, []).append((ch.channel_id, f"{ch.name} ({type_label})"))

    for group_label, group_choices in groups.items():
        choices.append((group_label, group_choices))

    return choices


def _get_role_choices(prefixes: list[str] | None = None) -> list:
    """Build choices list for Discord role Select widget.

    When multiple prefixes are supplied, the resulting list is structured as
    Django optgroups — one group per prefix — so the admin can scan a long
    dropdown without losing track of which prefix a role belongs to. When the
    list is empty/None, all roles are returned flat.

    Args:
        prefixes: If provided, only include roles whose name starts with one
            of these prefixes.

    Returns:
        Mixed list of ``("0", "(none)")`` tuple followed by either flat
        ``(role_id, label)`` tuples or ``(group_label, [(role_id, label), ...])``
        optgroup tuples.

    """
    choices: list = [("0", "(none)")]
    qs = DiscordRole.objects.order_by("-position")

    if not prefixes:
        choices.extend((role.role_id, f"@{role.name}") for role in qs)
        return choices

    # Group by prefix; preserve admin-chosen prefix order.
    from django.db.models import Q

    prefix_q = Q()
    for p in prefixes:
        prefix_q |= Q(name__startswith=p)
    filtered = list(qs.filter(prefix_q))

    for p in prefixes:
        group_roles = [(role.role_id, f"@{role.name}") for role in filtered if role.name.startswith(p)]
        if group_roles:
            choices.append((f"Prefix: {p}", group_roles))
    return choices


def _tri_state(raw: object) -> bool | None:
    """Read a "", "1", "0" choice as None / True / False.

    Args:
        raw: The submitted choice value.

    Returns:
        True, False, or None for "no event default".

    """
    if raw in ("", None):
        return None
    return str(raw) == "1"


class EventForm(forms.ModelForm):
    """Form for creating and editing events."""

    # Tri-state: "" leaves it to the captain, "1"/"0" set an event default. Declared
    # explicitly rather than left to the nullable model field: Django's form
    # BooleanField coerces the submitted string to a bool before clean_<field> runs,
    # so "0" would arrive as False and the "no default" case become unreachable.
    grid_default_max_races_question = forms.ChoiceField(
        required=False,
        choices=[("", "No default"), ("1", "Yes"), ("0", "No")],
        label="Default: Ask: Max number of races",
        widget=forms.Select(attrs={"class": "select select-sm w-full"}),
    )
    grid_default_rest_days_question = forms.ChoiceField(
        required=False,
        choices=[("", "No default"), ("1", "Yes"), ("0", "No")],
        label="Default: Ask: Rest days between races",
        widget=forms.Select(attrs={"class": "select select-sm w-full"}),
    )
    grid_default_hide_empty_days = forms.ChoiceField(
        required=False,
        choices=[("", "No default"), ("1", "Yes"), ("0", "No")],
        label="Default: Hide days with no available times",
        widget=forms.Select(attrs={"class": "select select-sm w-full"}),
    )
    grid_default_single_slot = forms.ChoiceField(
        required=False,
        choices=[("", "No default"), ("1", "Yes"), ("0", "No")],
        label="Default: Single Time Slot",
        widget=forms.Select(attrs={"class": "select select-sm w-full"}),
    )
    grid_default_expanded_features = forms.ChoiceField(
        required=False,
        choices=[("", "No default"), ("1", "Yes"), ("0", "No")],
        label="Default: Expand Features",
        widget=forms.Select(attrs={"class": "select select-sm w-full"}),
    )

    discord_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full filter-select"}),
    )
    signup_notification_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full filter-select"}),
    )

    class Meta:
        """Meta options for EventForm."""

        model = Event
        fields: ClassVar[list[str]] = [
            "title",
            "logo",
            "description",
            "config_option",
            "start_date",
            "end_date",
            "url",
            "discord_channel_id",
            "signup_notification_channel_id",
            "visible",
            "signups_open",
            "show_signups",
            "signup_instructions",
            "timezone_options",
            "timezone_required",
            "squad_gender_required",
            "require_race_verified_availability",
            "grid_default_max_races_question",
            "grid_enforce_max_races_question",
            "grid_default_rest_days_question",
            "grid_enforce_rest_days_question",
            "grid_default_hide_empty_days",
            "grid_enforce_hide_empty_days",
            "grid_default_single_slot",
            "grid_enforce_single_slot",
            "grid_default_expanded_features",
            "grid_enforce_expanded_features",
        ]
        widgets: ClassVar[dict] = {
            "title": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Event title"},
            ),
            "logo": forms.ClearableFileInput(
                attrs={"class": "file-input file-input-bordered w-full", "accept": "image/*"},
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 4,
                    "placeholder": "Event description (supports Markdown)",
                },
            ),
            "start_date": forms.DateInput(
                attrs={"class": "input input-bordered w-full", "type": "date"},
                format="%Y-%m-%d",
            ),
            "end_date": forms.DateInput(
                attrs={"class": "input input-bordered w-full", "type": "date"},
                format="%Y-%m-%d",
            ),
            "url": forms.URLInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "https://..."},
            ),
            "config_option": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "visible": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
            "signups_open": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
            "show_signups": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
            "signup_instructions": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 3,
                    "placeholder": "Instructions shown to users on the signup form",
                },
            ),
            "timezone_options": forms.HiddenInput(),
            "timezone_required": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
            "squad_gender_required": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
            "grid_enforce_max_races_question": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "grid_enforce_rest_days_question": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "grid_enforce_hide_empty_days": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "grid_enforce_single_slot": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "grid_enforce_expanded_features": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "require_race_verified_availability": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        """Initialize form with Discord channel choices."""
        super().__init__(*args, **kwargs)
        choices = _get_channel_choices()
        all_values = self._flat_choice_values(choices)

        for field_name in ("discord_channel_id", "signup_notification_channel_id"):
            current_value = str(self.initial.get(field_name, 0) or 0)
            field_choices = list(choices)
            if current_value != "0" and current_value not in all_values:
                field_choices.append((current_value, f"Unknown Channel ({current_value})"))
            self.fields[field_name].widget.choices = field_choices
            self.initial[field_name] = current_value

        # The tri-state defaults are declared as ChoiceFields, so the instance's
        # True/False/None has to be mapped onto the "1"/"0"/"" the select offers.
        for setting in GRID_DEFAULT_SETTINGS:
            name = f"grid_default_{setting}"
            value = getattr(self.instance, name, None) if self.instance else None
            self.initial[name] = "" if value is None else ("1" if value else "0")

    @staticmethod
    def _flat_choice_values(choices: list) -> set[str]:
        """Extract all values from a choices list including optgroups.

        Args:
            choices: Django choices list with possible optgroup tuples.

        Returns:
            Set of all choice values.

        """
        values = set()
        for item in choices:
            if isinstance(item[1], list | tuple) and item[1] and isinstance(item[1][0], tuple | list):
                for val, _label in item[1]:
                    values.add(str(val))
            else:
                values.add(str(item[0]))
        return values

    def clean_grid_default_max_races_question(self) -> bool | None:
        """Convert the tri-state choice back to True/False/None.

        Returns:
            The default, or None when the event sets none.

        """
        return _tri_state(self.cleaned_data.get("grid_default_max_races_question"))

    def clean_grid_default_rest_days_question(self) -> bool | None:
        """Convert the tri-state choice back to True/False/None.

        Returns:
            The default, or None when the event sets none.

        """
        return _tri_state(self.cleaned_data.get("grid_default_rest_days_question"))

    def clean_grid_default_hide_empty_days(self) -> bool | None:
        """Convert the tri-state choice back to True/False/None.

        Returns:
            The default, or None when the event sets none.

        """
        return _tri_state(self.cleaned_data.get("grid_default_hide_empty_days"))

    def clean_grid_default_single_slot(self) -> bool | None:
        """Convert the tri-state choice back to True/False/None.

        Returns:
            The default, or None when the event sets none.

        """
        return _tri_state(self.cleaned_data.get("grid_default_single_slot"))

    def clean_grid_default_expanded_features(self) -> bool | None:
        """Convert the tri-state choice back to True/False/None.

        Returns:
            The default, or None when the event sets none.

        """
        return _tri_state(self.cleaned_data.get("grid_default_expanded_features"))

    def clean_discord_channel_id(self) -> int:
        """Convert selected channel ID string back to int for the model.

        Returns:
            Channel ID as integer (0 for none).

        """
        value = self.cleaned_data.get("discord_channel_id", "0")
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def clean_signup_notification_channel_id(self) -> int:
        """Convert selected signup-notification channel ID string back to int.

        Returns:
            Channel ID as integer (0 for none).

        """
        value = self.cleaned_data.get("signup_notification_channel_id", "0")
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0


def _allowed_event_prefixes() -> list[str]:
    """Load the Constance allowed-prefixes list with a sensible fallback.

    Returns:
        List of allowed prefix strings.

    """
    from constance import config

    try:
        value = json.loads(config.EVENT_ROLE_PREFIXES)
        if isinstance(value, list):
            return [str(p) for p in value]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return ["$", ">", "¡", "~", "^"]


class EventRoleSetupForm(forms.ModelForm):
    """Form for editing event Discord role settings (prefixes, head captain role, event role)."""

    prefixes = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox checkbox-sm"}),
        label="Discord Prefixes",
        help_text="One or more channel/role prefixes. Roles matching any selected prefix appear in role pickers.",
    )

    head_captain_role_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
        label="Head Captain Role",
    )

    event_role = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
        label="Event Role",
    )

    coordinator_role_ids = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox checkbox-sm coord-role-cb"}),
        label="Regional/Group Coordinators",
    )

    region_role_ids = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox checkbox-sm region-role-cb"}),
        label="Region Roles",
        help_text="Squads can only pick their Region Role from these.",
    )

    captain_role_ids = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox checkbox-sm captain-role-cb"}),
        label="Captain Roles",
        help_text="Squads can only pick their Captain Discord Role from these.",
    )

    class Meta:
        """Meta options for EventRoleSetupForm."""

        model = Event
        fields: ClassVar[list[str]] = [
            "prefixes",
            "head_captain_role_id",
            "event_role",
            "coordinator_role_ids",
            "region_role_ids",
            "captain_role_ids",
        ]

    def __init__(self, *args, **kwargs) -> None:
        """Initialize form with prefix choices and Discord role choices."""
        super().__init__(*args, **kwargs)

        # Prefix checkboxes — choices come from the Constance allowed list.
        allowed = _allowed_event_prefixes()
        self.fields["prefixes"].choices = [(p, p) for p in allowed]

        # Head captain role: all roles server-side; the template JS filters the
        # <select> options live by the currently-checked prefixes above.
        role_choices = _get_role_choices()
        current_role = str(self.initial.get("head_captain_role_id", 0) or 0)
        role_values = {c[0] for c in role_choices}
        if current_role != "0" and current_role not in role_values:
            role_choices.append((current_role, f"Unknown Role ({current_role})"))
        self.fields["head_captain_role_id"].widget.choices = role_choices
        self.initial["head_captain_role_id"] = current_role

        # Event role: all roles server-side; the template JS filters the
        # <select> options live by the currently-checked prefixes above.
        event_role_choices = _get_role_choices()
        current_event_role = str(self.initial.get("event_role", 0) or 0)
        event_role_values = {c[0] for c in event_role_choices}
        if current_event_role != "0" and current_event_role not in event_role_values:
            event_role_choices.append((current_event_role, f"Unknown Role ({current_event_role})"))
        self.fields["event_role"].widget.choices = event_role_choices
        self.initial["event_role"] = current_event_role

        # Regional/Group Coordinators: choices are all roles whose name starts
        # with any allowed prefix from Constance. The template filters this
        # set further by the event's currently-checked prefixes via JS so the
        # admin can save prefixes and coordinator picks in a single submit.
        # Security: the choices list is the authoritative server-side gate.
        # No fallback "Unknown Role" entries are included — previously-saved
        # IDs that no longer match an allowed prefix (e.g. a Discord role was
        # renamed off-prefix) silently drop out and cannot be re-submitted.
        from django.db.models import Q

        prefix_q = Q()
        for p in allowed:
            prefix_q |= Q(name__startswith=p)
        coord_roles = list(DiscordRole.objects.filter(prefix_q).order_by("name"))
        coord_choices: list[tuple[str, str]] = [(r.role_id, r.name) for r in coord_roles]
        # Initial is only the saved IDs that intersect the live choices — any
        # stale IDs are dropped on re-render rather than re-checked by default.
        valid_ids = {c[0] for c in coord_choices}
        # Also drop ids that no longer match *this event's* prefixes, not just the global
        # ones the choices are drawn from. Leaving them checked was silently fatal: the JS
        # filter hides an off-prefix role, the browser resubmits it because it is still
        # checked, and clean() then rejects the whole form -- so an unrelated edit
        # elsewhere on the page appeared to save and did not. Rendering them unchecked
        # lets the list heal itself on the next save.
        event_prefixes = [str(p) for p in (getattr(self.instance, "prefixes", None) or []) if str(p)]
        by_id = {r.role_id: r.name for r in coord_roles}

        def _on_prefix(rid: str) -> bool:
            if not event_prefixes:
                return True
            name = by_id.get(rid, "")
            return any(name.startswith(prefix) for prefix in event_prefixes)

        for field_name in ("coordinator_role_ids", "region_role_ids", "captain_role_ids"):
            self.fields[field_name].choices = coord_choices
            self.initial[field_name] = [
                str(rid)
                for rid in (self.initial.get(field_name) or [])
                if str(rid) in valid_ids and _on_prefix(str(rid))
            ]

    def clean_head_captain_role_id(self) -> int:
        """Convert selected role ID string back to int for the model.

        Returns:
            Role ID as integer (0 for none).

        """
        value = self.cleaned_data.get("head_captain_role_id", "0")
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def clean_event_role(self) -> int:
        """Convert selected event role ID string back to int for the model.

        Returns:
            Role ID as integer (0 for none).

        """
        value = self.cleaned_data.get("event_role", "0")
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def clean_coordinator_role_ids(self) -> list[str]:
        """Validate each submitted coordinator role ID server-side.

        Defense in depth: even though the field's ``choices`` list is built
        from roles whose names start with an allowed prefix, this method
        re-resolves each submitted ID against ``DiscordRole`` and rejects any
        role that no longer exists or whose name no longer starts with one of
        the Constance-allowed prefixes (``EVENT_ROLE_PREFIXES``). Prevents
        bypassing the UI gate via crafted POST payloads.

        Returns:
            Deduplicated list of validated role-ID strings.

        """
        return self._clean_prefixed_role_ids("coordinator_role_ids")

    def clean_region_role_ids(self) -> list[str]:
        """Validate each submitted region role ID server-side.

        Same gate as the coordinator list: re-resolve every submitted id against
        ``DiscordRole`` and reject anything unknown or off-prefix, so a crafted POST
        cannot widen what a squad's Region Role may later be set to.

        Returns:
            Deduplicated list of validated role-ID strings.

        """
        return self._clean_prefixed_role_ids("region_role_ids")

    def clean_captain_role_ids(self) -> list[str]:
        """Validate each submitted captain role ID server-side.

        Same gate as the other two lists: every submitted id is re-resolved against
        ``DiscordRole`` and rejected if unknown or off-prefix, so a crafted POST cannot
        widen what a squad's Captain Discord Role may later be set to.

        Returns:
            Deduplicated list of validated role-ID strings.

        """
        return self._clean_prefixed_role_ids("captain_role_ids")

    def _clean_prefixed_role_ids(self, field_name: str) -> list[str]:
        """Validate a list of Discord role ids against the allowed prefixes.

        Args:
            field_name: The form field holding the submitted ids.

        Returns:
            Deduplicated list of validated role-ID strings, in submitted order.

        Raises:
            forms.ValidationError: If any submitted ID is unknown, no longer in the
                DiscordRole table, or no longer starts with an allowed prefix.

        """
        raw = self.cleaned_data.get(field_name) or []
        if not raw:
            return []

        submitted_ids = [str(rid).strip() for rid in raw if str(rid).strip()]
        roles_by_id = {r.role_id: r for r in DiscordRole.objects.filter(role_id__in=submitted_ids)}
        allowed = _allowed_event_prefixes()

        unknown: list[str] = []
        off_prefix: list[str] = []
        seen: list[str] = []
        for rid in submitted_ids:
            if rid in seen:
                continue
            role = roles_by_id.get(rid)
            if role is None:
                unknown.append(rid)
                continue
            if not any(role.name.startswith(p) for p in allowed):
                off_prefix.append(f'"@{role.name}"')
                continue
            seen.append(rid)

        if unknown:
            raise forms.ValidationError(
                f"Unknown Discord role ID{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}"
            )
        if off_prefix:
            raise forms.ValidationError(
                f"Role{'s' if len(off_prefix) > 1 else ''} must start with an allowed prefix "
                f"({', '.join(allowed)}): {', '.join(off_prefix)}"
            )
        return seen

    def clean_prefixes(self) -> list[str]:
        """Coerce the MultipleChoiceField output into a clean list of strings.

        Returns:
            Deduplicated list of valid prefix strings, preserving submitted order.

        """
        raw = self.cleaned_data.get("prefixes") or []
        allowed = set(_allowed_event_prefixes())
        seen: list[str] = []
        for item in raw:
            value = str(item).strip()
            if value and value in allowed and value not in seen:
                seen.append(value)
        return seen

    def clean(self) -> dict:
        """Validate that at least one prefix is set and role names match.

        Returns:
            dict: The cleaned form data.

        """
        cleaned = super().clean()
        prefixes: list[str] = cleaned.get("prefixes") or []

        if not prefixes:
            self.add_error("prefixes", "At least one prefix is required for role setup.")
            return cleaned

        head_captain_id = cleaned.get("head_captain_role_id", 0)
        event_role_id = cleaned.get("event_role", 0)

        def _role_matches(role_name: str) -> bool:
            return any(role_name.startswith(p) for p in prefixes)

        if head_captain_id and head_captain_id != 0:
            role = DiscordRole.objects.filter(role_id=str(head_captain_id)).first()
            if role and not _role_matches(role.name):
                self.add_error(
                    "head_captain_role_id",
                    f'Role name "@{role.name}" must start with one of: {", ".join(prefixes)}.',
                )

        if event_role_id and event_role_id != 0:
            role = DiscordRole.objects.filter(role_id=str(event_role_id)).first()
            if role and not _role_matches(role.name):
                self.add_error(
                    "event_role",
                    f'Role name "@{role.name}" must start with one of: {", ".join(prefixes)}.',
                )

        for field_name in ("coordinator_role_ids", "region_role_ids", "captain_role_ids"):
            selected = cleaned.get(field_name) or []
            if not selected:
                continue
            roles_by_id = {
                r.role_id: r for r in DiscordRole.objects.filter(role_id__in=[str(i) for i in selected])
            }
            invalid = [
                f'"@{roles_by_id[str(rid)].name}"'
                for rid in selected
                if str(rid) in roles_by_id and not _role_matches(roles_by_id[str(rid)].name)
            ]
            if invalid:
                self.add_error(
                    field_name,
                    f"These roles do not match any selected prefix: {', '.join(invalid)}.",
                )

        return cleaned


class SquadForm(forms.ModelForm):
    """Form for creating and editing squads."""

    discord_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full filter-select"}),
    )

    audio_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full filter-select"}),
        label="Audio Channel",
    )

    team_discord_role = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full filter-select"}),
        label="Discord Role",
    )

    discord_captain_role = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full filter-select"}),
        label="Captain Discord Role",
    )

    regional_coordinator_role = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full filter-select"}),
        label="Regional Coordinator",
    )

    region_role = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full filter-select"}),
        label="Region Role",
    )

    gender = forms.ChoiceField(
        required=True,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
        label="Gender",
    )

    class Meta:
        """Meta options for SquadForm."""

        model = Squad
        fields: ClassVar[list[str]] = [
            "name",
            "squad_timezone",
            "gender",
            "discord_channel_id",
            "audio_channel_id",
            "discord_captain_role",
            "regional_coordinator_role",
            "region_role",
            "team_discord_role",
            "min_zwift_category",
            "max_zwift_category",
            "enforce_min_zwift_category",
            "enforce_max_zwift_category",
            "min_womens_zwift_category",
            "max_womens_zwift_category",
            "enforce_min_womens_zwift_category",
            "enforce_max_womens_zwift_category",
            "min_zwift_racing_category",
            "max_zwift_racing_category",
            "enforce_min_zwift_racing_category",
            "enforce_max_zwift_racing_category",
            "require_zauth",
            "captains",
            "vice_captains",
            "min_zftp_wkg",
            "max_zftp_wkg",
            "enforce_min_zftp_wkg",
            "enforce_max_zftp_wkg",
            "min_zftp_w",
            "max_zftp_w",
            "enforce_min_zftp_w",
            "enforce_max_zftp_w",
            "min_zmap_wkg",
            "max_zmap_wkg",
            "enforce_min_zmap_wkg",
            "enforce_max_zmap_wkg",
            "min_zmap_w",
            "max_zmap_w",
            "enforce_min_zmap_w",
            "enforce_max_zmap_w",
            "enforce_gender",
            "url",
            "invite_url",
            "captain_notifications",
        ]
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Squad name"},
            ),
            "squad_timezone": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "e.g., America/New_York"},
            ),
            "min_zwift_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "max_zwift_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "enforce_min_zwift_category": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "enforce_max_zwift_category": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "min_womens_zwift_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "max_womens_zwift_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "enforce_min_womens_zwift_category": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "enforce_max_womens_zwift_category": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "min_zwift_racing_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "max_zwift_racing_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "enforce_min_zwift_racing_category": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "enforce_max_zwift_racing_category": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "require_zauth": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "min_zftp_wkg": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "step": "0.01", "min": "0", "placeholder": "e.g. 3.74"},
            ),
            "max_zftp_wkg": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "step": "0.01", "min": "0", "placeholder": "e.g. 3.74"},
            ),
            "enforce_min_zftp_wkg": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "enforce_max_zftp_wkg": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "min_zftp_w": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "step": "1", "min": "0", "placeholder": "e.g. 200"},
            ),
            "max_zftp_w": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "step": "1", "min": "0", "placeholder": "e.g. 200"},
            ),
            "enforce_min_zftp_w": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "enforce_max_zftp_w": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "min_zmap_wkg": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "step": "0.01", "min": "0", "placeholder": "e.g. 3.74"},
            ),
            "max_zmap_wkg": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "step": "0.01", "min": "0", "placeholder": "e.g. 3.74"},
            ),
            "enforce_min_zmap_wkg": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "enforce_max_zmap_wkg": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "min_zmap_w": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "step": "1", "min": "0", "placeholder": "e.g. 200"},
            ),
            "max_zmap_w": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "step": "1", "min": "0", "placeholder": "e.g. 200"},
            ),
            "enforce_min_zmap_w": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "enforce_max_zmap_w": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "enforce_gender": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary checkbox-sm"},
            ),
            "url": forms.URLInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "https://..."},
            ),
            "invite_url": forms.URLInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "https://..."},
            ),
            "captain_notifications": forms.CheckboxInput(
                attrs={"class": "toggle toggle-primary"},
            ),
            # Not SelectMultiple: DaisyUI's .select is display:inline-flex with a fixed
            # height, which lays a multi-select's options out side by side on one line.
            # Checkboxes also drop the ctrl/cmd-click, which is unusable over a long
            # rider list -- one mis-click clears every other pick.
            # No attrs: Django copies widget attrs onto the wrapping <div> as well as
            # each input, and DaisyUI's .checkbox is a fixed 1rem box -- on the wrapper
            # that collapses the whole list. Styled by .captain-picker in the template.
            "captains": forms.CheckboxSelectMultiple(),
            "vice_captains": forms.CheckboxSelectMultiple(),
        }

    # Non-model: whether a newly picked leader should also join the squad's roster.
    # Leadership and membership are separate today -- a captain need not race -- so
    # this is asked per save rather than assumed either way.
    captains_add_as_members = forms.BooleanField(
        required=False,
        label="Also add new captains to the squad",
        widget=forms.CheckboxInput(attrs={"class": "checkbox checkbox-primary checkbox-sm"}),
    )
    vice_captains_add_as_members = forms.BooleanField(
        required=False,
        label="Also add new vice-captains to the squad",
        widget=forms.CheckboxInput(attrs={"class": "checkbox checkbox-primary checkbox-sm"}),
    )

    def __init__(
        self,
        *args,
        event_prefixes: list[str] | None = None,
        coordinator_role_ids: list[str] | None = None,
        region_role_ids: list[str] | None = None,
        captain_role_ids: list[str] | None = None,
        event=None,
        **kwargs,
    ) -> None:
        """Initialize form with Discord channel choices and captain labels.

        Args:
            *args: Positional arguments passed to ModelForm.
            event: The parent event, used to limit the captain pickers to its signups.
                None leaves both pickers empty.
            event_prefixes: The parent event's Discord prefixes. When empty, the role field is disabled.
            coordinator_role_ids: The parent event's configured coordinator role
                IDs (from the Role Setup page). The Regional Coordinator picker is
                limited to these; when empty, that field is disabled.
            region_role_ids: The parent event's configured region role IDs (Role Setup
                page). The Region Role picker is limited to these; when empty, that
                field is disabled.
            captain_role_ids: The parent event's configured captain role IDs (Role Setup
                page). The Captain Discord Role picker is limited to these; when empty,
                that field is disabled.
            **kwargs: Keyword arguments passed to ModelForm.

        """
        super().__init__(*args, **kwargs)
        self.event_prefixes = list(event_prefixes or [])
        self.coordinator_role_ids = [str(rid) for rid in (coordinator_role_ids or [])]
        self.region_role_ids = [str(rid) for rid in (region_role_ids or [])]
        self.captain_role_ids = [str(rid) for rid in (captain_role_ids or [])]
        # The event's head captain role must never end up on a squad. Squad roles are
        # auto-assigned to riders as they join, so pointing one at the head captain role
        # would hand every member of that squad event-wide control of squads, Discord
        # roles and eligibility. Stripped from the pickers below and refused in clean().
        self.head_captain_role_id = str(getattr(event, "head_captain_role_id", 0) or 0)

        # Squad gender is a fixed set (Male/Female/COED) and required when configuring a squad.
        self.fields["gender"].choices = [("", "Select gender"), *SQUAD_GENDER_CHOICES]

        # Captains are chosen from the event's registered signups, not from the squad's
        # own members -- picking a leader is often the step that *brings* someone into a
        # squad. The per-member "Set as Captain" control on the squad panel still works
        # and writes the same two relations.
        signups = (
            User.objects.filter(
                event_signups__event=event,
                event_signups__status=EventSignup.Status.REGISTERED,
            )
            .distinct()
            .order_by("first_name", "last_name", "username")
            if event is not None
            else User.objects.none()
        )
        for name in ("captains", "vice_captains"):
            self.fields[name].queryset = signups
            self.fields[name].label_from_instance = lambda u: u.get_full_name() or u.discord_username or u.username

        choices = _get_channel_choices()

        current_value = str(self.initial.get("discord_channel_id", 0) or 0)

        all_values = EventForm._flat_choice_values(choices)
        if current_value != "0" and current_value not in all_values:
            choices.append((current_value, f"Unknown Channel ({current_value})"))

        self.fields["discord_channel_id"].widget.choices = choices
        self.initial["discord_channel_id"] = current_value

        # Populate voice channel choices for audio_channel_id
        voice_choices = _get_voice_channel_choices()
        current_audio = str(self.initial.get("audio_channel_id", 0) or 0)
        audio_values = EventForm._flat_choice_values(voice_choices)
        if current_audio != "0" and current_audio not in audio_values:
            voice_choices.append((current_audio, f"Unknown Channel ({current_audio})"))
        self.fields["audio_channel_id"].widget.choices = voice_choices
        self.initial["audio_channel_id"] = current_audio

        # Populate Discord role choices filtered by event prefixes (any-of match).
        # When prefixes is empty, the field is shown but disabled and presents a
        # placeholder, matching the pre-multi-prefix behavior.
        if self.event_prefixes:
            role_choices = self._without_head_captain(_get_role_choices(prefixes=self.event_prefixes))
        else:
            role_choices = [("0", "(none — set event prefixes first)")]
            self.fields["team_discord_role"].widget.attrs["disabled"] = True
        current_role = str(self.initial.get("team_discord_role", 0) or 0)
        role_values = EventForm._flat_choice_values(role_choices)
        if current_role != "0" and current_role not in role_values:
            role_choices.append((current_role, f"Unknown Role ({current_role})"))
        self.fields["team_discord_role"].widget.choices = role_choices
        self.initial["team_discord_role"] = current_role

        # Captain role: chosen from the event's configured captain roles (Role Setup
        # page), like the region and coordinator pickers below.
        if self.captain_role_ids:
            captain_name_by_id = dict(
                DiscordRole.objects.filter(role_id__in=self.captain_role_ids).values_list("role_id", "name")
            )
            captain_role_choices = [("0", "(none)")]
            captain_role_choices.extend(
                (rid, f"@{captain_name_by_id.get(rid, f'Unknown Role ({rid})')}")
                for rid in self.captain_role_ids
                if rid != self.head_captain_role_id
            )
        else:
            captain_role_choices = [("0", "(none — set captain roles in Role Setup first)")]
            self.fields["discord_captain_role"].widget.attrs["disabled"] = True
        # Drop a stored value that is no longer an allowed captain role rather than
        # offering it back, or clean_discord_captain_role would make the squad
        # un-saveable. Mirrors the region and coordinator handling.
        current_captain_role = str(self.initial.get("discord_captain_role", 0) or 0)
        if current_captain_role not in {c[0] for c in captain_role_choices}:
            current_captain_role = "0"
        self.fields["discord_captain_role"].widget.choices = captain_role_choices
        self.initial["discord_captain_role"] = current_captain_role

        # Region role: same prefix filtering as the squad/captain roles. This
        # role is auto-added to riders when they join the squad and removed when
        # they leave (unless another squad still grants it — enforced in views).
        # Region Role: chosen from the event's configured region roles (Role Setup page),
        # not from every prefixed role. Same shape as the coordinator picker below --
        # narrowing it is what stops a squad handing out access through an arbitrary role.
        if self.region_role_ids:
            region_name_by_id = dict(
                DiscordRole.objects.filter(role_id__in=self.region_role_ids).values_list("role_id", "name")
            )
            region_role_choices = [("0", "(none)")]
            region_role_choices.extend(
                (rid, f"@{region_name_by_id.get(rid, f'Unknown Role ({rid})')}")
                for rid in self.region_role_ids
                if rid != self.head_captain_role_id
            )
        else:
            region_role_choices = [("0", "(none — set region roles in Role Setup first)")]
            self.fields["region_role"].widget.attrs["disabled"] = True
        # Drop a stored value that is no longer an allowed region role rather than
        # offering it back, or clean_region_role would make the squad un-saveable.
        # Mirrors the coordinator handling directly below.
        current_region_role = str(self.initial.get("region_role", 0) or 0)
        if current_region_role not in {c[0] for c in region_role_choices}:
            current_region_role = "0"
        self.fields["region_role"].widget.choices = region_role_choices
        self.initial["region_role"] = current_region_role

        # Regional coordinator: chosen from the event's configured coordinator
        # roles (Role Setup page), not the event prefixes. When the event has no
        # coordinator roles the field is shown but disabled with a placeholder.
        if self.coordinator_role_ids:
            coord_name_by_id = dict(
                DiscordRole.objects.filter(role_id__in=self.coordinator_role_ids).values_list("role_id", "name")
            )
            coord_role_choices = [("0", "(none)")]
            coord_role_choices.extend(
                (rid, f"@{coord_name_by_id.get(rid, f'Unknown Role ({rid})')}")
                for rid in self.coordinator_role_ids
                if rid != self.head_captain_role_id
            )
        else:
            coord_role_choices = [("0", "(none — set coordinator roles in Role Setup first)")]
            self.fields["regional_coordinator_role"].widget.attrs["disabled"] = True
        # Drop a stale stored value — a coordinator role since removed from the
        # event — instead of offering it back. Otherwise the whole squad edit
        # form would be un-saveable, because clean_regional_coordinator_role
        # rejects the preselected value. Mirrors EventRoleSetupForm's handling of
        # coordinator_role_ids, which strips stale ids from the initial data.
        current_coord_role = str(self.initial.get("regional_coordinator_role", 0) or 0)
        coord_role_values = {c[0] for c in coord_role_choices}
        if current_coord_role not in coord_role_values:
            current_coord_role = "0"
        self.fields["regional_coordinator_role"].widget.choices = coord_role_choices
        self.initial["regional_coordinator_role"] = current_coord_role

    def _without_head_captain(self, choices: list) -> list:
        """Drop the event's head captain role from a role picker.

        Handles the optgroup shape ``_get_role_choices`` returns when the event has more
        than one prefix, where the pairs live one level down.

        Args:
            choices: A Django choices list, flat or grouped.

        Returns:
            The same list without the head captain role.

        """
        blocked = self.head_captain_role_id
        if blocked == "0":
            return choices
        pruned = []
        for value, label in choices:
            if isinstance(label, (list, tuple)):
                group = [pair for pair in label if str(pair[0]) != blocked]
                if group:
                    pruned.append((value, group))
            elif str(value) != blocked:
                pruned.append((value, label))
        return pruned

    def _refuse_head_captain(self, role_id: int, *, what: str) -> None:
        """Reject the head captain role wherever a squad tries to use it.

        The picker no longer offers it, but the picker is not the gate -- a crafted POST
        would otherwise sail through, and this is a privilege escalation rather than a
        cosmetic mistake.

        Args:
            role_id: The submitted role id.
            what: How to name the field in the error, e.g. "squad role".

        Raises:
            forms.ValidationError: If the role is the event's head captain role.

        """
        if role_id and str(role_id) == self.head_captain_role_id:
            raise forms.ValidationError(
                f"The event's Head Captain role cannot be used as a {what}. Riders are given a "
                "squad's roles when they join it, so this would grant every member of this squad "
                "event-wide control of squads, Discord roles and eligibility."
            )

    def clean(self) -> dict:
        """Reject anyone listed as both captain and vice-captain.

        The per-member control enforces this by construction -- promoting to one
        removes the other -- but these two pickers are independent, so it has to be
        checked here.

        Returns:
            The cleaned data.

        """
        cleaned = super().clean()
        captains = set(cleaned.get("captains") or [])
        vices = set(cleaned.get("vice_captains") or [])
        both = captains & vices
        if both:
            names = ", ".join(sorted(u.get_full_name() or u.username for u in both))
            self.add_error("vice_captains", f"Already listed as captain: {names}")
        return cleaned

    def clean_discord_channel_id(self) -> int:
        """Convert selected channel ID string back to int for the model.

        Returns:
            Channel ID as integer (0 for none).

        """
        value = self.cleaned_data.get("discord_channel_id", "0")
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def clean_audio_channel_id(self) -> int:
        """Convert selected audio channel ID string back to int for the model.

        Returns:
            Channel ID as integer (0 for none).

        """
        value = self.cleaned_data.get("audio_channel_id", "0")
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def clean_team_discord_role(self) -> int:
        """Convert selected role ID string back to int and validate prefix.

        Returns:
            Role ID as integer (0 for none).

        Raises:
            forms.ValidationError: If a role is selected without a prefix or doesn't match the prefix.

        """
        value = self.cleaned_data.get("team_discord_role", "0")
        try:
            role_id = int(value)
        except (ValueError, TypeError):
            return 0

        self._refuse_head_captain(role_id, what="squad role")

        if role_id and role_id != 0 and not self.event_prefixes:
            raise forms.ValidationError("Set at least one event prefix before assigning a role.")

        if role_id and role_id != 0 and self.event_prefixes:
            role = DiscordRole.objects.filter(role_id=str(role_id)).first()
            if role and not any(role.name.startswith(p) for p in self.event_prefixes):
                raise forms.ValidationError(
                    f'Role "@{role.name}" must start with one of: {", ".join(self.event_prefixes)}.'
                )

        return role_id

    def clean_region_role(self) -> int:
        """Convert selected region role ID string back to int and validate prefix.

        Returns:
            Role ID as integer (0 for none).

        Raises:
            forms.ValidationError: If a role is selected without a prefix or doesn't match the prefix.

        """
        value = self.cleaned_data.get("region_role", "0")
        try:
            role_id = int(value)
        except (ValueError, TypeError):
            return 0

        self._refuse_head_captain(role_id, what="region role")

        # The authoritative gate. The picker only offers the event's configured region
        # roles, but a crafted POST carrying any other role id is rejected here.
        if role_id and str(role_id) not in self.region_role_ids:
            raise forms.ValidationError(
                "Select a region role configured for this event on the Role Setup page."
            )

        if role_id and role_id != 0 and not self.event_prefixes:
            raise forms.ValidationError("Set at least one event prefix before assigning a role.")

        if role_id and role_id != 0 and self.event_prefixes:
            role = DiscordRole.objects.filter(role_id=str(role_id)).first()
            if role and not any(role.name.startswith(p) for p in self.event_prefixes):
                raise forms.ValidationError(
                    f'Role "@{role.name}" must start with one of: {", ".join(self.event_prefixes)}.'
                )

        return role_id

    def clean_regional_coordinator_role(self) -> int:
        """Convert the coordinator role ID to int and validate it's an event coordinator role.

        The submitted role must be one of the event's configured coordinator
        roles (``Event.coordinator_role_ids``, set on the Role Setup page). This
        is the authoritative server-side gate — a crafted POST carrying any other
        role id is rejected regardless of the rendered dropdown.

        Returns:
            Role ID as integer (0 for none).

        Raises:
            forms.ValidationError: If a non-coordinator role id is submitted.

        """
        value = self.cleaned_data.get("regional_coordinator_role", "0")
        try:
            role_id = int(value)
        except (ValueError, TypeError):
            return 0
        self._refuse_head_captain(role_id, what="coordinator role")
        if role_id and str(role_id) not in self.coordinator_role_ids:
            raise forms.ValidationError(
                "Select a coordinator role configured for this event on the Role Setup page."
            )
        return role_id

    def clean_discord_captain_role(self) -> int:
        """Convert selected captain role ID string back to int and validate prefix.

        Returns:
            Role ID as integer (0 for none).

        Raises:
            forms.ValidationError: If a role is selected without a prefix or doesn't match the prefix.

        """
        value = self.cleaned_data.get("discord_captain_role", "0")
        try:
            role_id = int(value)
        except (ValueError, TypeError):
            return 0

        self._refuse_head_captain(role_id, what="squad captain role")

        # The authoritative gate. The picker only offers the event's configured captain
        # roles, but a crafted POST carrying any other role id is rejected here.
        if role_id and str(role_id) not in self.captain_role_ids:
            raise forms.ValidationError(
                "Select a captain role configured for this event on the Role Setup page."
            )

        if role_id and role_id != 0 and not self.event_prefixes:
            raise forms.ValidationError("Set at least one event prefix before assigning a role.")

        if role_id and role_id != 0 and self.event_prefixes:
            role = DiscordRole.objects.filter(role_id=str(role_id)).first()
            if role and not any(role.name.startswith(p) for p in self.event_prefixes):
                raise forms.ValidationError(
                    f'Role "@{role.name}" must start with one of: {", ".join(self.event_prefixes)}.'
                )

        return role_id


class SignupQuestionForm(forms.ModelForm):
    """Create or edit a custom signup question for an event."""

    options = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "textarea textarea-bordered w-full",
                "rows": 4,
                "placeholder": "One option per line (choice questions only)",
            }
        ),
        label="Options",
        help_text="One option per line. Used only for single / multiple choice questions.",
    )

    class Meta:
        """Meta options for SignupQuestionForm."""

        model = SignupQuestion
        fields: ClassVar[list[str]] = ["label", "question_type", "options", "required", "help_text", "order"]
        widgets: ClassVar[dict] = {
            "label": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Question text"},
            ),
            "question_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "required": forms.CheckboxInput(attrs={"class": "checkbox checkbox-primary"}),
            "help_text": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Optional helper text"},
            ),
            "order": forms.NumberInput(attrs={"class": "input input-bordered w-24", "min": 0}),
        }

    def __init__(self, *args, answers_exist: bool = False, **kwargs) -> None:
        """Initialize, prefilling the options textarea from the stored list.

        Args:
            *args: Positional arguments passed to ModelForm.
            answers_exist: True when a signup already answered this question, which
                freezes ``question_type`` (see clean_question_type).
            **kwargs: Keyword arguments passed to ModelForm.

        """
        super().__init__(*args, **kwargs)
        self.answers_exist = answers_exist
        if self.instance and self.instance.pk and isinstance(self.instance.options, list):
            self.initial["options"] = "\n".join(self.instance.options)

    def clean_options(self) -> list[str]:
        """Parse the options textarea into a deduped list of non-empty lines.

        Returns:
            List of option label strings.

        Raises:
            forms.ValidationError: If more than the allowed number of options.

        """
        raw = self.cleaned_data.get("options", "") or ""
        seen: list[str] = []
        for line in raw.splitlines():
            value = line.strip()
            if value and value not in seen:
                seen.append(value)
        if len(seen) > MAX_OPTIONS_PER_QUESTION:
            raise forms.ValidationError(f"At most {MAX_OPTIONS_PER_QUESTION} options are allowed.")
        return seen

    def clean_question_type(self) -> str:
        """Freeze the question type once any signup has answered it.

        Returns:
            The selected question type.

        Raises:
            forms.ValidationError: If the type is changed while answers exist.

        """
        value = self.cleaned_data.get("question_type")
        if self.answers_exist and self.instance and self.instance.pk and value != self.instance.question_type:
            raise forms.ValidationError(
                "This question already has answers, so its type can't be changed. "
                "Delete it and add a new question instead."
            )
        return value

    def clean(self) -> dict:
        """Require options for choice questions; clear options for text/yes-no.

        Returns:
            The cleaned data dict.

        """
        cleaned = super().clean()
        qtype = cleaned.get("question_type")
        options = cleaned.get("options") or []
        if qtype in (SignupQuestion.Type.SINGLE, SignupQuestion.Type.MULTI):
            if not options:
                self.add_error("options", "Add at least one option for a choice question.")
        else:
            cleaned["options"] = []
        return cleaned

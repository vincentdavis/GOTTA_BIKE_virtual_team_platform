"""Forms for events app."""

import json
from typing import ClassVar

from django import forms

from apps.events.models import Event, Squad
from apps.team.models import DiscordChannel, DiscordRole


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


class EventForm(forms.ModelForm):
    """Form for creating and editing events."""

    discord_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )
    signup_notification_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )

    class Meta:
        """Meta options for EventForm."""

        model = Event
        fields: ClassVar[list[str]] = [
            "title",
            "logo",
            "description",
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
            "squad_gender_options",
            "squad_gender_required",
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
            "squad_gender_options": forms.HiddenInput(),
            "squad_gender_required": forms.CheckboxInput(
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

    class Meta:
        """Meta options for EventRoleSetupForm."""

        model = Event
        fields: ClassVar[list[str]] = [
            "prefixes",
            "head_captain_role_id",
            "event_role",
            "coordinator_role_ids",
        ]

    def __init__(self, *args, **kwargs) -> None:
        """Initialize form with prefix choices and Discord role choices."""
        super().__init__(*args, **kwargs)

        # Prefix checkboxes — choices come from the Constance allowed list.
        allowed = _allowed_event_prefixes()
        self.fields["prefixes"].choices = [(p, p) for p in allowed]

        # Head captain role: all roles
        role_choices = _get_role_choices()
        current_role = str(self.initial.get("head_captain_role_id", 0) or 0)
        role_values = {c[0] for c in role_choices}
        if current_role != "0" and current_role not in role_values:
            role_choices.append((current_role, f"Unknown Role ({current_role})"))
        self.fields["head_captain_role_id"].widget.choices = role_choices
        self.initial["head_captain_role_id"] = current_role

        # Event role: all roles (validation checks prefix, not the dropdown filter)
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
        self.fields["coordinator_role_ids"].choices = coord_choices
        # Initial is only the saved IDs that intersect the live choices — any
        # stale IDs are dropped on re-render rather than re-checked by default.
        valid_ids = {c[0] for c in coord_choices}
        self.initial["coordinator_role_ids"] = [
            str(rid) for rid in (self.initial.get("coordinator_role_ids") or []) if str(rid) in valid_ids
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

        Raises:
            forms.ValidationError: If any submitted ID is unknown, no longer
                in the DiscordRole table, or no longer starts with an allowed
                prefix.

        """
        raw = self.cleaned_data.get("coordinator_role_ids") or []
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

        coordinator_ids = cleaned.get("coordinator_role_ids") or []
        if coordinator_ids:
            roles_by_id = {
                r.role_id: r for r in DiscordRole.objects.filter(role_id__in=[str(i) for i in coordinator_ids])
            }
            invalid = [
                f'"@{roles_by_id[str(rid)].name}"'
                for rid in coordinator_ids
                if str(rid) in roles_by_id and not _role_matches(roles_by_id[str(rid)].name)
            ]
            if invalid:
                self.add_error(
                    "coordinator_role_ids",
                    f"These roles do not match any selected prefix: {', '.join(invalid)}.",
                )

        return cleaned


class SquadForm(forms.ModelForm):
    """Form for creating and editing squads."""

    discord_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )

    audio_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
        label="Audio Channel",
    )

    team_discord_role = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
        label="Discord Role",
    )

    discord_captain_role = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
        label="Captain Discord Role",
    )

    gender = forms.ChoiceField(
        required=False,
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
            "team_discord_role",
            "min_zwift_category",
            "max_zwift_category",
            "min_zwift_racing_category",
            "max_zwift_racing_category",
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
            "min_zwift_category": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "e.g., A, B, C, D, E"},
            ),
            "max_zwift_category": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "e.g., A, B, C, D, E"},
            ),
            "min_zwift_racing_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "max_zwift_racing_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
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
        }

    def __init__(
        self,
        *args,
        event_prefixes: list[str] | None = None,
        gender_options: list[str] | None = None,
        **kwargs,
    ) -> None:
        """Initialize form with Discord channel choices and captain labels.

        Args:
            *args: Positional arguments passed to ModelForm.
            event_prefixes: The parent event's Discord prefixes. When empty, the role field is disabled.
            gender_options: The parent event's allowed squad-gender options. When empty, the gender field is disabled.
            **kwargs: Keyword arguments passed to ModelForm.

        """
        super().__init__(*args, **kwargs)
        self.event_prefixes = list(event_prefixes or [])
        self.gender_options = list(gender_options or [])

        # Populate gender choices from the parent event's options
        gender_choices = [("", "—")] + [(g, g) for g in self.gender_options]
        current_gender = self.initial.get("gender") or ""
        if current_gender and current_gender not in self.gender_options:
            gender_choices.append((current_gender, f"{current_gender} (not in event options)"))
        self.fields["gender"].choices = gender_choices
        if not self.gender_options:
            self.fields["gender"].widget.attrs["disabled"] = True

        # Captains and vice-captains are managed per-member from the squad panel
        # (Set as Captain / Vice Captain), not via this form.

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
            role_choices = _get_role_choices(prefixes=self.event_prefixes)
        else:
            role_choices = [("0", "(none — set event prefixes first)")]
            self.fields["team_discord_role"].widget.attrs["disabled"] = True
        current_role = str(self.initial.get("team_discord_role", 0) or 0)
        role_values = EventForm._flat_choice_values(role_choices)
        if current_role != "0" and current_role not in role_values:
            role_choices.append((current_role, f"Unknown Role ({current_role})"))
        self.fields["team_discord_role"].widget.choices = role_choices
        self.initial["team_discord_role"] = current_role

        # Captain role: same filtering rules as team role.
        if self.event_prefixes:
            captain_role_choices = _get_role_choices(prefixes=self.event_prefixes)
        else:
            captain_role_choices = [("0", "(none — set event prefixes first)")]
            self.fields["discord_captain_role"].widget.attrs["disabled"] = True
        current_captain_role = str(self.initial.get("discord_captain_role", 0) or 0)
        captain_role_values = EventForm._flat_choice_values(captain_role_choices)
        if current_captain_role != "0" and current_captain_role not in captain_role_values:
            captain_role_choices.append((current_captain_role, f"Unknown Role ({current_captain_role})"))
        self.fields["discord_captain_role"].widget.choices = captain_role_choices
        self.initial["discord_captain_role"] = current_captain_role

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

        if role_id and role_id != 0 and not self.event_prefixes:
            raise forms.ValidationError("Set at least one event prefix before assigning a role.")

        if role_id and role_id != 0 and self.event_prefixes:
            role = DiscordRole.objects.filter(role_id=str(role_id)).first()
            if role and not any(role.name.startswith(p) for p in self.event_prefixes):
                raise forms.ValidationError(
                    f'Role "@{role.name}" must start with one of: {", ".join(self.event_prefixes)}.'
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

        if role_id and role_id != 0 and not self.event_prefixes:
            raise forms.ValidationError("Set at least one event prefix before assigning a role.")

        if role_id and role_id != 0 and self.event_prefixes:
            role = DiscordRole.objects.filter(role_id=str(role_id)).first()
            if role and not any(role.name.startswith(p) for p in self.event_prefixes):
                raise forms.ValidationError(
                    f'Role "@{role.name}" must start with one of: {", ".join(self.event_prefixes)}.'
                )

        return role_id

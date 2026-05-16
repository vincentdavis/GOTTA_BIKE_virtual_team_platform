"""Background tasks for events app."""

from __future__ import annotations

import logfire
from django.tasks import task  # ty:ignore[unresolved-import]

from apps.accounts.discord_service import send_discord_channel_message
from apps.events.models import EventSignup
from apps.team.services import ZP_DIV_TO_CATEGORY
from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftracing.models import ZRRider


def _format_signup_message(signup: EventSignup, profile_url: str | None) -> str:
    """Build the Discord channel message for a new event signup.

    Args:
        signup: The freshly created EventSignup.
        profile_url: Absolute URL to the rider's public profile in the app.

    Returns:
        Multi-line Markdown string ready for Discord.

    """
    user = signup.user
    event = signup.event
    display_name = user.get_full_name() or user.discord_nickname or user.discord_username or user.username

    lines: list[str] = [f"📝 **New signup — {event.title}**"]

    # Rider line; link the display name to their app profile when we have a URL.
    if profile_url:
        lines.append(f"Rider: [{display_name}]({profile_url})")
    else:
        lines.append(f"Rider: {display_name}")

    # ZP category — read the user's div/divw via their zwid
    zp_cat = ""
    if user.zwid:
        zp = ZPTeamRiders.objects.filter(zwid=user.zwid).first()
        if zp:
            zp_cat = ZP_DIV_TO_CATEGORY.get(zp.divw if user.gender == "female" else zp.div, "")
    if zp_cat:
        lines.append(f"ZP: **{zp_cat}**")

    # ZR category + rating
    if user.zwid:
        zr = ZRRider.objects.filter(zwid=user.zwid).first()
        if zr:
            cat = (getattr(zr, "race_current_category", "") or "").strip()
            rating = getattr(zr, "race_current_rating", None)
            if cat or rating is not None:
                rating_part = f" ({rating})" if rating is not None else ""
                lines.append(f"ZR: **{cat or '?'}**{rating_part}")

    # Conditional fields, only when the event captures them
    if event.timezone_required and signup.signup_timezone:
        lines.append("Timezone: " + ", ".join(signup.signup_timezone))
    if event.squad_gender_required and signup.signup_squad_gender:
        lines.append("Squad Gender: " + ", ".join(signup.signup_squad_gender))

    return "\n".join(lines)


def post_signup_notification(signup_id: int, profile_url: str | None = None) -> dict:
    """Post the signup notification synchronously.

    No-op if the event has no ``signup_notification_channel_id`` configured.
    Failures are logged and swallowed — they must not affect the signup flow.

    Args:
        signup_id: PK of the freshly created EventSignup.
        profile_url: Absolute URL to the rider's public profile in this app.

    Returns:
        Dict describing the outcome (status: posted / skipped / error).

    """
    with logfire.span("notify_signup_to_channel", signup_id=signup_id):
        try:
            signup = EventSignup.objects.select_related("user", "event").get(pk=signup_id)
        except EventSignup.DoesNotExist:
            logfire.error("Signup not found", signup_id=signup_id)
            return {"status": "error", "reason": "signup_not_found"}

        channel_id = signup.event.signup_notification_channel_id
        if not channel_id:
            logfire.debug("Event has no signup notification channel; skipping")
            return {"status": "skipped", "reason": "channel_not_configured"}

        message = _format_signup_message(signup, profile_url)
        ok = send_discord_channel_message(channel_id, message)
        if not ok:
            logfire.warning(
                "Signup notification not sent",
                signup_id=signup_id,
                event_id=signup.event_id,
                channel_id=channel_id,
            )
            return {"status": "error", "reason": "send_failed", "channel_id": str(channel_id)}

        logfire.info(
            "Signup notification posted",
            signup_id=signup_id,
            event_id=signup.event_id,
            channel_id=channel_id,
        )
        return {"status": "posted", "channel_id": str(channel_id)}


@task
def notify_signup_to_channel(signup_id: int, profile_url: str | None = None) -> dict:
    """Background-task wrapper around ``post_signup_notification``.

    Args:
        signup_id: PK of the freshly created EventSignup.
        profile_url: Absolute URL to the rider's public profile in this app.

    Returns:
        See :func:`post_signup_notification`.

    """
    return post_signup_notification(signup_id=signup_id, profile_url=profile_url)


# Convenience for code that wants to enqueue with the right URL builder.
def enqueue_signup_notification(signup: EventSignup, *, request=None) -> None:
    """Enqueue ``notify_signup_to_channel`` for a freshly created signup.

    Args:
        signup: The newly created EventSignup.
        request: Optional HttpRequest; used to build an absolute profile URL.

    """
    if not signup.event.signup_notification_channel_id:
        return  # nothing to do
    profile_url: str | None = None
    if request is not None:
        from django.urls import reverse

        try:
            path = reverse("accounts:public_profile", kwargs={"user_id": signup.user_id})
            profile_url = request.build_absolute_uri(path)
        except Exception:
            profile_url = None
    notify_signup_to_channel.enqueue(signup_id=signup.pk, profile_url=profile_url)

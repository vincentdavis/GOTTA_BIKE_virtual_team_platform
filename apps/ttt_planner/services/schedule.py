"""The pull schedule as text to paste into a Discord message.

Discord shows a code block in a monospace font, so a table padded with spaces lines up
there, on a phone too. Everything, the plan's name included, goes inside the block, so
nothing a rider or a captain typed can turn into Discord formatting; a backtick, which
would end the block, becomes an apostrophe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.ttt_planner.models import TttPlan
    from apps.ttt_planner.services.compute import PlanResult

# Longest rider name kept, so a long one does not push the table off a phone screen.
NAME_WIDTH = 14
FENCE = "```"


def ordinal(number: int) -> str:
    """Return a wheel position as a word: 2nd, 3rd, 4th, 11th, 21st.

    Args:
        number: The position.

    Returns:
        The number with its suffix.

    """
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _plain(text: str) -> str:
    return " ".join((text or "").replace("`", "'").split())


def discord_text(plan: TttPlan, result: PlanResult) -> str:
    """Lay out the pull schedule as a code block for Discord.

    One row per rider in pull order: their pull length, the pull's W/kg and watts, then
    the watts to hold the speed sitting in at each wheel from the 2nd to the last.

    Args:
        plan: The plan (for its name and course).
        result: The plan's computed results.

    Returns:
        The text, fences included.

    """
    course = plan.course_name or (plan.route.name if plan.route else "")
    title = " · ".join(part for part in (_plain(plan.name) or "TTT plan", _plain(course)) if part)
    head = ["Rider", "Pull", "W/kg", "W", *(ordinal(position) for position in result.wheel_positions)]
    rows = []
    for rr in result.riders:
        if rr.rider.zero_pull or not rr.rider.pull_duration_s:
            pull = ["no pull", "-", "-"]
        else:
            pull = [f"{rr.rider.pull_duration_s}s", f"{rr.pull_wkg:.1f}", str(rr.pull_power_w)]
        rows.append([_plain(rr.rider.name)[:NAME_WIDTH], *pull, *(str(watts) for watts in rr.wheel_powers)])
    widths = [max(len(row[col]) for row in (head, *rows)) for col in range(len(head))]

    def line(cells: list[str]) -> str:
        return "  ".join(
            cell.ljust(width) if col == 0 else cell.rjust(width)
            for col, (cell, width) in enumerate(zip(cells, widths, strict=True))
        ).rstrip()

    speed = f"{result.target_speed_kph:.1f} kph on the flat"
    text = [FENCE, title, speed, "", line(head), *(line(row) for row in rows)]
    if result.wheel_positions:
        first = result.wheel_positions[0]
        named = ordinal(first) + (f", {ordinal(first + 1)}…" if len(result.wheel_positions) > 1 else "")
        text += ["", f"{named}: watts sitting in at that wheel"]
    text.append(FENCE)
    return "\n".join(text)

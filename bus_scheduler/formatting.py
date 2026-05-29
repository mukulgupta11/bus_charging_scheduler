from __future__ import annotations


def parse_clock(value: str) -> int:
    """Parse HH:MM into minutes after midnight."""
    hour, minute = value.strip().split(":")
    return int(hour) * 60 + int(minute)


def format_clock(minutes: float | int | None) -> str:
    if minutes is None:
        return ""
    rounded = int(round(minutes))
    days, rem = divmod(rounded, 24 * 60)
    text = f"{rem // 60:02d}:{rem % 60:02d}"
    if days:
        text += f" (+{days}d)"
    return text


def format_duration(minutes: float | int | None) -> str:
    if minutes is None:
        return ""
    rounded = int(round(minutes))
    sign = "-" if rounded < 0 else ""
    rounded = abs(rounded)
    hours, mins = divmod(rounded, 60)
    if hours and mins:
        return f"{sign}{hours}h {mins}m"
    if hours:
        return f"{sign}{hours}h"
    return f"{sign}{mins}m"


def title_case_id(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()

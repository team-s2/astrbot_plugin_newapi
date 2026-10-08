"""Numeric helpers and window parsing for new-api subscription payloads."""

from __future__ import annotations

from math import isfinite
from typing import Any


def compact_token_count(value: Any) -> str:
    """Format a token count compactly for a diagram label."""
    try:
        count = max(float(value or 0), 0)
    except (OverflowError, TypeError, ValueError):
        count = 0
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if count >= divisor:
            scaled = count / divisor
            digits = 0 if scaled >= 100 else 1
            return f"{scaled:.{digits}f}{suffix} tokens"
    return f"{count:.0f} tokens"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number >= 0 else None


def _codex_windows(
    source: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rate_limit = source.get("rate_limit")
    if not isinstance(rate_limit, dict):
        rate_limit = {}
    primary = rate_limit.get("primary_window") or source.get("primary_window")
    secondary = rate_limit.get("secondary_window") or source.get("secondary_window")
    windows = [window for window in (primary, secondary) if isinstance(window, dict)]
    five_hour = None
    weekly = None
    for window in windows:
        seconds = _number(window.get("limit_window_seconds"))
        if seconds is None:
            continue
        if seconds >= 24 * 3600 and weekly is None:
            weekly = window
        elif seconds < 24 * 3600 and five_hour is None:
            five_hour = window

    plan_type = str(
        source.get("plan_type") or rate_limit.get("plan_type") or ""
    ).lower()
    if plan_type == "free":
        return None, weekly or (windows[0] if windows else None)
    if five_hour is None and weekly is None:
        return (
            primary if isinstance(primary, dict) else None,
            secondary if isinstance(secondary, dict) else None,
        )
    if five_hour is None:
        five_hour = next((window for window in windows if window is not weekly), None)
    if weekly is None:
        weekly = next((window for window in windows if window is not five_hour), None)
    return five_hour, weekly

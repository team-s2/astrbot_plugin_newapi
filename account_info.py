"""Format subscription-channel account information returned by new-api."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any


def format_token_count(value: Any) -> str:
    """Format a token or quota count without presenting it as currency."""
    try:
        count = round(float(value or 0))
    except (OverflowError, TypeError, ValueError):
        count = 0
    return f"{max(count, 0):,} tokens"


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


def _duration(seconds: Any) -> str | None:
    value = _number(seconds)
    if value is None or value <= 0:
        return None
    total = round(value)
    days, remainder = divmod(total, 24 * 3600)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小时")
    if minutes or not parts:
        parts.append(f"{minutes} 分钟")
    return " ".join(parts[:2])


def _reset_text(window: dict[str, Any], milliseconds: bool = False) -> str:
    remaining = _duration(window.get("reset_after_seconds"))
    if remaining:
        return f"{remaining}后重置"
    reset_at = _number(window.get("next_reset_time" if milliseconds else "reset_at"))
    if reset_at is None or reset_at <= 0:
        return "重置时间未知"
    if milliseconds:
        reset_at /= 1000
    return (
        datetime.fromtimestamp(reset_at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        + " 重置"
    )


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


def _codex_status(rate_limit: Any) -> str:
    if not isinstance(rate_limit, dict) or not rate_limit:
        return "状态未知"
    if rate_limit.get("allowed") and not rate_limit.get("limit_reached"):
        return "可用"
    return "受限"


def _codex_window_line(label: str, window: dict[str, Any]) -> str:
    used = _number(window.get("used_percent")) or 0
    remaining = max(100 - min(used, 100), 0)
    return f"{label}：剩余 {remaining:.1f}% · {_reset_text(window)}"


def format_codex_account(
    usage: dict[str, Any], credits: dict[str, Any] | None = None
) -> list[str]:
    """Render the useful fields from the Codex Account Info response."""
    rate_limit = usage.get("rate_limit")
    if not isinstance(rate_limit, dict):
        rate_limit = {}
    plan = usage.get("plan_type") or rate_limit.get("plan_type") or "未知"
    email = usage.get("email") or "未知账户"
    available_credits = None
    if isinstance(credits, dict):
        available_credits = credits.get("available_count")
    embedded_credits = usage.get("rate_limit_reset_credits")
    if available_credits is None and isinstance(embedded_credits, dict):
        available_credits = embedded_credits.get("available_count")

    summary = f"账户 {email} · 套餐 {plan} · {_codex_status(rate_limit)}"
    if available_credits is not None:
        summary += f" · 可用重置 {int(_number(available_credits) or 0)}"
    lines = [summary]
    five_hour, weekly = _codex_windows(usage)
    if five_hour is not None:
        lines.append(_codex_window_line("5 小时", five_hour))
    if weekly is not None:
        lines.append(_codex_window_line("每周", weekly))

    additional_limits = usage.get("additional_rate_limits")
    if isinstance(additional_limits, list):
        for item in additional_limits:
            if not isinstance(item, dict) or not item:
                continue
            item_rate_limit = item.get("rate_limit")
            name = (
                item.get("limit_name")
                or item.get("metered_feature")
                or "附加限额"
            )
            lines.append(f"附加限额 {name} · {_codex_status(item_rate_limit)}")
            five_hour, weekly = _codex_windows(item)
            if five_hour is not None:
                lines.append("  " + _codex_window_line("5 小时", five_hour))
            if weekly is not None:
                lines.append("  " + _codex_window_line("每周", weekly))
    return lines


def _zhipu_limit_line(label: str, limit: Any, unit: str) -> str | None:
    if not isinstance(limit, dict):
        return None
    total = _number(limit.get("usage"))
    remaining = _number(limit.get("remaining"))
    current = _number(limit.get("current_value"))
    if remaining is None and total is not None and current is not None:
        remaining = max(total - current, 0)

    if remaining is not None and total is not None:
        usage_text = f"剩余 {remaining:,.0f} / {total:,.0f} {unit}"
    else:
        percentage = min(_number(limit.get("percentage")) or 0, 100)
        usage_text = f"剩余 {100 - percentage:.1f}%"
    return f"{label}：{usage_text} · {_reset_text(limit, milliseconds=True)}"


def format_zhipu_account(usage: dict[str, Any]) -> list[str]:
    """Render the useful fields from a Zhipu Coding Plan Account Info response."""
    lines = [f"套餐 {str(usage.get('level') or '未知').upper()}"]
    for label, key, unit in (
        ("5 小时", "five_hour", "tokens"),
        ("每周", "weekly", "tokens"),
        ("MCP 月度", "mcp_monthly", "次"),
    ):
        line = _zhipu_limit_line(label, usage.get(key), unit)
        if line:
            lines.append(line)
    return lines

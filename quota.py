"""Read and normalize subscription quotas without performing any resets."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .account_info import _codex_windows
from .client import NewApiClient, NewApiError

WEEK = 7 * 86400
FIVE_HOURS = 5 * 3600


@dataclass(frozen=True)
class QuotaWindow:
    label: str
    used_percent: float | None
    duration: float
    reset_at: float | None
    detail: str = ""

    @property
    def start_at(self) -> float | None:
        return self.reset_at - self.duration if self.reset_at is not None else None


@dataclass(frozen=True)
class ChannelQuota:
    channel_id: int
    name: str
    provider: str
    status: str
    plan: str = ""
    weekly: QuotaWindow | None = None
    five_hour: QuotaWindow | None = None
    reset_count: int | None = None
    reset_note: str = ""
    reset_failed: bool = False
    issue: str = ""
    unsupported: bool = False
    limit_note: str = ""


def account_kind(channel: dict[str, Any]) -> str | None:
    channel_type = int(channel.get("type") or 0)
    if channel_type == 57:
        return "codex"
    if channel_type == 100 or (
        channel_type == 26
        and str(channel.get("base_url") or "").strip() == "glm-coding-plan"
    ):
        return "zhipu"
    return None


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) and result >= 0 else None


def issue_text(error: object) -> str:
    """Keep useful provider errors, excluding credentials and response HTML."""
    text = " ".join(str(error).split())
    if re.search(r"\b401\b", text):
        return "HTTP 401 · 认证失效，请重新登录或更新渠道凭据"
    if re.search(r"\b403\b", text):
        return "HTTP 403 · 上游拒绝访问，请检查账户权限"
    if "OAuth access token expired" in text:
        return "OAuth 凭据已过期，请重新登录智谱账户"
    if "multi-key" in text:
        return "暂不支持多密钥渠道的订阅额度查询"
    if "cannot connect" in text or "timeout" in text.lower():
        return "连接失败或请求超时，请稍后重试"
    text = re.sub(r"https?://\S+", "[上游地址]", text)
    text = re.sub(
        r"(?i)(bearer\s+|(?:token|password|api_key)[\s:=\"']+)[^\s,}]+",
        r"\1[已隐藏]",
        text,
    )
    if "<html" in text.lower() or "<!doctype" in text.lower():
        return "上游返回了网页，无法读取额度数据"
    return text[:180] or "额度读取失败"


def parse_window(
    raw: Any, label: str, duration: int, now: float, *, zhipu: bool = False
) -> QuotaWindow | None:
    if not isinstance(raw, dict) or not raw:
        return None
    used = number(raw.get("percentage" if zhipu else "used_percent"))
    detail = ""
    if zhipu:
        total = number(raw.get("usage"))
        current = number(raw.get("current_value"))
        remaining = number(raw.get("remaining"))
        if current is None and total is not None and remaining is not None:
            current = max(0, total - remaining)
        if total is not None and current is not None:
            detail = f"已用 {current:,.0f} / {total:,.0f} tokens"
            if total > 0:
                used = current / total * 100
        reset = number(raw.get("next_reset_time"))
        reset = reset / 1000 if reset else None
    else:
        duration = number(raw.get("limit_window_seconds")) or duration
        reset = number(raw.get("reset_at"))
        if not reset:
            after = number(raw.get("reset_after_seconds"))
            reset = now + after if after is not None else None
    # datetime and the renderer must not see out-of-range upstream timestamps.
    if reset is not None and not 0 < reset < 253402214400:
        reset = None
    if duration > 366 * 86400:
        duration = 7 * 86400
    return QuotaWindow(label, used, duration, reset, detail)


def normalize_quota(
    channel: dict, usage: object, credits: object, now: float
) -> ChannelQuota:
    kind = account_kind(channel)
    common = {
        "channel_id": int(channel["id"]),
        "name": str(channel.get("name") or "未命名"),
        "provider": {"codex": "Codex", "zhipu": "智谱 Coding Plan"}.get(
            kind, f"类型 {channel.get('type', '?')}"
        ),
        "status": {1: "启用", 2: "手动禁用", 3: "自动禁用"}.get(
            channel.get("status"), "状态未知"
        ),
    }
    if kind is None:
        return ChannelQuota(
            **common, unsupported=True, issue="暂不支持此渠道类型的订阅额度查询"
        )
    count = None
    reset_note = "上游未提供主动重置次数" if kind == "zhipu" else "未返回重置次数"
    if kind == "zhipu":
        reset = usage.get("reset") if isinstance(usage, dict) else None
        if isinstance(reset, dict):
            five_hour = reset.get("available_five_hour_resets")
            week = reset.get("available_week_resets")
            if isinstance(five_hour, list) and isinstance(week, list):
                count = len(five_hour) + len(week)
                reset_note = ""
        elif isinstance(usage, dict):
            reason = str(
                usage.get("reset_unavailable_reason") or ""
            ).strip()
            reset_note = "重置卡查询失败：" + (reason or "未知原因")
    if kind == "codex":
        embedded = (
            usage.get("rate_limit_reset_credits") if isinstance(usage, dict) else None
        )
        embedded = embedded if isinstance(embedded, dict) else {}
        credit_data = credits if isinstance(credits, dict) else {}
        count_value = number(credit_data.get("available_count"))
        if count_value is None:
            count_value = number(embedded.get("available_count"))
        count = int(count_value) if count_value is not None else None
        if isinstance(credits, Exception):
            reset_note = (
                "使用用量接口返回的次数；" if count is not None else ""
            ) + issue_text(credits)
        elif count is not None:
            reset_note = ""
    common.update(
        reset_count=count,
        reset_note=reset_note,
        reset_failed=isinstance(credits, Exception),
    )
    if isinstance(usage, Exception):
        return ChannelQuota(**common, issue=issue_text(usage))
    if not isinstance(usage, dict):
        return ChannelQuota(**common, issue="接口未返回有效额度数据")
    if kind == "codex":
        # The main account pool only; additional_rate_limits (including Spark)
        # are deliberately excluded from this subscription overview.
        five_raw, week_raw = _codex_windows(usage)
        plan = str(usage.get("plan_type") or "")
        rate_limit = usage.get("rate_limit") or {}
        limited = isinstance(rate_limit, dict) and (
            rate_limit.get("limit_reached") is True
            or rate_limit.get("allowed") is False
        )
    else:
        five_raw, week_raw = usage.get("five_hour"), usage.get("weekly")
        plan = str(usage.get("level") or "")
        limited = False
    weekly = parse_window(week_raw, "周额度", WEEK, now, zhipu=kind == "zhipu")
    five_hour = parse_window(five_raw, "5 小时", FIVE_HOURS, now, zhipu=kind == "zhipu")
    return ChannelQuota(
        **common,
        plan=plan.upper(),
        weekly=weekly,
        five_hour=five_hour,
        limit_note="上游当前限流" if limited else "",
        issue="上游未返回周额度或 5 小时额度"
        if weekly is None and five_hour is None
        else "",
    )


async def collect_quotas(client: NewApiClient) -> list[ChannelQuota]:
    """Fetch all channels with at most four concurrent upstream requests."""
    channels = await client.all_channels()
    semaphore = asyncio.Semaphore(4)

    async def request(method, channel_id):
        async with semaphore:
            try:
                return await method(channel_id)
            except NewApiError as error:
                return error

    async def fetch(channel):
        kind = account_kind(channel)
        channel_id = int(channel["id"])
        usage = credits = None
        if kind == "codex":
            usage, credits = await asyncio.gather(
                request(client.codex_usage, channel_id),
                request(client.codex_reset_credits, channel_id),
            )
        elif kind == "zhipu":
            usage = await request(client.zhipu_coding_plan_usage, channel_id)
        return normalize_quota(channel, usage, credits, time.time())

    rows = await asyncio.gather(*(fetch(channel) for channel in channels))
    return sorted(rows, key=lambda row: (row.unsupported, bool(row.issue)))

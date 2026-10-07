"""Fetch subscription Account Info for a channel and format it as text lines."""

from __future__ import annotations

import asyncio
from typing import Any

from ..api.client import NewApiClient, NewApiError
from .account_info import (
    format_codex_account,
    format_grok_account,
    format_zhipu_account,
)
from .quota import account_kind

AccountInfoResult = tuple[str, object, object | None]


async def fetch_account_info(
    client: NewApiClient,
    channel: dict[str, Any],
    include_credits: bool = False,
) -> AccountInfoResult | None:
    """Query the upstream Account Info of a subscription channel.

    Errors are returned in place of the data so callers can report them inline.
    """
    channel_id = int(channel.get("id") or 0)
    kind = account_kind(channel)
    if kind == "codex":
        if include_credits:
            usage, credits = await asyncio.gather(
                client.codex_usage(channel_id),
                client.codex_reset_credits(channel_id),
                return_exceptions=True,
            )
        else:
            try:
                usage = await client.codex_usage(channel_id)
            except NewApiError as error:
                usage = error
            credits = None
        return kind, usage, credits
    if kind == "zhipu":
        try:
            usage = await client.zhipu_coding_plan_usage(channel_id)
        except NewApiError as error:
            usage = error
        return kind, usage, None
    if kind == "grok":
        try:
            usage = await client.grok_usage(channel_id)
        except NewApiError as error:
            usage = error
        return kind, usage, None
    return None


def account_info_lines(result: AccountInfoResult | None) -> list[str]:
    """Format a :func:`fetch_account_info` result as text lines."""
    if result is None:
        return []
    kind, usage, credits = result
    if isinstance(usage, Exception):
        return [f"Account Info 查询失败：{usage}"]
    if not isinstance(usage, dict):
        return ["Account Info 查询失败：new-api 返回了无效数据"]
    if kind == "zhipu":
        return format_zhipu_account(usage)
    if kind == "grok":
        return format_grok_account(usage)

    credit_data = credits if isinstance(credits, dict) else None
    lines = format_codex_account(usage, credit_data)
    if isinstance(credits, Exception):
        lines.append(f"重置次数查询失败：{credits}")
    return lines

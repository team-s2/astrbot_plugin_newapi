"""``/newapi channel``: list channels or show one channel's details."""

from __future__ import annotations

import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..api.client import NewApiError
from ..config import NewApiBindingError, NewApiInstance
from ..core.account_fetch import account_info_lines, fetch_account_info
from ..core.account_info import format_token_count
from ..core.channels import CHANNEL_STATUSES, channel_has_group, channel_type_name
from ..core.quota import account_kind
from .base import CommandBase


class ChannelCommands(CommandBase):
    """Implementation of ``/newapi channel``."""

    async def _channel(self, event: AstrMessageEvent, channel: str):
        """List all channels, or show details for one name or ID."""
        try:
            instance = self._instance_for(event)
        except NewApiBindingError as error:
            yield event.plain_result(str(error))
            return

        query, allgroup = self._without_allgroup((channel or "").strip())
        group = self._group_for(event, instance, allgroup)
        if query:
            async for result in self._show_channel(event, instance, query, group):
                yield result
        else:
            async for result in self._list_channels(event, instance, group):
                yield result

    async def _list_channels(
        self,
        event: AstrMessageEvent,
        instance: NewApiInstance,
        group: str | None = None,
    ):
        """List all channels with usage information in a list format."""
        client = instance.client
        try:
            channels_result, quota_per_unit_result = await asyncio.gather(
                client.list_channels(),
                client.quota_per_unit(),
                return_exceptions=True,
            )
            if isinstance(channels_result, Exception):
                raise channels_result
            channels, total = channels_result
            if group:
                channels = [
                    ch
                    for ch in await client.all_channels()
                    if channel_has_group(ch, group)
                ]
                total = len(channels)
            quota_per_unit = (
                quota_per_unit_result
                if isinstance(quota_per_unit_result, float)
                else None
            )
            if isinstance(quota_per_unit_result, Exception):
                logger.warning(
                    "Failed to query new-api quota_per_unit: %s",
                    quota_per_unit_result,
                )
            limit = max(1, min(int(self.config.get("channel_list_limit", 30)), 100))
            shown = channels[:limit]

            account_channels = [ch for ch in shown if account_kind(ch)]
            account_results = await asyncio.gather(
                *(fetch_account_info(client, ch) for ch in account_channels)
            )
            account_info = {
                int(ch.get("id") or 0): result
                for ch, result in zip(account_channels, account_results, strict=True)
            }

            lines = [
                f"【{instance.name}】new-api 渠道（显示 {len(shown)}/{total}）",
                "",
            ]
            for ch in shown:
                type_name = channel_type_name(ch)
                status = CHANNEL_STATUSES.get(int(ch.get("status", 0)), "未知")
                name = str(ch.get("name") or "未命名")
                group = str(ch.get("group") or "default")
                used_quota = ch.get("used_quota") or 0

                lines.append(f"#{ch.get('id')} {name}")
                lines.append(f"  {type_name} · {status} · {group}")
                quota_line = f"  计费额度：已用 {format_token_count(used_quota)}"
                if quota_per_unit is not None:
                    balance_quota = float(ch.get("balance") or 0) * quota_per_unit
                    quota_line += f" · 余额 {format_token_count(balance_quota)}"
                lines.append(quota_line)
                result = account_info.get(int(ch.get("id") or 0))
                if result is not None:
                    info_lines = account_info_lines(result)
                    lines.extend(f"  {line}" for line in info_lines)
                if ch is not shown[-1]:
                    lines.append("")
            if total > limit:
                lines.append("")
                lines.append("可用 /newapi channel <名称或 ID> 查看具体渠道。")
            yield event.plain_result("\n".join(lines))
        except NewApiError as error:
            logger.warning("Failed to list new-api channels: %s", error)
            yield event.plain_result(f"查询 new-api 失败：{error}")

    async def _show_channel(
        self,
        event: AstrMessageEvent,
        instance: NewApiInstance,
        query: str,
        group: str | None = None,
    ):
        """Show one channel and subscription Account Info when available."""
        client = instance.client
        try:
            found = await client.find_channel(query)
            channel_id = int(found.get("id", 0))
            if not channel_id:
                raise NewApiError("new-api 返回了无效的渠道 ID")
            found = await client.get(f"/api/channel/{channel_id}")
            if not isinstance(found, dict):
                raise NewApiError(f"未找到渠道：{query}")
            if not channel_has_group(found, group):
                raise NewApiError(f"未找到渠道：{query}")

            quota_per_unit_result, account_info = await asyncio.gather(
                client.quota_per_unit(),
                fetch_account_info(client, found, include_credits=True),
                return_exceptions=True,
            )
            status = int(found.get("status", 0))
            models = [
                item for item in str(found.get("models") or "").split(",") if item
            ]
            lines = [
                f"【{instance.name}】渠道 #{channel_id} · {found.get('name') or '未命名'}",
                f"类型：{channel_type_name(found)}",
                f"状态：{CHANNEL_STATUSES.get(status, '未知')}",
                f"分组：{found.get('group') or 'default'}",
                f"模型：{len(models)} 个"
                + (f"（{', '.join(models[:8])}）" if models else ""),
                f"已用计费额度：{format_token_count(found.get('used_quota'))}",
            ]
            if isinstance(quota_per_unit_result, float):
                balance_quota = float(found.get("balance") or 0) * quota_per_unit_result
                balance_text = format_token_count(balance_quota)
                lines.append(f"剩余计费额度：{balance_text}")
            elif isinstance(quota_per_unit_result, Exception):
                logger.warning(
                    "Failed to query new-api quota_per_unit: %s",
                    quota_per_unit_result,
                )
            lines.append(f"响应时间：{int(found.get('response_time') or 0)} ms")
            if found.get("base_url"):
                lines.append(f"Base URL：{found['base_url']}")
            if found.get("tag"):
                lines.append(f"标签：{found['tag']}")
            if found.get("remark"):
                lines.append(f"备注：{found['remark']}")

            if not isinstance(account_info, Exception) and account_info is not None:
                lines.append("")
                lines.append("Account Info")
                lines.extend(account_info_lines(account_info))
            yield event.plain_result("\n".join(lines))
        except NewApiError as error:
            logger.warning("Failed to show new-api channel: %s", error)
            yield event.plain_result(f"查询 new-api 失败：{error}")

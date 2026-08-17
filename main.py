"""AstrBot plugin commands for querying new-api."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .account_info import (
    format_codex_account,
    format_token_count,
    format_zhipu_account,
)
from .client import NewApiClient, NewApiError
from .flow_renderer import FlowStage, OverflowMode, render_sankey

CHANNEL_TYPES = {
    1: "OpenAI",
    3: "Azure",
    4: "Ollama",
    8: "Custom",
    14: "Anthropic",
    17: "Ali",
    20: "OpenRouter",
    24: "Gemini",
    26: "Zhipu V4",
    33: "AWS",
    40: "SiliconFlow",
    41: "Vertex AI",
    43: "DeepSeek",
    48: "xAI",
    57: "ChatGPT Subscription (Codex)",
    58: "Advanced Custom",
}
CHANNEL_STATUSES = {0: "未知", 1: "启用", 2: "手动禁用", 3: "自动禁用"}


@star.register(
    "astrbot_plugin_newapi",
    "team-s2",
    "查询 new-api 渠道信息并绘制 Dashboard 流图",
    "1.0.0",
)
class NewApiPlugin(star.Star):
    """Expose read-only new-api administration commands to AstrBot admins."""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        """Initialize the plugin from AstrBot configuration.

        Args:
            context: AstrBot plugin context.
            config: Plugin configuration from the WebUI.
        """
        super().__init__(context)
        self.config = config
        self.client = NewApiClient(
            str(config.get("base_url", "")),
            str(config.get("access_token", "")),
            int(config.get("user_id", 0)),
            float(config.get("request_timeout", 20)),
        )

    async def terminate(self) -> None:
        """Release the HTTP session when AstrBot unloads the plugin."""
        await self.client.close()

    @filter.command_group("newapi")
    def newapi(self) -> None:
        """Group new-api administration commands."""

    @newapi.command("channel")
    async def channel(self, event: AstrMessageEvent, channel: GreedyStr = ""):
        """List all channels or show details for one.

        Without arguments, lists channels with usage info.
        With a channel name or ID, shows that channel's details.

        Args:
            event: Incoming AstrBot message event.
            channel: Optional channel name or numeric ID.
        """
        query = (channel or "").strip()
        if query:
            async for result in self._show_channel(event, query):
                yield result
        else:
            async for result in self._list_channels(event):
                yield result

    async def _list_channels(self, event: AstrMessageEvent):
        """List all channels with usage information in a list format."""
        try:
            channels_result, quota_per_unit_result = await asyncio.gather(
                self.client.list_channels(),
                self.client.quota_per_unit(),
                return_exceptions=True,
            )
            if isinstance(channels_result, Exception):
                raise channels_result
            channels, total = channels_result
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

            account_channels = [ch for ch in shown if self._account_info_kind(ch)]
            account_results = await asyncio.gather(
                *(self._fetch_account_info(ch) for ch in account_channels)
            )
            account_info = {
                int(ch.get("id") or 0): result
                for ch, result in zip(account_channels, account_results, strict=True)
            }

            lines = [f"new-api 渠道（显示 {len(shown)}/{total}）", ""]
            for ch in shown:
                type_name = self._channel_type_name(ch)
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
                    info_lines = self._account_info_lines(result)
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

    async def _show_channel(self, event: AstrMessageEvent, query: str):
        """Show one channel and subscription Account Info when available."""
        try:
            found = await self.client.find_channel(query)
            channel_id = int(found.get("id", 0))
            if not channel_id:
                raise NewApiError("new-api 返回了无效的渠道 ID")
            found = await self.client.get(f"/api/channel/{channel_id}")
            if not isinstance(found, dict):
                raise NewApiError(f"未找到渠道：{query}")

            quota_per_unit_result, account_info = await asyncio.gather(
                self.client.quota_per_unit(),
                self._fetch_account_info(found, include_credits=True),
                return_exceptions=True,
            )
            status = int(found.get("status", 0))
            models = [
                item for item in str(found.get("models") or "").split(",") if item
            ]
            lines = [
                f"渠道 #{channel_id} · {found.get('name') or '未命名'}",
                f"类型：{self._channel_type_name(found)}",
                f"状态：{CHANNEL_STATUSES.get(status, '未知')}",
                f"分组：{found.get('group') or 'default'}",
                f"模型：{len(models)} 个"
                + (f"（{', '.join(models[:8])}）" if models else ""),
                f"已用计费额度：{format_token_count(found.get('used_quota'))}",
            ]
            if isinstance(quota_per_unit_result, float):
                balance_quota = (
                    float(found.get("balance") or 0) * quota_per_unit_result
                )
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
                lines.extend(self._account_info_lines(account_info))
            yield event.plain_result("\n".join(lines))
        except NewApiError as error:
            logger.warning("Failed to show new-api channel: %s", error)
            yield event.plain_result(f"查询 new-api 失败：{error}")

    @newapi.command("flow")
    async def flow(self, event: AstrMessageEvent):
        """Render and send the configured new-api Dashboard flow.

        Args:
            event: Incoming AstrBot message event.
        """
        output: Path | None = None
        try:
            raw_stages = self.config.get("flow_stages", ["token", "model", "channel"])
            valid_stages = {"user", "node", "token", "group", "model", "channel"}
            stages = [
                cast(FlowStage, stage) for stage in raw_stages if stage in valid_stages
            ]
            if len(stages) < 2:
                raise NewApiError("流图配置至少需要选择两个阶段")
            hours = max(1, min(int(self.config.get("flow_hours", 24)), 24 * 30))
            end_timestamp = int(time.time())
            rows = await self.client.flow(end_timestamp - hours * 3600, end_timestamp)
            if not rows:
                raise NewApiError("所选时间范围内没有流图数据")

            output = Path(get_astrbot_temp_path()) / f"newapi-flow-{uuid4().hex}.png"
            font_value = str(self.config.get("font_path", "")).strip()
            summary = await asyncio.to_thread(
                render_sankey,
                rows,
                output,
                stages,
                max(1, min(int(self.config.get("flow_top_n", 20)), 100)),
                cast(
                    OverflowMode,
                    self.config.get("flow_overflow", "aggregate"),
                ),
                1800,
                1120,
                Path(font_value) if font_value else None,
            )
            logger.info(
                "Rendered new-api flow with %d nodes and %d links",
                summary.node_count,
                summary.link_count,
            )
            yield event.image_result(str(output))
        except (NewApiError, ValueError, OSError) as error:
            logger.warning("Failed to render new-api flow: %s", error)
            yield event.plain_result(f"生成 new-api 流图失败：{error}")

    @staticmethod
    def _account_info_kind(channel: dict[str, Any]) -> str | None:
        channel_type = int(channel.get("type") or 0)
        if channel_type == 57:
            return "codex"
        base_url = str(channel.get("base_url") or "").strip()
        if channel_type == 26 and base_url == "glm-coding-plan":
            return "zhipu"
        return None

    @classmethod
    def _channel_type_name(cls, channel: dict[str, Any]) -> str:
        if cls._account_info_kind(channel) == "zhipu":
            return "Zhipu Coding Plan"
        channel_type = int(channel.get("type") or 0)
        return CHANNEL_TYPES.get(channel_type, f"类型 {channel_type}")

    async def _fetch_account_info(
        self, channel: dict[str, Any], include_credits: bool = False
    ) -> tuple[str, object, object | None] | None:
        channel_id = int(channel.get("id") or 0)
        kind = self._account_info_kind(channel)
        if kind == "codex":
            if include_credits:
                usage, credits = await asyncio.gather(
                    self.client.codex_usage(channel_id),
                    self.client.codex_reset_credits(channel_id),
                    return_exceptions=True,
                )
            else:
                try:
                    usage = await self.client.codex_usage(channel_id)
                except NewApiError as error:
                    usage = error
                credits = None
            return kind, usage, credits
        if kind == "zhipu":
            try:
                usage = await self.client.zhipu_coding_plan_usage(channel_id)
            except NewApiError as error:
                usage = error
            return kind, usage, None
        return None

    @staticmethod
    def _account_info_lines(
        result: tuple[str, object, object | None] | None,
    ) -> list[str]:
        if result is None:
            return []
        kind, usage, credits = result
        if isinstance(usage, Exception):
            return [f"Account Info 查询失败：{usage}"]
        if not isinstance(usage, dict):
            return ["Account Info 查询失败：new-api 返回了无效数据"]
        if kind == "zhipu":
            return format_zhipu_account(usage)

        credit_data = credits if isinstance(credits, dict) else None
        lines = format_codex_account(usage, credit_data)
        if isinstance(credits, Exception):
            lines.append(f"重置次数查询失败：{credits}")
        return lines

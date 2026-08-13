"""AstrBot plugin commands for querying new-api."""

from __future__ import annotations

import asyncio
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from uuid import uuid4

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .client import NewApiClient, NewApiError
from .flow_renderer import FlowMetric, FlowStage, OverflowMode, render_sankey

CHANNEL_TYPES = {
    1: "OpenAI",
    3: "Azure",
    4: "Ollama",
    8: "Custom",
    14: "Anthropic",
    17: "Ali",
    20: "OpenRouter",
    24: "Gemini",
    33: "AWS",
    40: "SiliconFlow",
    41: "Vertex AI",
    43: "DeepSeek",
    48: "xAI",
    57: "ChatGPT Subscription (Codex)",
    58: "Advanced Custom",
}
CHANNEL_STATUSES = {0: "未知", 1: "启用", 2: "手动禁用", 3: "自动禁用"}


def _display_width(text: str) -> int:
    """Return display width counting CJK characters as 2 columns."""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in text
    )


def _pad(text: str, width: int) -> str:
    """Left-justify *text* to *width* display columns."""
    diff = width - _display_width(text)
    return text + " " * max(0, diff)


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
        """List all channels in table format or show details for one.

        Without arguments, lists channels as a table.
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
        """List all channels in a table format."""
        try:
            channels, total = await self.client.list_channels()
            limit = max(1, min(int(self.config.get("channel_list_limit", 30)), 100))
            shown = channels[:limit]

            headers = ["ID", "名称", "类型", "状态", "分组"]
            rows = []
            for ch in shown:
                ch_type = int(ch.get("type", 0))
                type_name = CHANNEL_TYPES.get(ch_type, f"类型 {ch_type}")
                status = CHANNEL_STATUSES.get(int(ch.get("status", 0)), "未知")
                rows.append([
                    f"#{ch.get('id')}",
                    str(ch.get("name") or "未命名"),
                    type_name,
                    status,
                    str(ch.get("group") or "default"),
                ])

            col_widths = []
            for i, header in enumerate(headers):
                max_cell = max(
                    (_display_width(row[i]) for row in rows), default=0
                )
                col_widths.append(max(_display_width(header), max_cell))

            separator = "  "
            lines = [f"new-api 渠道（显示 {len(shown)}/{total}）"]
            lines.append(
                separator.join(_pad(h, col_widths[i]) for i, h in enumerate(headers))
            )
            lines.append(
                "─" * (sum(col_widths) + len(separator) * (len(headers) - 1))
            )
            for row in rows:
                lines.append(
                    separator.join(
                        _pad(row[i], col_widths[i]) for i in range(len(row))
                    )
                )
            if total > limit:
                lines.append("")
                lines.append("可用 /newapi channel <名称或 ID> 查看具体渠道。")
            yield event.plain_result("\n".join(lines))
        except NewApiError as error:
            logger.warning("Failed to list new-api channels: %s", error)
            yield event.plain_result(f"查询 new-api 失败：{error}")

    async def _show_channel(self, event: AstrMessageEvent, query: str):
        """Show one channel and Codex subscription usage when available."""
        try:
            found = await self.client.find_channel(query)
            channel_id = int(found.get("id", 0))
            if not channel_id:
                raise NewApiError("new-api 返回了无效的渠道 ID")
            found = await self.client.get(f"/api/channel/{channel_id}")
            if not isinstance(found, dict):
                raise NewApiError(f"未找到渠道：{query}")

            channel_type = int(found.get("type", 0))
            status = int(found.get("status", 0))
            models = [
                item for item in str(found.get("models") or "").split(",") if item
            ]
            lines = [
                f"渠道 #{channel_id} · {found.get('name') or '未命名'}",
                f"类型：{CHANNEL_TYPES.get(channel_type, f'类型 {channel_type}')}",
                f"状态：{CHANNEL_STATUSES.get(status, '未知')}",
                f"分组：{found.get('group') or 'default'}",
                f"模型：{len(models)} 个"
                + (f"（{', '.join(models[:8])}）" if models else ""),
                f"余额：${float(found.get('balance') or 0):.4f}",
                f"响应时间：{int(found.get('response_time') or 0)} ms",
            ]
            if found.get("base_url"):
                lines.append(f"Base URL：{found['base_url']}")
            if found.get("tag"):
                lines.append(f"标签：{found['tag']}")
            if found.get("remark"):
                lines.append(f"备注：{found['remark']}")

            if channel_type == 57:
                usage_result, credits_result = await asyncio.gather(
                    self.client.codex_usage(channel_id),
                    self.client.codex_reset_credits(channel_id),
                    return_exceptions=True,
                )
                lines.append("")
                lines.append("Codex 用量")
                if isinstance(usage_result, Exception):
                    lines.append(f"用量查询失败：{usage_result}")
                else:
                    rate_limit = usage_result.get("rate_limit") or {}
                    lines.append(f"账户：{usage_result.get('email') or '未知'}")
                    lines.append(
                        f"套餐：{usage_result.get('plan_type') or rate_limit.get('plan_type') or '未知'}"
                    )
                    lines.append(
                        "状态："
                        + (
                            "可用"
                            if rate_limit.get("allowed")
                            and not rate_limit.get("limit_reached")
                            else "受限"
                        )
                    )
                    for label, key in (
                        ("主窗口", "primary_window"),
                        ("次窗口", "secondary_window"),
                    ):
                        window = rate_limit.get(key)
                        if not isinstance(window, dict):
                            continue
                        reset_at = window.get("reset_at")
                        reset_text = "未知"
                        if isinstance(reset_at, (int, float)) and reset_at > 0:
                            reset_text = datetime.fromtimestamp(
                                reset_at, timezone.utc
                            ).strftime("%Y-%m-%d %H:%M UTC")
                        lines.append(
                            f"{label}：已用 {float(window.get('used_percent') or 0):.1f}%"
                            f"，重置 {reset_text}"
                        )
                if isinstance(credits_result, Exception):
                    lines.append(f"重置次数查询失败：{credits_result}")
                else:
                    lines.append(
                        f"可用重置次数：{int(credits_result.get('available_count') or 0)}"
                        f" / 累计 {int(credits_result.get('total_earned_count') or 0)}"
                    )
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
                cast(FlowMetric, self.config.get("flow_metric", "quota")),
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

"""AstrBot plugin commands for querying new-api."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
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
FLOW_DURATION_UNITS = {"m": 60, "h": 3600, "d": 86400}
MAX_FLOW_DURATION = 30 * 86400
FLOW_STAGE_ORDER: tuple[FlowStage, ...] = (
    "user",
    "node",
    "token",
    "group",
    "model",
    "channel",
)


class NewApiBindingError(NewApiError):
    """The current UMO has no configured new-api tenant."""


def parse_flow_duration(value: str) -> int:
    """Convert a compact duration such as ``30m``, ``1h`` or ``7d`` to seconds."""
    match = re.fullmatch(r"([1-9]\d*)([mhd])", value.strip(), re.IGNORECASE)
    if not match:
        raise NewApiError("时间范围格式错误，请使用 30m、1h 或 7d 等格式")
    seconds = int(match.group(1)) * FLOW_DURATION_UNITS[match.group(2).lower()]
    if seconds > MAX_FLOW_DURATION:
        raise NewApiError("统计时间范围不能超过 30 天")
    return seconds


@dataclass(frozen=True, slots=True)
class NewApiInstance:
    """A configured new-api tenant and its authenticated client."""

    name: str
    client: NewApiClient
    flow_stages: tuple[FlowStage, ...]


@star.register(
    "astrbot_plugin_newapi",
    "team-s2",
    "查询 new-api 渠道信息并绘制 Dashboard 流图",
    "1.3.0",
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
        self.instances: list[NewApiInstance] = []
        self.instances_by_umo: dict[str, NewApiInstance] = {}
        self._flow_render_lock = asyncio.Lock()
        self._load_instances()

    async def terminate(self) -> None:
        """Release all HTTP sessions when AstrBot unloads the plugin."""
        await asyncio.gather(*(item.client.close() for item in self.instances))

    def _load_instances(self) -> None:
        """Validate configured tenants and build the exact UMO routing table."""
        raw_instances = self.config.get("instances", [])
        if not isinstance(raw_instances, list):
            raise ValueError("new-api 实例配置必须是数组")

        try:
            timeout = float(self.config.get("request_timeout", 20))
        except (TypeError, ValueError) as error:
            raise ValueError("new-api 请求超时必须是数字") from error
        if timeout <= 0:
            raise ValueError("new-api 请求超时必须大于 0")
        for index, raw_instance in enumerate(raw_instances, start=1):
            if not isinstance(raw_instance, dict):
                raise ValueError(f"new-api 实例 #{index} 配置格式错误")

            name = str(raw_instance.get("name") or "").strip()
            base_url = str(raw_instance.get("base_url") or "").strip()
            access_token = str(raw_instance.get("access_token") or "").strip()
            try:
                user_id = int(raw_instance.get("user_id") or 0)
            except (TypeError, ValueError) as error:
                raise ValueError(f"new-api 实例 #{index} 的用户 ID 无效") from error
            if not name:
                raise ValueError(f"new-api 实例 #{index} 缺少实例名称")
            if not base_url:
                raise ValueError(f"new-api 实例“{name}”缺少地址")
            if not access_token:
                raise ValueError(f"new-api 实例“{name}”缺少 Access Token")
            if user_id <= 0:
                raise ValueError(f"new-api 实例“{name}”的用户 ID 必须为正整数")

            raw_umos = raw_instance.get("umos", [])
            if not isinstance(raw_umos, list):
                raise ValueError(f"new-api 实例“{name}”的 UMO 必须是数组")

            raw_stages = raw_instance.get(
                "flow_stages", ["token", "model", "channel"]
            )
            if not isinstance(raw_stages, list):
                raise ValueError(f"new-api 实例“{name}”的流图显示阶段必须是数组")
            selected_stages = set(raw_stages)
            unknown_stages = selected_stages.difference(FLOW_STAGE_ORDER)
            if unknown_stages:
                raise ValueError(
                    f"new-api 实例“{name}”包含无效流图阶段："
                    + "、".join(sorted(str(stage) for stage in unknown_stages))
                )
            flow_stages = tuple(
                stage for stage in FLOW_STAGE_ORDER if stage in selected_stages
            )
            if len(flow_stages) < 2:
                raise ValueError(f"new-api 实例“{name}”的流图至少需要选择两个阶段")

            instance = NewApiInstance(
                name=name,
                client=NewApiClient(base_url, access_token, user_id, timeout),
                flow_stages=flow_stages,
            )
            self.instances.append(instance)
            for raw_umo in raw_umos:
                umo = str(raw_umo or "").strip()
                umo_parts = umo.split(":", 2)
                if len(umo_parts) != 3 or not all(umo_parts):
                    raise ValueError(
                        f"new-api 实例“{name}”包含无效 UMO：{umo or '<空>'}"
                    )
                existing = self.instances_by_umo.get(umo)
                if existing is not None and existing is not instance:
                    raise ValueError(
                        f"UMO {umo} 同时绑定了实例“{existing.name}”和“{name}”"
                    )
                self.instances_by_umo[umo] = instance

    def _instance_for(self, event: AstrMessageEvent) -> NewApiInstance:
        """Resolve the tenant bound to the event's exact UMO."""
        umo = event.unified_msg_origin
        instance = self.instances_by_umo.get(umo)
        if instance is None:
            raise NewApiBindingError(
                "当前会话未绑定 new-api 实例。\n"
                f"UMO：{umo}\n"
                "请使用 /sid 确认 UMO，并在插件配置中完成绑定。"
            )
        return instance

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
        try:
            instance = self._instance_for(event)
        except NewApiBindingError as error:
            yield event.plain_result(str(error))
            return

        query = (channel or "").strip()
        if query:
            async for result in self._show_channel(event, instance, query):
                yield result
        else:
            async for result in self._list_channels(event, instance):
                yield result

    async def _list_channels(
        self, event: AstrMessageEvent, instance: NewApiInstance
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
                *(self._fetch_account_info(client, ch) for ch in account_channels)
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

    async def _show_channel(
        self,
        event: AstrMessageEvent,
        instance: NewApiInstance,
        query: str,
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

            quota_per_unit_result, account_info = await asyncio.gather(
                client.quota_per_unit(),
                self._fetch_account_info(client, found, include_credits=True),
                return_exceptions=True,
            )
            status = int(found.get("status", 0))
            models = [
                item for item in str(found.get("models") or "").split(",") if item
            ]
            lines = [
                f"【{instance.name}】渠道 #{channel_id} · {found.get('name') or '未命名'}",
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
    async def flow(self, event: AstrMessageEvent, duration: str = ""):
        """Render and send the configured new-api Dashboard flow.

        Args:
            event: Incoming AstrBot message event.
            duration: Optional compact duration; empty uses configuration.
        """
        output: Path | None = None
        try:
            instance = self._instance_for(event)
            range_seconds = (
                parse_flow_duration(duration)
                if duration
                else max(
                    3600,
                    min(
                        int(self.config.get("flow_hours", 24)) * 3600,
                        MAX_FLOW_DURATION,
                    ),
                )
            )
            end_timestamp = int(time.time())
            rows = await instance.client.flow(
                end_timestamp - range_seconds, end_timestamp
            )
            if not rows:
                raise NewApiError("所选时间范围内没有流图数据")

            output = Path(get_astrbot_temp_path()) / f"newapi-flow-{uuid4().hex}.png"
            event.track_temporary_local_file(str(output))
            font_value = str(self.config.get("font_path", "")).strip()
            async with self._flow_render_lock:
                summary = await asyncio.to_thread(
                    render_sankey,
                    rows,
                    output,
                    list(instance.flow_stages),
                    max(1, min(int(self.config.get("flow_top_n", 20)), 100)),
                    cast(
                        OverflowMode,
                        self.config.get("flow_overflow", "aggregate"),
                    ),
                    Path(font_value) if font_value else None,
                )
            logger.info(
                "Rendered new-api flow at %dx%d (%d pixels) with %d nodes and %d links",
                summary.width,
                summary.height,
                summary.pixel_count,
                summary.node_count,
                summary.link_count,
            )
            yield event.image_result(str(output))
        except NewApiBindingError as error:
            yield event.plain_result(str(error))
        except NewApiError as error:
            logger.warning("Failed to render new-api flow: %s", error)
            yield event.plain_result(f"生成 new-api 流图失败：{error}")
        except (ValueError, OSError) as error:
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
        self,
        client: NewApiClient,
        channel: dict[str, Any],
        include_credits: bool = False,
    ) -> tuple[str, object, object | None] | None:
        channel_id = int(channel.get("id") or 0)
        kind = self._account_info_kind(channel)
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

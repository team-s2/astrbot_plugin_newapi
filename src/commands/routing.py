"""``/newapi weight|priority``: set channel routing weight or priority."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image, Plain

from ..api.client import NewApiClient, NewApiError
from ..config import NewApiBindingError
from ..core.channels import channel_has_group, channel_type_name
from ..core.quota import collect_quotas, quota_version_label, routing_value
from .base import CommandBase

# new-api stores weight as an unsigned int and priority as a signed 64-bit int;
# int32 bounds keep the values safely round-trippable through every client.
ROUTING_LIMITS = {
    "weight": (0, 2**31 - 1, "权重", "同优先级内越大分到的请求越多"),
    "priority": (-(2**31), 2**31 - 1, "优先级", "越大越优先选中"),
}


def _parse_target(raw: str) -> int | str | None:
    """``all``, or a positive channel ID (a leading ``#`` is allowed)."""
    if raw.casefold() == "all":
        return "all"
    raw = raw.removeprefix("#")
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def _parse_value(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        return None


async def _update_many(
    client: NewApiClient, channels: list[dict[str, Any]], field: str, value: int
) -> list[tuple[dict[str, Any], NewApiError]]:
    """Write ``field`` on every channel, four at a time; return the failures."""
    semaphore = asyncio.Semaphore(4)

    async def update(channel):
        async with semaphore:
            try:
                await client.update_channel_fields(int(channel["id"]), {field: value})
            except NewApiError as error:
                return channel, error
            return None

    results = await asyncio.gather(*(update(channel) for channel in channels))
    return [result for result in results if result is not None]


class ChannelRoutingCommands(CommandBase):
    """Implementation of ``/newapi weight`` and ``/newapi priority``."""

    async def _set_channel_routing(
        self, event: AstrMessageEvent, args: str, field: str
    ):
        """Validate, update, and report a weight/priority change."""
        low, high, label, meaning = ROUTING_LIMITS[field]
        usage = (
            f"用法：/newapi {field} <渠道 ID|all> <{label}>（{meaning}）\n"
            f"all 会修改当前群聊可操作的全部渠道，例如 /newapi {field} all 1"
        )
        try:
            instance = self._instance_for(event)
            parts = args.split()
            if len(parts) != 2:
                yield event.plain_result(usage)
                return
            target = _parse_target(parts[0])
            if target is None:
                yield event.plain_result(f"无效的渠道 ID：{parts[0]}\n{usage}")
                return
            value = _parse_value(parts[1])
            if value is None:
                yield event.plain_result(f"{label}必须是整数：{parts[1]}")
                return
            if not low <= value <= high:
                yield event.plain_result(f"{label}必须在 {low} 到 {high} 之间。")
                return

            group = self._group_for(event, instance, False)
            channels = await instance.client.all_channels()
            if target == "all":
                selected = [
                    channel for channel in channels if channel_has_group(channel, group)
                ]
                if not selected:
                    scope = f"分组 {group} " if group else ""
                    yield event.plain_result(f"当前群聊{scope}没有可操作的渠道。")
                    return
            else:
                channel = next(
                    (item for item in channels if int(item.get("id") or 0) == target),
                    None,
                )
                if channel is None:
                    yield event.plain_result(f"渠道 #{target} 不存在。")
                    return
                if not channel_has_group(channel, group):
                    yield event.plain_result(
                        f"渠道 #{target} 不在当前群聊可操作的分组内。"
                    )
                    return
                selected = [channel]

            pending = [
                channel
                for channel in selected
                if routing_value(channel.get(field)) != value
            ]
            if not pending:
                subject = (
                    f"渠道 #{target} 的{label}"
                    if target != "all"
                    else f"全部 {len(selected)} 个渠道的{label}"
                )
                yield event.plain_result(f"{subject}已经是 {value}，无需修改。")
                return

            failures = await _update_many(instance.client, pending, field, value)
            if target != "all" and failures:
                raise failures[0][1]
            rows = await collect_quotas(
                instance.client, include_disabled=True, group=group
            )
            if target != "all":
                channel = selected[0]
                quota_row = next(
                    (row for row in rows if row.channel_id == target), None
                )
                version = (
                    quota_version_label(quota_row)
                    if quota_row
                    else channel_type_name(channel)
                )
                name = str(channel.get("name") or "未命名")
                details = (
                    f"已将渠道 #{target} 的{label}设为 {value}：{name} ({version})"
                )
            else:
                changed = len(pending) - len(failures)
                details = f"已将 {changed} 个渠道的{label}设为 {value}"
                if len(selected) > len(pending):
                    details += f"，{len(selected) - len(pending)} 个原本已是 {value}"
                if group:
                    details = f"分组 {group}：{details}"
                if failures:
                    details += f"\n{len(failures)} 个渠道修改失败：" + "".join(
                        f"\n- #{channel['id']} {channel.get('name') or '未命名'}：{error}"
                        for channel, error in failures
                    )
            output = await self._render_quota_image(event, instance, rows, time.time())
            yield event.chain_result(
                [Plain(details), Image.fromFileSystem(str(output))]
            )
        except NewApiBindingError as error:
            yield event.plain_result(str(error))
        except (NewApiError, ValueError, OSError) as error:
            logger.warning("Failed to update new-api channel routing: %s", error)
            yield event.plain_result(f"修改 new-api 渠道{label}失败：{error}")

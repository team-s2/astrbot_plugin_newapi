"""``/newapi weight|priority``: set one channel's routing weight or priority."""

from __future__ import annotations

import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image, Plain

from ..api.client import NewApiError
from ..config import NewApiBindingError
from ..core.channels import channel_has_group, channel_type_name
from ..core.quota import collect_quotas, quota_version_label
from .base import CommandBase

# new-api stores weight as an unsigned int and priority as a signed 64-bit int;
# int32 bounds keep the values safely round-trippable through every client.
ROUTING_LIMITS = {
    "weight": (0, 2**31 - 1, "权重", "越大分到的请求越多"),
    "priority": (-(2**31), 2**31 - 1, "优先级", "越大越优先选中"),
}


class ChannelRoutingCommands(CommandBase):
    """Implementation of ``/newapi weight`` and ``/newapi priority``."""

    async def _set_channel_routing(
        self, event: AstrMessageEvent, args: str, field: str
    ):
        """Validate, update, and report one channel weight/priority change."""
        low, high, label, meaning = ROUTING_LIMITS[field]
        try:
            instance = self._instance_for(event)
            parts = args.split()
            if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) <= 0:
                yield event.plain_result(
                    f"用法：/newapi {field} <渠道 ID> <{label}>（{meaning}）"
                )
                return
            try:
                value = int(parts[1])
            except ValueError:
                yield event.plain_result(f"{label}必须是整数。")
                return
            if not low <= value <= high:
                yield event.plain_result(
                    f"{label}必须在 {low} 到 {high} 之间。"
                )
                return

            target_id = int(parts[0])
            group = self._group_for(event, instance, False)
            channels = await instance.client.all_channels()
            channel = next(
                (item for item in channels if int(item.get("id") or 0) == target_id),
                None,
            )
            if channel is None:
                yield event.plain_result(f"渠道 #{target_id} 不存在。")
                return
            if not channel_has_group(channel, group):
                yield event.plain_result(
                    f"渠道 #{target_id} 不在当前群聊可操作的分组内。"
                )
                return

            current = channel.get(field)
            if current is not None and int(current) == value:
                yield event.plain_result(
                    f"渠道 #{target_id} 的{label}已经是 {value}，无需修改。"
                )
                return

            await instance.client.update_channel_fields(target_id, {field: value})

            name = str(channel.get("name") or "未命名")
            rows = await collect_quotas(
                instance.client, include_disabled=True, group=group
            )
            quota_row = next(
                (row for row in rows if row.channel_id == target_id), None
            )
            version = (
                quota_version_label(quota_row)
                if quota_row
                else channel_type_name(channel)
            )
            details = (
                f"已将渠道 #{target_id} 的{label}设为 {value}：{name} ({version})"
            )
            output = await self._render_quota_image(event, rows, time.time())
            yield event.chain_result(
                [Plain(details), Image.fromFileSystem(str(output))]
            )
        except NewApiBindingError as error:
            yield event.plain_result(str(error))
        except (NewApiError, ValueError, OSError) as error:
            logger.warning("Failed to update new-api channel routing: %s", error)
            yield event.plain_result(f"修改 new-api 渠道{label}失败：{error}")

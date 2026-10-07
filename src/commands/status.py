"""``/newapi on|off``: enable or disable one channel."""

from __future__ import annotations

import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image, Plain

from ..api.client import NewApiError
from ..config import NewApiBindingError
from ..core.channels import CHANNEL_STATUSES, channel_has_group, channel_type_name
from ..core.quota import collect_quotas, quota_version_label
from .base import CommandBase


class StatusCommands(CommandBase):
    """Implementation of ``/newapi on`` and ``/newapi off``."""

    async def _set_channel_status(
        self, event: AstrMessageEvent, channel_id: str, enabled: bool
    ):
        """Validate, update, and report one channel status change."""
        try:
            instance = self._instance_for(event)
            query = channel_id.strip()
            group = self._group_for(event, instance, False)
            if not query.isdigit() or int(query) <= 0:
                yield event.plain_result("用法：/newapi on|off <渠道 ID>")
                return
            target_id = int(query)
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

            current_status = int(channel.get("status") or 0)
            target_status = 1 if enabled else 2
            action = "启用" if enabled else "禁用"
            already_enabled = current_status == 1
            already_disabled = current_status in (2, 3)
            if (enabled and already_enabled) or (not enabled and already_disabled):
                status_text = CHANNEL_STATUSES.get(current_status, "未知")
                yield event.plain_result(
                    f"渠道 #{target_id} 已经是{status_text}状态，无需{action}。"
                )
                return

            changed = await instance.client.update_channel_status(
                target_id, target_status
            )
            if not changed:
                yield event.plain_result(f"渠道 #{target_id} 状态未发生变化，请重试。")
                return

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
            details = f"已{action}渠道 #{target_id}：{name} ({version})"
            output = await self._render_quota_image(event, rows, time.time())
            yield event.chain_result(
                [Plain(details), Image.fromFileSystem(str(output))]
            )
        except NewApiBindingError as error:
            yield event.plain_result(str(error))
        except (NewApiError, ValueError, OSError) as error:
            logger.warning("Failed to update new-api channel status: %s", error)
            yield event.plain_result(f"修改 new-api 渠道状态失败：{error}")

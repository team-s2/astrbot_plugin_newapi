"""``/newapi quota``: render the subscription quota overview."""

from __future__ import annotations

import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..api.client import NewApiError
from ..config import NewApiBindingError
from ..core.quota import collect_quotas
from .base import CommandBase


class QuotaCommands(CommandBase):
    """Implementation of ``/newapi quota``."""

    async def _quota(self, event: AstrMessageEvent, scope: str):
        """Send a quota image for the enabled channels, or all with ``all``."""
        scope, allgroup = self._without_allgroup(scope.strip())
        scope = scope.lower()
        if scope not in ("", "all"):
            yield event.plain_result("用法：/newapi quota [all]")
            return
        try:
            instance = self._instance_for(event)
            group = self._group_for(event, instance, allgroup)
            async with self._quota_render_lock:
                rows = await collect_quotas(
                    instance.client, include_disabled=scope == "all", group=group
                )
                output = await self._render_quota_image(event, rows, time.time())
            yield event.image_result(str(output))
        except NewApiBindingError as error:
            yield event.plain_result(str(error))
        except (NewApiError, ValueError, OSError) as error:
            logger.warning("Failed to render new-api quota: %s", error)
            yield event.plain_result(f"生成 new-api 额度图失败：{error}")

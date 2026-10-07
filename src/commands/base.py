"""Shared state and helpers for the command mixins."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from ..config import NewApiBindingError, NewApiInstance
from ..core.quota import ChannelQuota
from ..render.quota import render_quota


class CommandBase:
    """Attributes and helpers the plugin class provides to every command mixin."""

    config: AstrBotConfig
    instances: list[NewApiInstance]
    instances_by_umo: dict[str, NewApiInstance]
    _flow_render_lock: asyncio.Lock
    _quota_render_lock: asyncio.Lock

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

    @staticmethod
    def _without_allgroup(value: str) -> tuple[str, bool]:
        parts = value.split()
        has_allgroup = any(part.casefold() == "allgroup" for part in parts)
        return (
            " ".join(part for part in parts if part.casefold() != "allgroup"),
            has_allgroup,
        )

    def _group_for(
        self, event: AstrMessageEvent, instance: NewApiInstance, allgroup: bool
    ) -> str | None:
        if allgroup:
            return None
        return instance.group_filters.get(event.unified_msg_origin)

    def _font_path(self) -> Path | None:
        font_value = str(self.config.get("font_path", "")).strip()
        return Path(font_value) if font_value else None

    async def _render_quota_image(
        self, event: AstrMessageEvent, rows: list[ChannelQuota], now: float
    ) -> Path:
        """Render the quota overview to a tracked temporary PNG file."""
        output = Path(get_astrbot_temp_path()) / f"newapi-quota-{uuid4().hex}.png"
        event.track_temporary_local_file(str(output))
        await asyncio.to_thread(render_quota, rows, output, now, self._font_path())
        return output

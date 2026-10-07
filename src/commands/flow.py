"""``/newapi flow``: render the Dashboard token flow."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import cast
from uuid import uuid4

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from ..api.client import NewApiError
from ..config import NewApiBindingError
from ..core.channels import channel_has_group
from ..render.flow import OverflowMode, render_sankey
from .base import CommandBase

FLOW_DURATION_UNITS = {"m": 60, "h": 3600, "d": 86400}


def parse_flow_duration(value: str) -> int:
    """Convert a compact duration such as ``30m``, ``1h`` or ``7d`` to seconds."""
    match = re.fullmatch(r"([1-9]\d*)([mhd])", value.strip(), re.IGNORECASE)
    if not match:
        raise NewApiError("时间范围格式错误，请使用 30m、1h 或 7d 等格式")
    return int(match.group(1)) * FLOW_DURATION_UNITS[match.group(2).lower()]


class FlowCommands(CommandBase):
    """Implementation of ``/newapi flow``."""

    async def _flow(self, event: AstrMessageEvent, duration: str):
        """Render and send the configured new-api Dashboard flow.

        Args:
            event: Incoming AstrBot message event.
            duration: Optional compact duration; empty uses configuration.
        """
        output: Path | None = None
        try:
            instance = self._instance_for(event)
            duration, allgroup = self._without_allgroup(duration.strip())
            group = self._group_for(event, instance, allgroup)
            range_seconds = (
                parse_flow_duration(duration)
                if duration
                else max(3600, int(self.config.get("flow_hours", 24)) * 3600)
            )
            end_timestamp = int(time.time())
            rows = await instance.client.flow(
                end_timestamp - range_seconds, end_timestamp
            )
            # Channel tests are logged without a token, so token_id is 0 (omitted).
            rows = [row for row in rows if row.get("token_id")]
            if group:
                channels = await instance.client.all_channels()
                channel_ids = {
                    int(channel["id"])
                    for channel in channels
                    if channel_has_group(channel, group) and channel.get("id")
                }
                rows = [
                    row
                    for row in rows
                    if int(row.get("channel_id") or 0) in channel_ids
                ]
            if not rows:
                raise NewApiError("所选时间范围内没有流图数据")

            output = Path(get_astrbot_temp_path()) / f"newapi-flow-{uuid4().hex}.png"
            event.track_temporary_local_file(str(output))
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
                    self._font_path(),
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

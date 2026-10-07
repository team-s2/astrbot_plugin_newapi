"""AstrBot plugin commands for querying new-api."""

from __future__ import annotations

import asyncio

from astrbot.api import AstrBotConfig, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.star.filter.command import GreedyStr

from .src.commands.channel import ChannelCommands
from .src.commands.flow import FlowCommands
from .src.commands.quota import QuotaCommands
from .src.commands.status import StatusCommands
from .src.config import load_instances


class NewApiPlugin(
    ChannelCommands, QuotaCommands, StatusCommands, FlowCommands, star.Star
):
    """Expose read-only new-api commands to sessions bound to an instance."""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        """Initialize the plugin from AstrBot configuration.

        Args:
            context: AstrBot plugin context.
            config: Plugin configuration from the WebUI.
        """
        super().__init__(context)
        self.config = config
        self._flow_render_lock = asyncio.Lock()
        self._quota_render_lock = asyncio.Lock()
        self.instances, self.instances_by_umo = load_instances(self.config)

    async def terminate(self) -> None:
        """Release all HTTP sessions when AstrBot unloads the plugin."""
        await asyncio.gather(*(item.client.close() for item in self.instances))

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
        async for result in self._channel(event, channel):
            yield result

    @newapi.command("quota")
    async def quota(self, event: AstrMessageEvent, scope: GreedyStr = ""):
        """Send a quota image for the enabled channels, or all with ``all``."""
        async for result in self._quota(event, scope):
            yield result

    @newapi.command("on")
    async def enable_channel(self, event: AstrMessageEvent, channel_id: GreedyStr = ""):
        """Enable one existing channel and send its updated quota overview."""
        async for result in self._set_channel_status(event, channel_id, True):
            yield result

    @newapi.command("off")
    async def disable_channel(
        self, event: AstrMessageEvent, channel_id: GreedyStr = ""
    ):
        """Disable one existing channel and send its updated quota overview."""
        async for result in self._set_channel_status(event, channel_id, False):
            yield result

    @newapi.command("flow")
    async def flow(self, event: AstrMessageEvent, duration: GreedyStr = ""):
        """Render and send the configured new-api Dashboard flow.

        Args:
            event: Incoming AstrBot message event.
            duration: Optional compact duration; empty uses configuration.
        """
        async for result in self._flow(event, duration):
            yield result

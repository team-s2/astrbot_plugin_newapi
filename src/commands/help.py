"""``/newapi``: list the plugin's subcommands for the bare group invocation."""

from __future__ import annotations

import re

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent
from astrbot.api.event.filter import CustomFilter

from .base import CommandBase

GROUP_NAME = "newapi"

# (usage, description) pairs rendered as the unordered help list.
HELP_ENTRIES = (
    ("quota [all]", "生成额度图（含权重与优先级，按渠道编号排序）；加 all 时包含已禁用渠道"),
    ("on <渠道 ID>", "启用渠道并返回最新额度图"),
    ("off <渠道 ID>", "禁用渠道并返回最新额度图"),
    ("weight <渠道 ID> <数值>", "设置渠道权重（越大分到的请求越多）并返回最新额度图"),
    ("priority <渠道 ID> <数值>", "设置渠道优先级（越大越优先选中）并返回最新额度图"),
    ("flow [时间范围]", "生成 Dashboard Flow 流图；时间范围支持 30m、1h、7d 等格式"),
)


def _is_bare_group(event: AstrMessageEvent) -> bool:
    """Whether the woken message is exactly the group name, no subcommand."""
    if not event.is_at_or_wake_command:
        return False
    text = re.sub(r"\s+", " ", event.message_str.strip())
    return text == GROUP_NAME


class NotBareGroup(CustomFilter):
    """Keep AstrBot's auto-generated command tree off the bare group name.

    Stacked under ``@filter.command_group`` it is evaluated before the group
    filter, so returning False for the bare group name skips the framework's
    “参数不足” reply. Subcommands are unaffected: they match through their own
    command filters and never evaluate this one.
    """

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        return not _is_bare_group(event)


class OnlyBareGroup(CustomFilter):
    """Let the plain ``newapi`` command answer only the bare group name.

    Without this filter the command would also swallow every
    ``/newapi <subcommand> ...`` invocation, because AstrBot ignores leftover
    parameters for handlers without declared arguments.
    """

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        return _is_bare_group(event)


class HelpCommands(CommandBase):
    """Implementation of the bare ``/newapi`` help listing."""

    async def _help(self, event: AstrMessageEvent):
        """Send the plugin's subcommands as an unordered list."""
        lines = ["/newapi 子命令：", ""]
        lines.extend(f"- /newapi {usage}：{desc}" for usage, desc in HELP_ENTRIES)
        lines.append("")
        lines.append("仅绑定了 new-api 实例的会话可以执行以上命令。")
        yield event.plain_result("\n".join(lines))

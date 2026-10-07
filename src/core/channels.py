"""Channel metadata helpers shared by the commands."""

from __future__ import annotations

from typing import Any

from .quota import account_kind

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
    100: "Zhipu Coding Plan",
    101: "Grok Subscription",
}
CHANNEL_STATUSES = {0: "未知", 1: "启用", 2: "手动禁用", 3: "自动禁用"}


def channel_has_group(channel: dict[str, Any], group: str | None) -> bool:
    """Whether the channel belongs to ``group``; no group matches everything."""
    if not group:
        return True
    return group in {
        part.strip() for part in str(channel.get("group") or "").split(",")
    }


def channel_type_name(channel: dict[str, Any]) -> str:
    """Human-readable channel type name."""
    if account_kind(channel) == "zhipu":
        return "Zhipu Coding Plan"
    channel_type = int(channel.get("type") or 0)
    return CHANNEL_TYPES.get(channel_type, f"类型 {channel_type}")

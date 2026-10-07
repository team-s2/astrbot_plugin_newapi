"""Plugin configuration parsing and per-UMO new-api instance routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .api.client import NewApiClient, NewApiError
from .render.flow import FlowStage

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


@dataclass(frozen=True, slots=True)
class NewApiInstance:
    """A configured new-api tenant and its authenticated client."""

    name: str
    client: NewApiClient
    flow_stages: tuple[FlowStage, ...]
    group_filters: dict[str, str]


def load_instances(
    config: Any,
) -> tuple[list[NewApiInstance], dict[str, NewApiInstance]]:
    """Validate configured tenants and build the exact UMO routing table.

    Args:
        config: Plugin configuration from the WebUI.

    Returns:
        All configured instances and the mapping from UMO to instance.
    """
    instances: list[NewApiInstance] = []
    instances_by_umo: dict[str, NewApiInstance] = {}
    raw_instances = config.get("instances", [])
    if not isinstance(raw_instances, list):
        raise ValueError("new-api 实例配置必须是数组")

    try:
        timeout = float(config.get("request_timeout", 20))
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

        group_filters = _parse_group_filters(
            name, raw_instance.get("group_filters", [])
        )
        flow_stages = _parse_flow_stages(
            name, raw_instance.get("flow_stages", ["token", "model", "channel"])
        )

        instance = NewApiInstance(
            name=name,
            client=NewApiClient(base_url, access_token, user_id, timeout),
            flow_stages=flow_stages,
            group_filters=group_filters,
        )
        instances.append(instance)
        for raw_umo in raw_umos:
            umo = str(raw_umo or "").strip()
            umo_parts = umo.split(":", 2)
            if len(umo_parts) != 3 or not all(umo_parts):
                raise ValueError(f"new-api 实例“{name}”包含无效 UMO：{umo or '<空>'}")
            existing = instances_by_umo.get(umo)
            if existing is not None and existing is not instance:
                raise ValueError(f"UMO {umo} 同时绑定了实例“{existing.name}”和“{name}”")
            instances_by_umo[umo] = instance
    return instances, instances_by_umo


def _parse_group_filters(name: str, raw_group_filters: Any) -> dict[str, str]:
    """Parse ``UMO=分组`` entries into a UMO to group mapping."""
    if not isinstance(raw_group_filters, list):
        raise ValueError(f"new-api 实例“{name}”的群聊分组必须是数组")
    group_filters: dict[str, str] = {}
    for raw_filter in raw_group_filters:
        if isinstance(raw_filter, dict):
            filter_umo = str(raw_filter.get("umo") or "").strip()
            filter_group = str(raw_filter.get("group") or "").strip()
        else:
            filter_umo, separator, filter_group = str(raw_filter).partition("=")
            filter_umo = filter_umo.strip()
            filter_group = filter_group.strip() if separator else ""
        if not filter_umo or not filter_group:
            raise ValueError(f"new-api 实例“{name}”的群聊分组格式错误，应为 UMO=分组")
        if filter_umo in group_filters and group_filters[filter_umo] != filter_group:
            raise ValueError(f"UMO {filter_umo} 配置了多个不同的 new-api 分组")
        group_filters[filter_umo] = filter_group
    return group_filters


def _parse_flow_stages(name: str, raw_stages: Any) -> tuple[FlowStage, ...]:
    """Validate selected flow stages and return them in canonical order."""
    if not isinstance(raw_stages, list):
        raise ValueError(f"new-api 实例“{name}”的流图显示阶段必须是数组")
    selected_stages = set(raw_stages)
    unknown_stages = selected_stages.difference(FLOW_STAGE_ORDER)
    if unknown_stages:
        raise ValueError(
            f"new-api 实例“{name}”包含无效流图阶段："
            + "、".join(sorted(str(stage) for stage in unknown_stages))
        )
    flow_stages = tuple(stage for stage in FLOW_STAGE_ORDER if stage in selected_stages)
    if len(flow_stages) < 2:
        raise ValueError(f"new-api 实例“{name}”的流图至少需要选择两个阶段")
    return flow_stages

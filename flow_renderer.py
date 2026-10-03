"""Render new-api flow rows as a light Sankey diagram with Skia."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, Literal

import skia

from .account_info import compact_token_count
from .painter import Painter

FlowStage = Literal["user", "node", "token", "group", "model", "channel"]
OverflowMode = Literal["aggregate", "hide"]

PALETTE = (
    0xFF48C98E,
    0xFF2F72F6,
    0xFFF9C51B,
    0xFF42C6E9,
    0xFFFF8B18,
    0xFFD2B5F3,
    0xFF8252DF,
    0xFF9FC5F8,
    0xFFFFC565,
    0xFFB8E8CD,
    0xFFFFE578,
    0xFF38557D,
)
BG = 0xFFF4F5F7
PANEL = 0xFFFFFFFF
INK = 0xFF1D2230
SUB = 0xFF6B7280
TRUNK = 0xFF9AA3B2  # single nodes ahead of the colour-giving stage

OTHER_LABELS: dict[FlowStage, str] = {
    "user": "Other users",
    "node": "Other nodes",
    "token": "Other tokens",
    "group": "Other groups",
    "model": "Other models",
    "channel": "Other channels",
}

MIN_IMAGE_WIDTH = 3600
MIN_IMAGE_HEIGHT = 2240
MAX_IMAGE_PIXELS = 64_000_000
FONT_SIZE = 40
HORIZONTAL_MARGIN = 80
VERTICAL_MARGIN = 80
PANEL_INSET = 32  # white panel edge inside the light page
NODE_RADIUS = 6
VALUE_GAP = 14  # between a node's name and its token count
NODE_WIDTH = 56
NODE_GAP = 14
MIN_NODE_HEIGHT = 8
LABEL_PADDING = 8
COLUMN_GAP = 120
LABEL_LINE_GAP = 56
PROPORTIONAL_FLOW_HEIGHT = 1120


@dataclass(frozen=True)
class FlowNode:
    """A single stage value in one flow path."""

    id: str
    label: str
    kind: FlowStage


@dataclass(frozen=True)
class RenderSummary:
    """Summary of a completed Sankey render."""

    row_count: int
    node_count: int
    link_count: int
    width: int
    height: int
    pixel_count: int


def _number(value: Any) -> float:
    """Coerce an API value to a non-negative number.

    Args:
        value: Value returned by new-api.

    Returns:
        A non-negative float, or zero for invalid input.
    """
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _row_node(row: dict[str, Any], stage: FlowStage) -> FlowNode:
    """Build a stable node identity and label from a flow row.

    Args:
        row: Flow row returned by new-api.
        stage: Dimension to extract.

    Returns:
        The node for the selected stage.
    """
    if stage == "user":
        user_id = int(_number(row.get("user_id")))
        label = str(
            row.get("username") or (f"user-{user_id}" if user_id else "Unknown User")
        )
        identity = str(user_id) if user_id else label
    elif stage == "node":
        label = str(row.get("node_name") or "default-node")
        identity = label
    elif stage == "token":
        token_id = int(_number(row.get("token_id")))
        label = str(row.get("token_name") or f"Deleted token #{token_id}")
        identity = str(token_id)
    elif stage == "group":
        label = str(row.get("use_group") or "default")
        identity = label
    elif stage == "model":
        label = str(row.get("model_name") or "Unknown model")
        identity = label
    else:
        channel_id = int(_number(row.get("channel_id")))
        label = str(row.get("channel_name") or f"Channel #{channel_id}")
        identity = str(channel_id)
    return FlowNode(id=f"{stage}:{identity}", label=label, kind=stage)


def _truncate(text: str, limit: int) -> str:
    """Shorten a label to the drawing limit.

    Args:
        text: Original label.
        limit: Maximum character count.

    Returns:
        Original or ellipsis-truncated label.
    """
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _node_label(node: FlowNode, value: float) -> tuple[str, str]:
    """Name and token count drawn beside a Sankey node."""
    # Every value is a token count, so the shared suffix is left out.
    return _truncate(node.label, 22), compact_token_count(value).removesuffix(" tokens")


def render_sankey(
    rows: list[dict[str, Any]],
    output: Path,
    stages: list[FlowStage],
    top_limit: int = 20,
    overflow_mode: OverflowMode = "aggregate",
    font_path: Path | None = None,
) -> RenderSummary:
    """Render new-api flow data in the visual style of its VChart Sankey.

    Args:
        rows: Flow rows returned by ``/api/data/flow``.
        output: Destination PNG path.
        stages: Ordered stages to display; at least two are required.
        top_limit: Maximum named nodes retained in each stage.
        overflow_mode: Aggregate overflow nodes or hide their entire paths.
        font_path: Optional custom font with CJK support.

    Returns:
        Counts describing the rendered graph.

    Raises:
        ValueError: If options are invalid or no positive flow remains.
    """
    if len(stages) < 2:
        raise ValueError("at least two flow stages must be visible")
    if overflow_mode not in ("aggregate", "hide"):
        raise ValueError(f"unsupported overflow mode: {overflow_mode}")

    prepared: list[tuple[list[FlowNode], float]] = []
    stage_totals: list[defaultdict[str, float]] = [defaultdict(float) for _ in stages]
    for row in rows:
        value = _number(row.get("token_used"))
        if value <= 0:
            continue
        path = [_row_node(row, stage) for stage in stages]
        prepared.append((path, value))
        for index, node in enumerate(path):
            stage_totals[index][node.id] += value

    top_ids: list[set[str]] = []
    for totals in stage_totals:
        ordered = sorted(totals, key=lambda node_id: (-totals[node_id], node_id))
        top_ids.append(set(ordered[:top_limit]))

    node_info: dict[str, FlowNode] = {}
    paths: dict[tuple[str, ...], float] = {}
    for path, value in prepared:
        has_overflow = any(
            node.id not in top_ids[index] for index, node in enumerate(path)
        )
        if has_overflow and overflow_mode == "hide":
            continue
        normalized: list[FlowNode] = []
        for index, node in enumerate(path):
            if node.id in top_ids[index]:
                normalized.append(node)
            else:
                normalized.append(
                    FlowNode(
                        id=f"{node.kind}:__other__",
                        label=OTHER_LABELS[node.kind],
                        kind=node.kind,
                    )
                )
        ids = tuple(node.id for node in normalized)
        paths[ids] = paths.get(ids, 0) + value
        for node in normalized:
            node_info[node.id] = node

    if not paths:
        raise ValueError("no positive flow data is available")

    # Colour by the first stage that actually branches: a lone user or node
    # would otherwise paint the whole diagram in a single colour.
    color_stage = next(
        (
            index
            for index in range(len(stages))
            if len({path[index] for path in paths}) > 1
        ),
        0,
    )
    color_totals: defaultdict[str, float] = defaultdict(float)
    for path, value in paths.items():
        color_totals[path[color_stage]] += value
    # Palette runs bottom-up through that column, which is laid out largest
    # first, so the first colour lands on the bottom node.
    color_ids = sorted(
        color_totals,
        key=lambda node_id: (-color_totals[node_id], node_info[node_id].label),
        reverse=True,
    )
    root_colors = {
        node_id: PALETTE[index % len(PALETTE)]
        for index, node_id in enumerate(color_ids)
    }
    node_totals: list[defaultdict[str, float]] = [defaultdict(float) for _ in stages]
    link_totals: list[defaultdict[tuple[str, str], float]] = [
        defaultdict(float) for _ in range(len(stages) - 1)
    ]
    # Shared nodes and ribbons take the colour that contributes most to them.
    node_votes: defaultdict[str, defaultdict[int, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    link_votes: list[defaultdict[tuple[str, str], defaultdict[int, float]]] = [
        defaultdict(lambda: defaultdict(float)) for _ in range(len(stages) - 1)
    ]
    for path, value in paths.items():
        color = root_colors[path[color_stage]]
        for index, node_id in enumerate(path):
            node_totals[index][node_id] += value
            node_votes[node_id][color if index >= color_stage else TRUNK] += value
        for index in range(len(path) - 1):
            key = (path[index], path[index + 1])
            link_totals[index][key] += value
            link_votes[index][key][color] += value

    def dominant(votes: dict[int, float]) -> int:
        return max(votes, key=lambda color: (votes[color], color))

    node_colors = {node_id: dominant(votes) for node_id, votes in node_votes.items()}
    link_colors = [
        {key: dominant(votes) for key, votes in stage.items()} for stage in link_votes
    ]
    del node_votes, link_votes

    measure = Painter(1, 1, font_path, scale=1)
    metrics = measure.font(FONT_SIZE, "regular").getMetrics()
    label_height = metrics.fDescent - metrics.fAscent
    label_line_gap = max(LABEL_LINE_GAP, label_height + 8)

    def label_width(node_id: str, value: float) -> float:
        name, count = _node_label(node_info[node_id], value)
        return (
            measure.measure(name, FONT_SIZE)
            + VALUE_GAP
            + measure.measure(count, FONT_SIZE, "bold")
        )

    stage_label_widths = [
        max(
            (label_width(node_id, value) for node_id, value in totals.items()),
            default=0,
        )
        for totals in node_totals
    ]

    natural_width = (
        2 * HORIZONTAL_MARGIN
        + len(stages) * (NODE_WIDTH + LABEL_PADDING)
        + sum(stage_label_widths)
        + (len(stages) - 1) * COLUMN_GAP
    )
    width = max(MIN_IMAGE_WIDTH, ceil(natural_width))
    extra_column_gap = (width - natural_width) / (len(stages) - 1)
    node_x = [float(HORIZONTAL_MARGIN)]
    for label_width in stage_label_widths[:-1]:
        node_x.append(
            node_x[-1]
            + NODE_WIDTH
            + LABEL_PADDING
            + label_width
            + COLUMN_GAP
            + extra_column_gap
        )

    max_stage_nodes = max(len(totals) for totals in node_totals)
    min_node_height = max(MIN_NODE_HEIGHT, label_line_gap - NODE_GAP)
    label_area_height = label_height + label_line_gap * (max_stage_nodes - 1)
    node_area_height = (
        min_node_height * max_stage_nodes
        + NODE_GAP * (max_stage_nodes - 1)
        + PROPORTIONAL_FLOW_HEIGHT
    )
    height = max(
        MIN_IMAGE_HEIGHT,
        ceil(2 * VERTICAL_MARGIN + max(label_area_height, node_area_height)),
    )
    pixel_count = width * height
    if pixel_count > MAX_IMAGE_PIXELS:
        raise ValueError(
            f"流图需要 {pixel_count:,} 像素，超过 {MAX_IMAGE_PIXELS:,} 像素的"
            "安全限制；请减少 Top N 或当前实例的可见阶段"
        )

    positions: list[dict[str, dict[str, Any]]] = []
    for index, totals in enumerate(node_totals):
        ordered = sorted(
            totals,
            key=lambda node_id: (-totals[node_id], node_info[node_id].label),
        )
        available = height - 2 * VERTICAL_MARGIN - NODE_GAP * max(len(ordered) - 1, 0)
        baseline = min(min_node_height, available / max(len(ordered), 1))
        flexible = max(available - baseline * len(ordered), 0)
        total = sum(totals.values()) or 1
        cursor = float(VERTICAL_MARGIN)
        stage_positions: dict[str, dict[str, Any]] = {}
        for node_id in ordered:
            node_height = baseline + flexible * totals[node_id] / total
            stage_positions[node_id] = {
                "x": node_x[index],
                "y0": cursor,
                "y1": cursor + node_height,
                "value": totals[node_id],
                "color": node_colors[node_id],
            }
            cursor += node_height + NODE_GAP
        positions.append(stage_positions)

    links: list[dict[str, Any]] = []
    for stage, totals in enumerate(link_totals):
        by_source: defaultdict[str, list[tuple[tuple[str, str], float]]] = defaultdict(
            list
        )
        for key, value in totals.items():
            by_source[key[0]].append((key, value))
        alphas: dict[tuple[str, str], float] = {}
        for source_links in by_source.values():
            source_links.sort(key=lambda item: (-item[1], item[0]))
            denominator = max(len(source_links) - 1, 1)
            for index, (key, _value) in enumerate(source_links):
                alphas[key] = (
                    0.34 if len(source_links) == 1 else 0.24 + index / denominator * 0.2
                )

        source_cursor = {
            node_id: node["y0"] for node_id, node in positions[stage].items()
        }
        target_cursor = {
            node_id: node["y0"] for node_id, node in positions[stage + 1].items()
        }
        ordered_links = sorted(
            totals.items(),
            key=lambda item: (
                positions[stage][item[0][0]]["y0"],
                positions[stage + 1][item[0][1]]["y0"],
            ),
        )
        for (source, target), value in ordered_links:
            source_node = positions[stage][source]
            target_node = positions[stage + 1][target]
            source_height = (
                (source_node["y1"] - source_node["y0"]) * value / source_node["value"]
            )
            target_height = (
                (target_node["y1"] - target_node["y0"]) * value / target_node["value"]
            )
            key = (source, target)
            links.append(
                {
                    "x0": source_node["x"] + NODE_WIDTH,
                    "x1": target_node["x"],
                    "sy0": source_cursor[source],
                    "sy1": source_cursor[source] + source_height,
                    "ty0": target_cursor[target],
                    "ty1": target_cursor[target] + target_height,
                    "color": link_colors[stage][key],
                    "alpha": alphas[key],
                    "value": value,
                }
            )
            source_cursor[source] += source_height
            target_cursor[target] += target_height

    node_count = sum(len(stage) for stage in positions)
    link_count = len(links)
    del prepared, stage_totals, top_ids, paths
    del (
        color_ids,
        root_colors,
        node_colors,
        link_totals,
        link_colors,
        node_totals,
    )

    p = Painter(width, height, font_path, scale=1, background=BG)
    p.rect(
        PANEL_INSET, PANEL_INSET, width - PANEL_INSET, height - PANEL_INSET, PANEL, 28
    )

    # Largest ribbons first so thin ones stay visible on top.
    for link in sorted(links, key=lambda item: item["value"], reverse=True):
        x0, x1 = link["x0"], link["x1"]
        control = (x1 - x0) * 0.48
        path = skia.Path()
        path.moveTo(x0, link["sy0"])
        path.cubicTo(
            x0 + control, link["sy0"], x1 - control, link["ty0"], x1, link["ty0"]
        )
        path.lineTo(x1, link["ty1"])
        path.cubicTo(
            x1 - control, link["ty1"], x0 + control, link["sy1"], x0, link["sy1"]
        )
        path.close()
        color = (link["color"] & 0xFFFFFF) | (round(link["alpha"] * 255) << 24)
        p.canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=color))

    label_top = VERTICAL_MARGIN + label_height / 2
    label_bottom = height - VERTICAL_MARGIN - label_height / 2
    for stage, stage_positions in enumerate(positions):
        ordered_nodes = sorted(stage_positions.items(), key=lambda item: item[1]["y0"])
        # Labels follow their nodes but keep one line apart, pushed up from the
        # bottom edge if the column runs out of room.
        label_centers: list[float] = []
        for _node_id, node in ordered_nodes:
            desired = (node["y0"] + node["y1"]) / 2
            floor_y = label_centers[-1] + label_line_gap if label_centers else label_top
            label_centers.append(max(desired, floor_y))
        if label_centers and label_centers[-1] > label_bottom:
            label_centers[-1] = label_bottom
            for index in range(len(label_centers) - 2, -1, -1):
                label_centers[index] = min(
                    label_centers[index], label_centers[index + 1] - label_line_gap
                )
        for (node_id, node), label_center in zip(
            ordered_nodes, label_centers, strict=True
        ):
            x = node["x"]
            radius = min(NODE_RADIUS, (node["y1"] - node["y0"]) / 2)
            p.rect(x, node["y0"], x + NODE_WIDTH, node["y1"], node["color"], radius)
            name, count = _node_label(node_info[node_id], node["value"])
            label_x = x + NODE_WIDTH + LABEL_PADDING
            label_x += p.text(label_x, label_center, name, FONT_SIZE, INK)
            p.text(label_x + VALUE_GAP, label_center, count, FONT_SIZE, SUB, "bold")

    output.parent.mkdir(parents=True, exist_ok=True)
    p.save(output)
    return RenderSummary(
        row_count=len(rows),
        node_count=node_count,
        link_count=link_count,
        width=width,
        height=height,
        pixel_count=pixel_count,
    )

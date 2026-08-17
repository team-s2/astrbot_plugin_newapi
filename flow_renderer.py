"""Render new-api flow rows as a light Sankey diagram with Pillow."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFont

from .account_info import compact_token_count

FlowStage = Literal["user", "node", "token", "group", "model", "channel"]
OverflowMode = Literal["aggregate", "hide"]

PALETTE = (
    "#48c98e",
    "#2f72f6",
    "#f9c51b",
    "#42c6e9",
    "#ff8b18",
    "#d2b5f3",
    "#8252df",
    "#9fc5f8",
    "#ffc565",
    "#b8e8cd",
    "#ffe578",
    "#38557d",
)

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


def _load_font(size: int, font_path: Path | None) -> ImageFont.FreeTypeFont:
    """Load a custom, CJK, or portable fallback font.

    Args:
        size: Font size in pixels.
        font_path: Optional explicitly configured font file.

    Returns:
        A Pillow TrueType font.
    """
    candidates = [
        font_path,
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.truetype("DejaVuSans-Bold.ttf", size)


def _truncate(text: str, limit: int) -> str:
    """Shorten a label to the drawing limit.

    Args:
        text: Original label.
        limit: Maximum character count.

    Returns:
        Original or ellipsis-truncated label.
    """
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _node_label(node: FlowNode, value: float) -> str:
    """Format the text drawn beside a Sankey node."""
    return f"{_truncate(node.label, 22)} · {compact_token_count(value)}"


def _bezier(a: float, b: float, c: float, d: float, t: float) -> float:
    """Evaluate one coordinate of a cubic Bézier curve.

    Args:
        a: Start coordinate.
        b: First control coordinate.
        c: Second control coordinate.
        d: End coordinate.
        t: Position along the curve from zero to one.

    Returns:
        Interpolated coordinate.
    """
    return (
        (1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t**2 * c + t**3 * d
    )


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

    root_ids = sorted({path[0] for path in paths})
    root_colors = {
        node_id: PALETTE[index % len(PALETTE)] for index, node_id in enumerate(root_ids)
    }
    node_colors: dict[str, str] = {}
    node_totals: list[defaultdict[str, float]] = [defaultdict(float) for _ in stages]
    link_totals: list[defaultdict[tuple[str, str], float]] = [
        defaultdict(float) for _ in range(len(stages) - 1)
    ]
    link_colors: list[dict[tuple[str, str], str]] = [{} for _ in range(len(stages) - 1)]
    for path, value in paths.items():
        color = root_colors[path[0]]
        for index, node_id in enumerate(path):
            node_totals[index][node_id] += value
            node_colors.setdefault(node_id, color)
        for index in range(len(path) - 1):
            key = (path[index], path[index + 1])
            link_totals[index][key] += value
            link_colors[index].setdefault(key, color)

    font = _load_font(FONT_SIZE, font_path)
    label_bbox = font.getbbox("Ag国pq")
    label_height = label_bbox[3] - label_bbox[1]
    label_line_gap = max(LABEL_LINE_GAP, label_height + 8)
    stage_label_widths = [
        max(
            (
                font.getlength(_node_label(node_info[node_id], value))
                for node_id, value in totals.items()
            ),
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
        available = (
            height
            - 2 * VERTICAL_MARGIN
            - NODE_GAP * max(len(ordered) - 1, 0)
        )
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
    del root_ids, root_colors, node_colors, link_totals, link_colors, node_totals

    image = Image.new("RGB", (width, height), "#ffffff")
    link_draw = ImageDraw.Draw(image, "RGBA")
    for link in sorted(links, key=lambda item: item["value"], reverse=True):
        control = (link["x1"] - link["x0"]) * 0.48
        top: list[tuple[float, float]] = []
        bottom: list[tuple[float, float]] = []
        for step in range(31):
            t = step / 30
            x = _bezier(
                link["x0"],
                link["x0"] + control,
                link["x1"] - control,
                link["x1"],
                t,
            )
            top.append(
                (
                    x,
                    _bezier(
                        link["sy0"],
                        link["sy0"],
                        link["ty0"],
                        link["ty0"],
                        t,
                    ),
                )
            )
            bottom.append(
                (
                    x,
                    _bezier(
                        link["sy1"],
                        link["sy1"],
                        link["ty1"],
                        link["ty1"],
                        t,
                    ),
                )
            )
        color = link["color"].lstrip("#")
        rgb = tuple(int(color[offset : offset + 2], 16) for offset in (0, 2, 4))
        link_draw.polygon(
            top + list(reversed(bottom)),
            fill=(*rgb, round(link["alpha"] * 255)),
        )
    draw = ImageDraw.Draw(image)
    label_top = VERTICAL_MARGIN + label_height / 2
    label_bottom = height - VERTICAL_MARGIN - label_height / 2

    for stage, stage_positions in enumerate(positions):
        ordered_nodes = sorted(stage_positions.items(), key=lambda item: item[1]["y0"])
        label_centers: list[float] = []
        for _node_id, node in ordered_nodes:
            desired = (node["y0"] + node["y1"]) / 2
            label_centers.append(
                max(
                    desired,
                    label_centers[-1] + label_line_gap
                    if label_centers
                    else label_top,
                )
            )
        if label_centers and label_centers[-1] > label_bottom:
            label_centers[-1] = label_bottom
            for index in range(len(label_centers) - 2, -1, -1):
                label_centers[index] = min(
                    label_centers[index],
                    label_centers[index + 1] - label_line_gap,
                )

        for (node_id, node), label_center in zip(
            ordered_nodes, label_centers, strict=True
        ):
            x = node["x"]
            draw.rectangle(
                (x, node["y0"], x + NODE_WIDTH, node["y1"]),
                fill=node["color"],
                outline="#b7c0cc",
                width=1,
            )
            label_x = x + NODE_WIDTH + LABEL_PADDING
            label = _node_label(node_info[node_id], node["value"])
            draw.text(
                (label_x, label_center),
                label,
                font=font,
                fill="#374151",
                anchor="lm",
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "PNG")
    return RenderSummary(
        row_count=len(rows),
        node_count=node_count,
        link_count=link_count,
        width=width,
        height=height,
        pixel_count=pixel_count,
    )

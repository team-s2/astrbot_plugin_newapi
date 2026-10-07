"""Subscription quota overview rendered with Skia.

One line per channel. The weekly window sits on a date axis and the 5h window
on an hour axis, each drawn where it falls in time with dark = used and
light = remaining; an orange line marks now, and used quota past it is ahead
of pace and turns red. Reset cards get a third, narrow date axis at the end of
the row, one thin lane per card type; Grok, which has none, shows its monthly
dollar allowance there instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil, floor
from pathlib import Path

import skia

from ..core.quota import ChannelQuota, QuotaWindow, ResetPool
from .painter import UNIT_GAP, Painter

TZ = timezone(timedelta(hours=8))
DAY = 86400
HOUR = 3600

PANEL = 0xFFFFFFFF
RULE = 0xFFEDEFF3
GRID = 0xFFF1F2F5
INK = 0xFF1D2230
SUB = 0xFF6B7280
MUTED = 0xFFA3A9B5
TODAY = 0xFFF6F7FB
NOW = 0xFFF08A24
OVER = 0xFFE5484D
WARN = 0xFFEC8E2C
WEEK = 0xFF6B5FD3
WEEK_PALE = 0xFFE7E4F8
FIVE = 0xFF12998F
FIVE_PALE = 0xFFDDF1EE
CARD = 0xFF5B5BD6
CARD_LANE = 0xFFE1E1F7

WIDTH = 1216
MARGIN = 24
PAD = 20
LEFT = MARGIN + PAD
RIGHT = WIDTH - MARGIN - PAD
NAME_W = 192
# Right edges of the value cells, then the three time axes they introduce.
WEEK_VALUE = 294
WEEK_X0, WEEK_X1 = 308, 626
FIVE_VALUE = 694
FIVE_X0, FIVE_X1 = 708, 906
CARD_LABEL = 920
CARD_VALUE = 952
CARD_X0, CARD_X1 = 964, RIGHT
TITLE_H = 60
HEAD_H = 34
ROW_H = 52
LANE_DY = 9  # Zhipu's week and 5h card lanes sit this far above/below centre
CHIP_GAP = 10
BAR_H = 12


def date_text(timestamp: float, fmt: str = "%m/%d %H:%M") -> str:
    return datetime.fromtimestamp(timestamp, TZ).strftime(fmt)


def day_start(timestamp: float) -> float:
    offset = 8 * 3600
    return floor((timestamp + offset) / DAY) * DAY - offset


def duration_text(seconds: float) -> str:
    """Two most significant units, e.g. 4天21时, with narrow gaps around units."""
    minutes = max(1, int(seconds / 60))
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [(days, "天"), (hours, "时")] if days else [(hours, "时"), (mins, "分")]
    if not days and not hours:
        parts = [(mins, "分")]
    if len(parts) == 2 and parts[1][0] == 0:
        parts = parts[:1]
    return UNIT_GAP.join(f"{value}{UNIT_GAP}{unit}" for value, unit in parts)


def percent_text(value: float) -> str:
    if value >= 10 or value == 0:
        return f"{value:.0f}%"
    return f"{value:.1f}".rstrip("0").rstrip(".") + "%"


def card_bins(
    pool: ResetPool, start: float, bin_days: int
) -> list[tuple[int, int, float]]:
    """Cards grouped into ``bin_days``-day bins: (bin, count, earliest expiry)."""
    bins: dict[int, list[float]] = {}
    for stamp in pool.expires_at:
        day = round((day_start(stamp) - start) / DAY)
        bins.setdefault(day // bin_days, []).append(stamp)
    return [(index, len(stamps), min(stamps)) for index, stamps in sorted(bins.items())]


class Axis:
    """Linear time scale from ``start`` to ``end`` over pixels ``x0``..``x1``."""

    def __init__(self, start: float, end: float, x0: float, x1: float):
        self.start, self.end, self.x0, self.x1 = start, end, x0, x1

    def x(self, stamp: float) -> float:
        return self.x0 + (stamp - self.start) / (self.end - self.start) * (
            self.x1 - self.x0
        )

    def clamp(self, x: float) -> float:
        return min(self.x1, max(self.x0, x))


def week_axis(rows: list[ChannelQuota], now: float) -> Axis:
    """Whole local days covering every live weekly window and today."""
    stamps = [now]
    for row in rows:
        window = row.weekly
        if window and window.reset_at is not None and window.reset_at > now:
            stamps += [window.start_at, window.reset_at]
    start = day_start(min(stamps))
    end = day_start(max(stamps)) + DAY
    return Axis(start, max(end, start + 8 * DAY), WEEK_X0, WEEK_X1)


def five_axis(now: float) -> Axis:
    """Five hours either side of now: every live 5h window fits."""
    return Axis(now - 5 * HOUR, now + 5 * HOUR, FIVE_X0, FIVE_X1)


def card_axis(rows: list[ChannelQuota], now: float) -> Axis:
    """Whole local days from today (or the oldest listed card) to the last one."""
    stamps = [s for row in rows for pool in row.reset_pools for s in pool.expires_at]
    start = day_start(min(stamps + [now]))
    end = max(day_start(max(stamps + [now])) + DAY, start + 14 * DAY)
    return Axis(start, end, CARD_X0, CARD_X1)


def draw_name(p: Painter, cy: float, row: ChannelQuota):
    enabled = row.status == "启用"
    p.circle(LEFT + 4, cy, 4, 0xFF2F9E6E if enabled else MUTED)
    tag = p.measure("限流", 11, "bold") + 12 if row.limit_note else 0
    name = p.fit(row.name, NAME_W - 22 - tag, 15, "bold")
    width = p.text(LEFT + 16, cy, name, 15, INK if enabled else SUB, "bold")
    if tag:
        x = LEFT + 22 + width
        p.rect(x, cy - 9, x + tag, cy + 9, OVER, 9)
        p.text(x + tag / 2, cy, "限流", 11, PANEL, "bold", "center")
    provider = "智谱" if row.provider.startswith("智谱") else row.provider
    # "SuperGrok" already names its provider.
    plan = row.plan if provider in row.plan else f"{provider} {row.plan}".strip()
    parts = [f"#{row.channel_id}", plan]
    if not enabled:
        parts.append(row.status)
    p.text(LEFT + 16, cy + 19, p.fit(" · ".join(parts), NAME_W - 16, 12), 12, SUB)


def window_bar(
    p: Painter,
    window: QuotaWindow | None,
    cy: float,
    axis: Axis,
    now: float,
    used_color: int,
    pale: int,
):
    if window is None or window.reset_at is None or window.reset_at <= now:
        return
    raw0, raw1 = axis.x(window.start_at), axis.x(window.reset_at)
    x0, x1 = axis.clamp(raw0), axis.clamp(raw1)
    if x1 - x0 < 1:
        return
    top, bottom, radius = cy - BAR_H / 2, cy + BAR_H / 2, BAR_H / 2
    p.rect(x0, top, x1, bottom, pale, radius)
    if not window.used_percent:
        return
    used = raw0 + (raw1 - raw0) * min(window.used_percent, 100) / 100
    now_x = axis.x(now)
    p.canvas.save()
    p.canvas.clipRRect(p.rrect(x0, top, x1, bottom, radius), doAntiAlias=True)
    p.rect(x0, top, min(used, now_x), bottom, used_color)
    if used > now_x:
        p.rect(now_x, top, used, bottom, OVER)
    p.canvas.restore()


def value_cell(p: Painter, right: float, cy: float, window: QuotaWindow | None, now):
    if window is None:
        p.text(right, cy, "—", 14, MUTED, "bold", "right")
        return
    if window.used_percent is None:
        p.text(right, cy - 7, "?", 14, MUTED, "bold", "right")
    else:
        left = max(0.0, 100 - window.used_percent)
        color = OVER if left < 10 else INK
        p.text(right, cy - 7, percent_text(left), 14, color, "bold", "right")
    if window.reset_at is None:
        countdown = "未开始" if window.used_percent == 0 else "—"
    elif window.reset_at <= now:
        countdown = "待刷新"
    else:
        countdown = duration_text(window.reset_at - now)
    p.text(right, cy + 9, countdown, 11, SUB, "regular", "right")


def week_head(p: Painter, axis: Axis, cy: float, top: float, bottom: float, now):
    days = round((axis.end - axis.start) / DAY)
    today = round((day_start(now) - axis.start) / DAY)
    width = (axis.x1 - axis.x0) / days
    p.rect(axis.x0 + today * width, top, axis.x0 + (today + 1) * width, bottom, TODAY)
    step = ceil(44 / width)  # room for one MM/DD label
    for day in range(days + 1):
        x = axis.x0 + day * width
        labelled = (day - today) % step == 0
        if width >= 12 or labelled:
            p.line(x, top, x, bottom, GRID)
        if day < days and labelled:
            label = (
                "今天" if day == today else date_text(axis.start + day * DAY, "%m/%d")
            )
            p.text(x + width / 2, cy, label, 11, SUB, "regular", "center")


def five_head(p: Painter, axis: Axis, cy: float, top: float, bottom: float):
    hour = ceil(axis.start / HOUR) * HOUR
    while hour <= axis.end:
        x = axis.x(hour)
        p.line(x, top, x, bottom, GRID)
        p.line(x, top, x, top + 4, MUTED)
        if round(hour / HOUR) % 2 == 0:
            label = date_text(hour, "%H:00")
            if axis.x0 + 14 <= x <= axis.x1 - 14:
                p.text(x, cy, label, 11, SUB, "regular", "center")
        hour += HOUR


def now_line(p: Painter, axis: Axis, top: float, bottom: float, now: float):
    x = axis.x(now)
    if axis.x0 <= x <= axis.x1:
        p.line(x, top, x, bottom, NOW, 1.5)
        cap = skia.Path()
        cap.moveTo(x - 4, top - 5)
        cap.lineTo(x + 4, top - 5)
        cap.lineTo(x, top)
        cap.close()
        p.canvas.drawPath(cap, skia.Paint(AntiAlias=True, Color=NOW))


def pool_label(pool: ResetPool) -> str:
    if pool.label.startswith("周"):
        return "周"
    if pool.label.startswith("5h"):
        return "5h"
    return ""


def monthly_cell(p: Painter, cy: float, row: ChannelQuota, now):
    """Grok's monthly allowance: remaining share, then spend and countdown."""
    window = row.monthly
    if window is None:
        p.text(CARD_LABEL, cy, p.fit(row.reset_note or "—", RIGHT - CARD_LABEL, 12),
               12, OVER if row.reset_note else MUTED)
        return
    x = CARD_LABEL + p.text(CARD_LABEL, cy - 7, "月", 11, MUTED) + 6
    if window.used_percent is None:
        x += p.text(x, cy - 7, "?", 14, MUTED, "bold")
    else:
        left = max(0.0, 100 - window.used_percent)
        color = OVER if left < 10 else INK
        x += p.text(x, cy - 7, percent_text(left), 14, color, "bold")
    if row.reset_note:
        x += 10
        p.text(x, cy - 7, p.fit(row.reset_note, RIGHT - x, 11), 11, OVER)
    parts = [window.detail] if window.detail else []
    if window.reset_at is not None:
        parts.append(
            "待刷新" if window.reset_at <= now else duration_text(window.reset_at - now)
        )
    if parts:
        line = p.fit(" · ".join(parts), RIGHT - CARD_LABEL, 11)
        p.text(CARD_LABEL, cy + 9, line, 11, SUB)


def card_cell(p: Painter, cy: float, row: ChannelQuota, axis: Axis, now):
    """Count per card type, then expiry chips on the shared card axis."""
    if row.reset_note and not any(pool.count for pool in row.reset_pools):
        x = min(axis.clamp(axis.x(now)) + 8, RIGHT - 80)
        p.text(x, cy, p.fit(row.reset_note, RIGHT - x, 12), 12, OVER)
        return
    pools = row.reset_pools
    days = round((axis.end - axis.start) / DAY)
    day_w = (axis.x1 - axis.x0) / days
    bin_days = max(1, ceil(CHIP_GAP / day_w))
    now_x = axis.x(now)
    for index, pool in enumerate(pools):
        offset = 0 if len(pools) == 1 else (-LANE_DY if index == 0 else LANE_DY)
        y = cy + offset
        if len(pools) > 1:
            p.text(CARD_LABEL, y, pool_label(pool), 11, MUTED)
        count = "?" if pool.count is None else str(pool.count)
        color = INK if pool.count else MUTED
        p.text(CARD_VALUE, y, count, 13, color, "bold", "right")
        bins = card_bins(pool, axis.start, bin_days)
        if not bins:
            continue
        span = bin_days * day_w
        chips = [(axis.x0 + (i + 0.5) * span, n, soonest) for i, n, soonest in bins]
        p.line(min(now_x, chips[0][0]), y, chips[-1][0], y, CARD_LANE, 2)
        for x, n, soonest in chips:
            left = soonest - now
            color = OVER if left < DAY else WARN if left < 3 * DAY else CARD
            radius = 6.5 if n > 1 else 4
            p.circle(x, y, radius + 1.5, PANEL)
            p.circle(x, y, radius, color)
            if n > 1:
                p.text(x, y, str(n), 9, PANEL, "bold", "center")
            if 0 < left < DAY:
                # Away from the other lane: above the top one, below the bottom.
                ly = y + (radius + 6) * (1 if offset > 0 else -1)
                p.text(x, ly, duration_text(left), 9.5, OVER, "bold", "center")


def render_quota(
    rows: list[ChannelQuota], output: Path, now: float, font_path: Path | None = None
) -> None:
    """One line per supported channel; unsupported channel types are left out."""
    supported = [row for row in rows if not row.unsupported]
    visible = [row for row in supported if not row.issue]
    failed = [row for row in supported if row.issue]
    body = len(supported) * ROW_H or 64
    height = MARGIN + TITLE_H + HEAD_H + body + 8 + MARGIN
    if height > 20000:
        raise ValueError("渠道过多，单张额度图片过高")
    p = Painter(WIDTH, height, font_path)

    # Title and the one legend the bars need.
    title_y = MARGIN + 22
    width = p.text(MARGIN + 4, title_y, "订阅额度", 24, INK, "bold")
    p.text(MARGIN + 18 + width, title_y + 2, date_text(now), 14, SUB)
    x = WIDTH - MARGIN - 4
    for label, kind, color in (
        ("现在", "line", NOW),
        ("超出进度", "box", OVER),
        ("已用", "box", WEEK),
        ("剩余", "box", WEEK_PALE),
    ):
        x -= p.measure(label, 12)
        p.text(x, title_y + 2, label, 12, SUB)
        if kind == "line":
            p.line(x - 8, title_y - 5, x - 8, title_y + 9, color, 1.5)
            x -= 30
        else:
            p.rect(x - 20, title_y - 3, x - 6, title_y + 7, color, 3)
            x -= 36

    top = MARGIN + TITLE_H
    p.rect(MARGIN, top, WIDTH - MARGIN, height - MARGIN, PANEL, 14)
    head = top + HEAD_H / 2 + 1
    rows_top = top + HEAD_H
    rows_bottom = rows_top + len(visible) * ROW_H
    week, five = week_axis(visible, now), five_axis(now)
    cards = card_axis(visible, now)
    p.text(WEEK_VALUE, head, "周", 12, SUB, "bold", "right")
    p.text(FIVE_VALUE, head, "5h", 12, SUB, "bold", "right")
    p.text(CARD_VALUE, head, "重置卡", 12, SUB, "bold", "right")
    if visible:
        week_head(p, week, head, rows_top, rows_bottom, now)
        five_head(p, five, head, rows_top, rows_bottom)
        week_head(p, cards, head, rows_top, rows_bottom, now)
    y = rows_top
    for row in visible:
        p.line(LEFT, y, RIGHT, y, RULE)
        cy = y + ROW_H / 2
        draw_name(p, cy - 9, row)
        value_cell(p, WEEK_VALUE, cy, row.weekly, now)
        value_cell(p, FIVE_VALUE, cy, row.five_hour, now)
        window_bar(p, row.weekly, cy, week, now, WEEK, WEEK_PALE)
        window_bar(p, row.five_hour, cy, five, now, FIVE, FIVE_PALE)
        if row.provider != "Grok":
            card_cell(p, cy, row, cards, now)
        y += ROW_H
    if visible:
        for axis in (week, five, cards):
            now_line(p, axis, rows_top, rows_bottom, now)
    # Grok rows don't use the card axis: blank out its grid and now line first.
    for index, row in enumerate(visible):
        if row.provider == "Grok":
            row_top = rows_top + index * ROW_H
            row_bottom = row_top + ROW_H
            p.rect(CARD_LABEL - 6, row_top - 1, RIGHT, row_bottom + 1, PANEL)
            p.line(CARD_LABEL - 6, row_top, RIGHT, row_top, RULE)
            if row_bottom < rows_bottom or failed:
                p.line(CARD_LABEL - 6, row_bottom, RIGHT, row_bottom, RULE)
            monthly_cell(p, row_top + ROW_H / 2, row, now)
    for row in failed:
        p.line(LEFT, y, RIGHT, y, RULE)
        draw_name(p, y + ROW_H / 2 - 9, row)
        x = LEFT + NAME_W + 16
        p.text(x, y + ROW_H / 2, p.fit(row.issue, RIGHT - x, 13), 13, OVER)
        y += ROW_H
    if not supported:
        p.text(
            WIDTH / 2, y + 32, "暂无可展示的订阅额度", 15, MUTED, "regular", "center"
        )
    p.save(output)

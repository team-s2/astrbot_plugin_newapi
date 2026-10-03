"""Compact quota rows and reset-card events, rendered with Pillow."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from math import ceil, floor
from pathlib import Path

from PIL import Image, ImageDraw

from .flow_renderer import _load_font
from .quota import ChannelQuota, QuotaWindow, ResetPool

TZ = timezone(timedelta(hours=8))
BG = "#FFFFFF"
INK = "#202738"
MUTED = "#737D90"
LINE = "#E8EBF2"
PURPLE = "#7970CE"
PALE = "#EEECFA"
TEAL = "#319C91"
TEAL_PALE = "#E4F3EF"
ORANGE = "#D99335"
ERROR = "#BD5555"
WIDTH = 1800
LEFT = 40
RIGHT = WIDTH - LEFT
TIME_LEFT = 880
TIME_RIGHT = RIGHT - 24
DAY = 86400


def date_text(timestamp: float, fmt: str = "%m/%d %H:%M") -> str:
    return datetime.fromtimestamp(timestamp, TZ).strftime(fmt)


def countdown(reset_at: float | None, now: float) -> str:
    if reset_at is None:
        return "重置时间未知"
    seconds = reset_at - now
    if seconds <= 0:
        return "已到期 · 待上游更新"
    minutes = max(1, int(seconds / 60))
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} 天 {hours} 小时后重置"
    if hours:
        return f"{hours} 小时 {mins} 分钟后重置"
    return f"{mins} 分钟后重置"


class QuotaCanvas:
    def __init__(self, height: int, font_path: Path | None):
        self.image = Image.new("RGB", (WIDTH, height), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {
            size: _load_font(size, font_path)
            for size in (15, 16, 17, 18, 20, 22, 24, 28)
        }

    def text(self, xy, value, size=18, fill=INK):
        self.draw.text(xy, str(value), font=self.fonts[size], fill=fill, anchor="lt")

    def fit(self, value: str, width: float, size=18) -> str:
        value = " ".join(value.split())
        font = self.fonts[size]
        if font.getlength(value) <= width:
            return value
        while value and font.getlength(value + "…") > width:
            value = value[:-1]
        return value + "…"

    def rounded(self, box, fill, radius=8):
        self.draw.rounded_rectangle(box, radius=radius, fill=fill)

    def pos(self, stamp: float, origin: float, duration: float) -> float:
        return TIME_LEFT + (stamp - origin) / duration * (TIME_RIGHT - TIME_LEFT)

    def window_bar(
        self,
        window: QuotaWindow,
        y: int,
        now: float,
        origin: float,
        duration: float,
        color: str,
        pale: str,
    ):
        start, end = window.start_at, window.reset_at
        if start is None or end is None:
            self.text((TIME_LEFT, y + 3), "重置时间未知", 16, MUTED)
            return
        if end <= now:
            self.text((TIME_LEFT, y + 3), "窗口已到期 · 等待上游更新", 16, MUTED)
            return
        raw_left, raw_right = (
            self.pos(start, origin, duration),
            self.pos(end, origin, duration),
        )
        x1, x2 = max(TIME_LEFT, raw_left), min(TIME_RIGHT, raw_right)
        if x2 <= x1:
            return
        self.rounded((x1, y, x2, y + 22), pale)
        if window.used_percent is not None:
            used_end = min(
                x2,
                max(
                    x1,
                    raw_left
                    + (raw_right - raw_left) * min(window.used_percent, 100) / 100,
                ),
            )
            if used_end > x1:
                mask = Image.new("L", (TIME_RIGHT - TIME_LEFT + 1, 23))
                draw = ImageDraw.Draw(mask)
                draw.rounded_rectangle(
                    (x1 - TIME_LEFT, 0, x2 - TIME_LEFT, 22), radius=8, fill=255
                )
                draw.rectangle(
                    (used_end - TIME_LEFT, 0, TIME_RIGHT - TIME_LEFT + 1, 23), fill=0
                )
                self.image.paste(color, (TIME_LEFT, y), mask)
        x = self.pos(now, origin, duration)
        if TIME_LEFT <= x <= TIME_RIGHT:
            self.draw.line((x, y - 3, x, y + 25), fill=ORANGE, width=3)


def card_events(pool: ResetPool, now: float) -> list[tuple[float, str, str]]:
    """Group simultaneous expiries without filtering dates."""
    events = []
    for stamp, count in sorted(Counter(pool.expires_at).items()):
        label = f"x{count}"
        events.append((stamp, label, ERROR if stamp - now < DAY else ORANGE))
    return events


def render_quota(
    rows: list[ChannelQuota], output: Path, now: float, font_path: Path | None = None
) -> None:
    """Omit unsupported channels and collect failures in a compact footer."""
    supported = [row for row in rows if not row.unsupported]
    visible = [row for row in supported if not row.issue]
    errors = [
        f"#{row.channel_id} {row.name}：{row.issue}" for row in supported if row.issue
    ]
    errors.extend(
        f"#{row.channel_id} {row.name}：{row.reset_note}"
        for row in visible
        if row.reset_note
    )
    # Include weekly windows and available-card expiries, excluding usage history.
    stamps = [now - 7 * DAY, now + 14 * DAY]
    for row in visible:
        if row.weekly and row.weekly.reset_at is not None:
            stamps.extend((row.weekly.start_at, row.weekly.reset_at))
        for pool in row.reset_pools:
            stamps.extend(pool.expires_at)
    # Round to local calendar days and limit tick density, not the data range.
    offset = 8 * 3600
    origin = floor((min(stamps) + offset) / DAY) * DAY - offset
    end = ceil((max(stamps) + offset) / DAY) * DAY - offset
    duration = end - origin
    total_days = round(duration / DAY)
    tick_step = max(1, ceil(total_days / 6))
    tick_days = list(range(0, total_days + 1, tick_step))
    if total_days - tick_days[-1] >= tick_step / 2:
        tick_days.append(total_days)
    # Keep one baseline per card pool; only labels move above or below it.
    measure = _load_font(15, font_path)

    def event_layout(pool):
        lanes: list[list[tuple[float, float]]] = [[], []]
        placed = []
        for index, (stamp, label, color) in enumerate(card_events(pool, now)):
            x = TIME_LEFT + (stamp - origin) / duration * (TIME_RIGHT - TIME_LEFT)
            width = measure.getlength(label)
            label_x = max(TIME_LEFT, min(TIME_RIGHT - width, x - width / 2))
            bounds = (label_x, label_x + width)
            lane = index % 2
            # Dense clusters get extra label tiers, never extra timeline lines.
            while lane < len(lanes) and any(
                bounds[1] + 10 > left and bounds[0] < right + 10
                for left, right in lanes[lane]
            ):
                lane += 2
            while lane >= len(lanes):
                lanes.append([])
            lanes[lane].append(bounds)
            placed.append((lane, x, label_x, label, color))
        above = max((lane // 2 + 1 for lane, *_ in placed if lane % 2 == 0), default=0)
        below = max((lane // 2 + 1 for lane, *_ in placed if lane % 2 == 1), default=0)
        baseline = above * 22 + 4
        return placed, max(42, baseline + below * 22 + 14), baseline

    layouts = []
    for row in visible:
        windows = [
            window for window in (row.weekly, row.five_hour) if window is not None
        ]
        pools = list(row.reset_pools)
        if not pools and row.reset_count is not None:
            pools = [ResetPool("重置卡", row.reset_count)]
        pool_layouts = [event_layout(pool) for pool in pools]
        window_heights = [38 for window in windows]
        height = max(
            108, 28 + sum(window_heights) + sum(item[1] for item in pool_layouts)
        )
        layouts.append((row, windows, window_heights, pools, pool_layouts, height))
    height = (
        136
        + sum(item[-1] for item in layouts)
        + (52 + 28 * len(errors) if errors else 0)
        + 42
    )
    if not visible:
        height += 60
    if WIDTH * height > 60_000_000:
        raise ValueError("渠道过多，单张额度图片超过 6000 万像素")
    c = QuotaCanvas(height, font_path)
    c.text((LEFT + 20, 24), "订阅额度", 28)
    c.text((LEFT + 164, 32), date_text(now) + " · UTC+8", 17, MUTED)
    c.text(
        (TIME_LEFT, 28),
        "深色 = 已用    浅色 = 剩余    橙线 = 现在    ◆ 到期",
        17,
        MUTED,
    )
    c.text((LEFT + 20, 92), "渠道 / 套餐", 17, MUTED)
    c.text((390, 92), "额度余量 / 重置卡", 17, MUTED)
    for day in tick_days:
        x = c.pos(origin + day * DAY, origin, duration)
        label = date_text(origin + day * DAY, "%m/%d")
        c.text((x - c.fonts[16].getlength(label) / 2, 93), label, 16, MUTED)
    now_x = c.pos(now, origin, duration)
    c.text((now_x - 16, 68), "现在", 16, ORANGE)
    y = 128
    if not visible:
        c.text((LEFT + 20, y + 20), "暂无可展示的订阅额度", 20, MUTED)
        y += 60
    for row, windows, window_heights, pools, pool_layouts, row_height in layouts:
        c.draw.line((LEFT + 20, y, RIGHT - 20, y), fill=LINE)
        c.text((LEFT + 20, y + 19), c.fit(row.name, 310, 22), 22)
        meta = f"#{row.channel_id} · {row.provider} · {row.plan}"
        c.text((LEFT + 20, y + 51), c.fit(meta, 310, 16), 16, MUTED)
        status = row.status + (" · " + row.limit_note if row.limit_note else "")
        c.text(
            (LEFT + 20, y + 77),
            c.fit(status, 310, 16),
            16,
            ERROR if row.limit_note else MUTED,
        )
        line_y = y + 18
        for window, line_height in zip(windows, window_heights):
            five = window is row.five_hour
            color, pale = (TEAL, TEAL_PALE) if five else (PURPLE, PALE)
            c.text((390, line_y + 2), "5 小时" if five else "周限额", 18, color)
            remaining = (
                max(0, 100 - window.used_percent)
                if window.used_percent is not None
                else None
            )
            value = (
                (f"{remaining:.1f}".rstrip("0").rstrip(".") + "%")
                if remaining is not None
                else "未知"
            )
            c.text(
                (475, line_y),
                value,
                22,
                ERROR if remaining is not None and remaining <= 10 else INK,
            )
            c.text((561, line_y + 4), "剩余", 16, MUTED)
            c.text(
                (617, line_y + 4),
                c.fit(countdown(window.reset_at, now), 245, 16),
                16,
                MUTED,
            )
            if five:
                # Five-hour rows share a relative scale centered on now.
                local_origin = now - 5 * 3600
                c.window_bar(window, line_y, now, local_origin, 10 * 3600, color, pale)
            else:
                for day in tick_days:
                    x = c.pos(origin + day * DAY, origin, duration)
                    c.draw.line((x, line_y - 2, x, line_y + 25), fill=LINE)
                c.window_bar(window, line_y, now, origin, duration, color, pale)
            line_y += line_height
        for pool, (events, pool_height, baseline) in zip(pools, pool_layouts):
            c.text((390, line_y + 3), pool.label, 17, MUTED)
            c.text(
                (510, line_y + 1),
                f"{pool.count} 张可用" if pool.count is not None else "数量未知",
                18,
            )
            if events:
                event_y = line_y + baseline
                c.draw.line((TIME_LEFT, event_y, TIME_RIGHT, event_y), fill=LINE)
                c.draw.line(
                    (now_x, event_y - 8, now_x, event_y + 8), fill=ORANGE, width=2
                )
                for lane, x, label_x, label, color in events:
                    label_y = (
                        event_y - 22 * (lane // 2 + 1)
                        if lane % 2 == 0
                        else event_y + 9 + 22 * (lane // 2)
                    )
                    if lane >= 2:
                        connector_y = label_y + 17 if lane % 2 == 0 else label_y - 2
                        c.draw.line((x, event_y, x, connector_y), fill=LINE)
                    c.draw.polygon(
                        (
                            (x, event_y - 5),
                            (x + 5, event_y),
                            (x, event_y + 5),
                            (x - 5, event_y),
                        ),
                        fill=color,
                    )
                    c.text((label_x, label_y), label, 15, color)
            elif not pool.expires_at:
                text = "暂无可用重置卡" if pool.count == 0 else "上游未提供有效期明细"
                c.text((TIME_LEFT, line_y + 4), text, 16, MUTED)
            line_y += pool_height
        y += row_height
    if errors:
        c.draw.line((LEFT + 20, y, RIGHT - 20, y), fill=LINE)
        c.text((LEFT + 20, y + 16), "查询异常", 17, ERROR)
        y += 48
        for error in errors:
            c.text((LEFT + 20, y), c.fit(error, WIDTH - 2 * LEFT - 40, 16), 16, ERROR)
            y += 28
    c.text(
        (LEFT + 20, y + 12),
        "周窗口与重置卡共用日期轴；5 小时窗口使用独立小时轴。窗口起点按周期推算，查询不会消耗重置卡。",
        15,
        MUTED,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    c.image.crop((LEFT, 12, RIGHT, min(height, y + 46))).save(
        output, "PNG", optimize=True
    )

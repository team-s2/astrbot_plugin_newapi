"""Compact quota table with aligned weekly windows (Pillow only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from .flow_renderer import _load_font
from .quota import ChannelQuota, QuotaWindow

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
LEFT = 48
RIGHT = WIDTH - LEFT
TIME_LEFT = 780
TIME_RIGHT = 1475


def date_text(timestamp: float, fmt: str = "%m/%d %H:%M") -> str:
    return datetime.fromtimestamp(timestamp, TZ).strftime(fmt)


def countdown(reset_at: float | None, now: float) -> str:
    if reset_at is None:
        return "重置时间未知"
    seconds = reset_at - now
    if seconds <= 0:
        return "窗口已到期 · 等待上游更新"
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
            for size in (15, 16, 17, 18, 20, 22, 24, 34)
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

    def wrap(self, xy, value: str, width: int, size=18, lines=2, fill=INK):
        value = " ".join(value.split())
        x, y = xy
        for index in range(lines):
            if not value:
                break
            if index == lines - 1:
                self.text((x, y), self.fit(value, width, size), size, fill)
                break
            length = 1
            while (
                length <= len(value)
                and self.fonts[size].getlength(value[:length]) <= width
            ):
                length += 1
            length = max(1, length - 1)
            self.text((x, y), value[:length], size, fill)
            value = value[length:]
            y += size + 8

    def rounded(self, box, fill, radius=10, outline=None):
        self.draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)

    def usage(self, window: QuotaWindow | None, x: int, y: int, *, major=False):
        if window is None:
            self.text((x, y + 8), "上游未提供此额度", 18, MUTED)
            return
        size = 34 if major else 24
        if window.used_percent is None:
            self.text((x, y + 6), "用量未知", 24, MUTED)
        else:
            remaining = max(0, 100 - window.used_percent)
            color = ERROR if remaining <= 10 else INK
            value = f"{remaining:.1f}".rstrip("0").rstrip(".") + "%"
            self.text((x, y), value, size, color)
            offset = self.fonts[size].getlength(value) + 12
            self.text((x + offset, y + (17 if major else 6)), "剩余", 17, MUTED)

    def window_bar(
        self, window: QuotaWindow, y: int, now: float, axis_start: float | None = None
    ):
        """Dark fill is consumed quota; the amber tick is elapsed wall time."""
        weekly = axis_start is not None
        left, right = TIME_LEFT, TIME_RIGHT
        duration = 16 * 86400 if weekly else window.duration
        start = window.start_at
        end = window.reset_at
        if start is None or end is None:
            self.text((left, y + 8), "未返回重置时间，无法定位当前窗口", 17, MUTED)
            return
        if end <= now:
            self.text(
                (left, y + 8),
                f"窗口已到期 · {date_text(end)} · 等待上游更新",
                17,
                ORANGE,
            )
            return
        origin = axis_start if weekly else start

        def pos(timestamp):
            return left + (timestamp - origin) / duration * (right - left)

        raw_x1, raw_x2 = pos(start), pos(end)
        x1, x2 = max(left, raw_x1), min(right, raw_x2)
        if x2 <= x1:
            self.text((left, y + 8), "窗口超出时间轴范围", 17, MUTED)
            return
        color, pale = (PURPLE, PALE) if weekly else (TEAL, TEAL_PALE)
        if window.used_percent is None:
            pale = "#EDF0F4"
        self.rounded((x1, y, x2, y + 28), pale, 14)
        if window.used_percent is None:
            self.text((x1 + 12, y + 5), "用量未知", 16, MUTED)
        if window.used_percent is not None:
            used_end = raw_x1 + (raw_x2 - raw_x1) * min(window.used_percent, 100) / 100
            used_end = min(x2, max(x1, used_end))
            if used_end > x1:
                # Clip the fill to the pill, including tiny nonzero percentages.
                mask = Image.new("L", (right - left + 1, 29))
                ImageDraw.Draw(mask).rounded_rectangle(
                    (x1 - left, 0, x2 - left, 28), radius=14, fill=255
                )
                ImageDraw.Draw(mask).rectangle(
                    (used_end - left, 0, right - left + 1, 29), fill=0
                )
                self.image.paste(color, (left, y), mask)
        now_x = pos(now)
        if left <= now_x <= right and start <= now <= end:
            self.draw.line((now_x, y - 5, now_x, y + 33), fill=ORANGE, width=3)
        if weekly:
            self.text(
                (left, y + 43), f"{date_text(start)}  →  {date_text(end)}", 17, MUTED
            )
        else:
            label = date_text(end) + " 结束"
            self.text(
                (right - self.fonts[16].getlength(label), y - 26), label, 16, MUTED
            )


def render_quota(
    rows: list[ChannelQuota],
    output: Path,
    now: float,
    font_path: Path | None = None,
) -> None:
    """Render only the compact table body; window dates use UTC+8."""
    heights = []
    for row in rows:
        weekly_detail_height = 20 if row.weekly and row.weekly.detail else 0
        if row.issue:
            heights.append(124)
        elif row.provider == "智谱 Coding Plan" and row.five_hour is not None:
            heights.append(
                176 + weekly_detail_height + (22 if row.five_hour.detail else 0)
            )
        elif row.reset_count is not None and row.reset_note:
            heights.append(138 + weekly_detail_height)
        else:
            heights.append(124 + weekly_detail_height)
    height = 76 + (sum(heights) if rows else 70) + 24
    if WIDTH * height > 60_000_000:
        raise ValueError("渠道过多，单张额度图片超过 6000 万像素")
    c = QuotaCanvas(height, font_path)
    c.text((72, 36), "渠道 / 套餐", 18, MUTED)
    c.text((415, 36), "额度余量", 18, MUTED)
    c.text((1520, 36), "主动重置", 18, MUTED)
    midnight = datetime.fromtimestamp(now, TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    axis_start = midnight.timestamp() - 8 * 86400
    day_width = (TIME_RIGHT - TIME_LEFT) / 16
    for day in range(0, 17, 2):
        x = TIME_LEFT + day * day_width
        label = date_text(axis_start + day * 86400, "%m/%d")
        c.text((x - c.fonts[15].getlength(label) / 2, 39), label, 15, MUTED)
    y = 76
    if not rows:
        c.text((72, y + 16), "当前实例没有渠道", 20, MUTED)
    for row, row_height in zip(rows, heights):
        c.draw.line((LEFT + 24, y, RIGHT - 24, y), fill=LINE)
        c.wrap((72, y + 14), row.name, 305, 22)
        name_lines = 1 if c.fonts[22].getlength(row.name) <= 305 else 2
        meta_y = y + 47 + (30 if name_lines == 2 else 0)
        meta = f"#{row.channel_id} · {row.provider}"
        if row.plan:
            meta += f" · {row.plan}"
        c.text((72, meta_y), c.fit(meta, 305, 17), 17, MUTED)
        status = row.status + (f" · {row.limit_note}" if row.limit_note else "")
        c.text(
            (72, meta_y + 27),
            c.fit(status, 305, 16),
            16,
            ERROR if row.limit_note else MUTED,
        )

        if row.issue:
            color = MUTED if row.unsupported else ERROR
            c.rounded(
                (405, y + 14, TIME_RIGHT, y + row_height - 14),
                "#F5F6F9" if row.unsupported else "#FFF3F1",
            )
            c.text(
                (425, y + 29),
                "暂不支持" if row.unsupported else "额度读取失败",
                20,
                color,
            )
            c.wrap((425, y + 65), row.issue, 1020, 17, 2, color)
        else:
            c.text((415, y + 12), "周额度", 17, PURPLE)
            c.usage(row.weekly, 415, y + 38, major=True)
            if row.weekly:
                c.text((415, y + 83), countdown(row.weekly.reset_at, now), 16, MUTED)
                for day in range(17):
                    x = TIME_LEFT + day * day_width
                    c.draw.line((x, y + 22, x, y + 83), fill=LINE)
                c.window_bar(row.weekly, y + 43, now, axis_start)
                if row.weekly.detail:
                    c.text(
                        (TIME_LEFT, y + 110),
                        c.fit(row.weekly.detail, 690, 15),
                        15,
                        MUTED,
                    )
            else:
                c.text((TIME_LEFT, y + 52), "上游未提供周窗口", 17, MUTED)

            # Codex rows contain only the weekly pool. The second line is for
            # the independently resetting Zhipu five-hour quota.
            if row.provider == "智谱 Coding Plan" and row.five_hour is not None:
                extra = 20 if row.weekly and row.weekly.detail else 0
                c.text((415, y + 139 + extra), "5 小时", 17, TEAL)
                c.usage(row.five_hour, 505, y + 132 + extra)
                c.text(
                    (TIME_LEFT, y + 111 + extra),
                    "5h · " + countdown(row.five_hour.reset_at, now),
                    16,
                    MUTED,
                )
                c.window_bar(row.five_hour, y + 137 + extra, now)
                if row.five_hour.detail:
                    c.text(
                        (415, y + 173 + extra),
                        c.fit(row.five_hour.detail, 340, 15),
                        15,
                        MUTED,
                    )

        if row.issue:
            y += row_height
            continue
        if row.reset_count is not None:
            count = str(row.reset_count)
            c.text((1520, y + 30), count, 34)
            c.text(
                (1520 + c.fonts[34].getlength(count) + 9, y + 45), "次剩余", 17, MUTED
            )
            if row.reset_note:
                c.wrap((1520, y + 81), row.reset_note, 205, 16, 2, MUTED)
        elif row.provider == "Codex":
            c.text(
                (1520, y + 36), "读取失败" if row.reset_failed else "未提供", 22, MUTED
            )
            if row.reset_failed:
                c.wrap((1520, y + 76), row.reset_note, 205, 16, 2, MUTED)
        y += row_height

    output.parent.mkdir(parents=True, exist_ok=True)
    c.image.crop((LEFT, 20, RIGHT, height - 12)).save(output, "PNG", optimize=True)

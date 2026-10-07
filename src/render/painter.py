"""Shared Skia drawing helpers: CJK font loading and a small text-aware canvas."""

from __future__ import annotations

import re
from pathlib import Path

import skia

INK = 0xFF1D2230
# Noto has no glyph for U+2005, so Painter draws it as a narrow gap instead;
# renderers use it around 天/时/分 to set units slightly apart from numbers.
UNIT_GAP = "\u2005"
GAP_EM = 1 / 6

# Debian's fonts-noto-cjk ships only these two weights, so the design uses two.
WEIGHTS = {"regular": 400, "bold": 700}
NOTO_DIRS = (
    Path("/usr/share/fonts/opentype/noto"),
    Path("/usr/share/fonts/truetype/noto"),
    Path("/usr/share/fonts/noto-cjk"),
)
FAMILIES = ("Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei")


def load_typefaces(font_path: Path | None) -> dict[str, skia.Typeface]:
    """Prefer the configured file, then Noto Sans CJK SC (Regular and Bold)."""
    if font_path and font_path.is_file():
        face = skia.Typeface.MakeFromFile(str(font_path))
        if face:
            return dict.fromkeys(WEIGHTS, face)
    faces: dict[str, skia.Typeface] = {}
    for key in WEIGHTS:
        for folder in NOTO_DIRS:
            path = folder / f"NotoSansCJK-{key.title()}.ttc"
            if not path.is_file():
                continue
            # The collection holds JP/KR/SC/TC/HK faces; pick SC by name.
            for index in range(10):
                face = skia.Typeface.MakeFromFile(str(path), index)
                if face and face.getFamilyName() == "Noto Sans CJK SC":
                    faces[key] = face
                    break
            if key in faces:
                break
    missing = [key for key in WEIGHTS if key not in faces]
    # Only touch fontconfig when Noto is absent; it is slow and noisy.
    manager = skia.FontMgr() if missing else None
    for key in missing:
        weight = WEIGHTS[key]
        style = skia.FontStyle(weight, 5, skia.FontStyle.kUpright_Slant)
        for family in FAMILIES:
            if face := manager.matchFamilyStyle(family, style):
                faces[key] = face
                break
        else:
            faces[key] = (
                manager.matchFamilyStyleCharacter("", style, ["zh-CN"], ord("中"))
                or skia.Typeface.MakeDefault()
            )
    return faces


class Painter:
    """Skia canvas in logical pixels, with text centred on a line."""

    def __init__(
        self,
        width: int,
        height: int,
        font_path: Path | None,
        scale: float = 2,
        background: int = 0xFFF4F5F7,
    ):
        self.surface = skia.Surface(round(width * scale), round(height * scale))
        self.canvas = self.surface.getCanvas()
        self.canvas.scale(scale, scale)
        self.canvas.clear(background)
        self.faces = load_typefaces(font_path)
        self.fonts: dict[tuple[str, float], skia.Font] = {}

    def font(self, size: float, weight: str) -> skia.Font:
        if (weight, size) not in self.fonts:
            font = skia.Font(self.faces[weight], size)
            font.setEdging(skia.Font.Edging.kAntiAlias)
            font.setSubpixel(True)
            self.fonts[weight, size] = font
        return self.fonts[weight, size]

    def measure(self, value: str, size: float, weight: str = "regular") -> float:
        font = self.font(size, weight)
        pieces = value.split(UNIT_GAP)
        gaps = (len(pieces) - 1) * size * GAP_EM
        return sum(font.measureText(piece) for piece in pieces) + gaps

    def fit(self, value: str, width: float, size: float, weight="regular") -> str:
        value = re.sub(r"[ \t\r\n]+", " ", value).strip()
        if self.measure(value, size, weight) <= width:
            return value
        while value and self.measure(value + "…", size, weight) > width:
            value = value[:-1]
        return value + "…"

    def text(self, x, cy, value, size, color=INK, weight="regular", align="left"):
        """Draw text vertically centred on ``cy`` and return its width."""
        font = self.font(size, weight)
        width = self.measure(value, size, weight)
        if align == "right":
            x -= width
        elif align == "center":
            x -= width / 2
        metrics = font.getMetrics()
        baseline = cy - (metrics.fAscent + metrics.fDescent) / 2
        paint = skia.Paint(AntiAlias=True, Color=color)
        for piece in value.split(UNIT_GAP):
            self.canvas.drawString(piece, x, baseline, font, paint)
            x += font.measureText(piece) + size * GAP_EM
        return width

    def rrect(self, x0, y0, x1, y1, radius) -> skia.RRect:
        return skia.RRect.MakeRectXY(skia.Rect.MakeLTRB(x0, y0, x1, y1), radius, radius)

    def rect(self, x0, y0, x1, y1, color, radius=0.0):
        paint = skia.Paint(AntiAlias=True, Color=color)
        self.canvas.drawRRect(self.rrect(x0, y0, x1, y1, radius), paint)

    def line(self, x0, y0, x1, y1, color, width=1.0):
        paint = skia.Paint(AntiAlias=True, Color=color, StrokeWidth=width)
        paint.setStrokeCap(skia.Paint.kRound_Cap)
        self.canvas.drawLine(x0, y0, x1, y1, paint)

    def circle(self, x, y, radius, color):
        self.canvas.drawCircle(x, y, radius, skia.Paint(AntiAlias=True, Color=color))

    def save(self, output: Path):
        output.parent.mkdir(parents=True, exist_ok=True)
        data = self.surface.makeImageSnapshot().encodeToData(skia.kPNG, 100)
        if data is None:
            raise OSError("额度图片 PNG 编码失败")
        output.write_bytes(bytes(data))

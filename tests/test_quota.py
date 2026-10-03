"""Quota normalization and image regressions without the AstrBot runtime."""

import importlib.util
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_quota_test_plugin"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
quota = importlib.import_module(f"{PACKAGE}.quota")
renderer = importlib.import_module(f"{PACKAGE}.quota_renderer")

NOW = 1791048000


class QuotaTests(unittest.TestCase):
    def codex(self, usage, credits=None):
        return quota.normalize_quota(
            {"id": 1, "name": "Codex", "type": 57, "status": 1},
            usage,
            credits,
            NOW,
        )

    def capture(self, rows):
        texts = []
        original = renderer.QuotaCanvas.text

        def record(canvas, xy, value, *args, **kwargs):
            texts.append(str(value))
            return original(canvas, xy, value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "quota.png"
            with patch.object(renderer.QuotaCanvas, "text", record):
                renderer.render_quota(rows, output, NOW)
            with Image.open(output) as image:
                self.assertGreater(image.height, 100)
                image.verify()
        return "\n".join(texts)

    def test_codex_both_windows_render(self):
        row = self.codex(
            {
                "plan_type": "plus",
                "rate_limit": {
                    "primary_window": {
                        "limit_window_seconds": 18000,
                        "used_percent": 100,
                        "reset_at": NOW + 3600,
                    },
                    "secondary_window": {
                        "limit_window_seconds": 604800,
                        "used_percent": 35,
                        "reset_at": NOW + 86400,
                    },
                },
            }
        )
        text = self.capture([row])
        self.assertIn("5 小时", text)
        self.assertIn("周限额", text)
        self.assertIn("0%", text)
        self.assertIn("65%", text)
        self.assertIn("1 小时 0 分钟后重置", text)

    def test_weekly_only_has_no_five_hour_placeholder(self):
        row = self.codex(
            {
                "rate_limit": {
                    "primary_window": {
                        "limit_window_seconds": 604800,
                        "used_percent": 0,
                        "reset_at": NOW + 86400,
                    }
                }
            }
        )
        self.assertIsNone(row.five_hour)
        self.assertNotIn("5 小时\n", self.capture([row]))

    def test_codex_cards_keep_only_available_expiries(self):
        row = self.codex(
            {},
            {
                "available_count": 2,
                "credits": [
                    {"status": "available", "expires_at": "2026-10-10T00:00:00Z"},
                    {"status": "available", "expires_at": "bad"},
                    {
                        "status": "redeemed",
                        "expires_at": "2026-10-11T00:00:00Z",
                        "redeemed_at": "2026-10-01T00:00:00Z",
                    },
                ],
            },
        )
        pool = row.reset_pools[0]
        self.assertEqual(pool.count, 2)
        self.assertEqual(len(pool.expires_at), 1)
        self.assertIsNotNone(pool.last_used_at)

    def test_zhipu_card_pools_and_milliseconds(self):
        row = quota.normalize_quota(
            {"id": 2, "type": 100},
            {
                "reset": {
                    "available_five_hour_resets": [],
                    "available_week_resets": [{"expire_at": (NOW + 86400) * 1000}] * 4,
                    "latest_week_reset_history": {"used_at": (NOW - 3600) * 1000},
                }
            },
            None,
            NOW,
        )
        week, five = row.reset_pools
        self.assertEqual((week.count, five.count), (4, 0))
        self.assertEqual(week.last_used_at, NOW - 3600)
        self.assertEqual(len(renderer.card_events(week, NOW)), 1)
        self.assertIn("x4", renderer.card_events(week, NOW)[0][1])

    def test_credit_failure_retains_usage_count(self):
        row = self.codex(
            {"rate_limit_reset_credits": {"available_count": 3}}, RuntimeError("401")
        )
        self.assertEqual(row.reset_pools[0].count, 3)
        self.assertTrue(row.reset_failed)
        self.assertIn("401", row.reset_note)

    def test_unsupported_hidden_and_failures_at_end(self):
        unsupported = quota.ChannelQuota(
            3, "HIDDEN_CHANNEL", "Other", "启用", unsupported=True, issue="UNSUPPORTED"
        )
        failed = quota.ChannelQuota(
            4, "FAILED_CHANNEL", "Codex", "启用", issue="HTTP 401"
        )
        text = self.capture([unsupported, failed])
        self.assertNotIn("HIDDEN_CHANNEL", text)
        self.assertNotIn("UNSUPPORTED", text)
        self.assertIn("查询异常\n#4 FAILED_CHANNEL：HTTP 401", text)

    def test_invalid_dates_and_empty_image(self):
        for value in (True, -1, float("inf"), 1e20, "bad", "2026-10-04"):
            self.assertIsNone(quota.reset_timestamp(value))
        self.assertIn("暂无可展示", self.capture([]))

    def test_zhipu_token_details_do_not_affect_image_or_height(self):
        week = quota.QuotaWindow("周额度", 35, 604800, NOW + 86400)
        five = quota.QuotaWindow("5 小时", 20, 18000, NOW + 3600)
        row = quota.ChannelQuota(
            2, "Zhipu", "智谱 Coding Plan", "启用", weekly=week, five_hour=five
        )
        with_tokens = replace(
            row,
            weekly=replace(week, detail="已用 3,500 / 10,000 tokens"),
            five_hour=replace(five, detail="已用 400 / 2,000 tokens"),
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("plain.png", "tokens.png")]
            for candidate, path in zip((row, with_tokens), paths):
                renderer.render_quota([candidate], path, NOW)
            self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())
        text = self.capture([with_tokens])
        self.assertNotIn("tokens", text)
        self.assertIn("65%", text)
        self.assertIn("80%", text)

    def test_close_card_expiries_share_one_timeline(self):
        row = quota.ChannelQuota(
            1,
            "Codex",
            "Codex",
            "启用",
            reset_pools=(
                quota.ResetPool(
                    "全额重置卡",
                    4,
                    tuple(NOW + offset for offset in (60, 120, 180, 240)),
                ),
            ),
        )
        lines = []
        original = renderer.ImageDraw.ImageDraw.line

        def record(draw, xy, *args, **kwargs):
            lines.append(xy)
            return original(draw, xy, *args, **kwargs)

        with patch.object(renderer.ImageDraw.ImageDraw, "line", record):
            text = self.capture([row])
        baselines = [
            xy
            for xy in lines
            if len(xy) == 4
            and xy[0] == renderer.TIME_LEFT
            and xy[2] == renderer.TIME_RIGHT
            and xy[1] == xy[3]
        ]
        self.assertEqual(len(baselines), 1)
        self.assertEqual(text.splitlines().count("x1"), 4)

    def test_used_card_history_does_not_affect_image_or_axis(self):
        pool = quota.ResetPool("全额重置卡", 1, (NOW + 86400,))
        row = quota.ChannelQuota(1, "Codex", "Codex", "启用", reset_pools=(pool,))
        old_history = replace(
            row, reset_pools=(replace(pool, last_used_at=NOW - 365 * 86400),)
        )
        # Identical pixels also verify that history cannot change date ticks,
        # the axis origin, event positions or row height.
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("plain.png", "history.png")]
            for candidate, path in zip((row, old_history), paths):
                renderer.render_quota([candidate], path, NOW)
            self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())
        text = self.capture([old_history])
        self.assertNotIn("上次", text)
        self.assertNotIn("已使用", text)

    def test_all_card_dates_and_minimal_labels(self):
        day = 86400
        pool = quota.ResetPool(
            "重置卡",
            6,
            (
                NOW - 7 * day - 1,
                NOW - 7 * day,
                NOW + day,
                NOW + day,
                NOW + 14 * day,
                NOW + 14 * day + 1,
            ),
        )
        events = renderer.card_events(pool, NOW)
        self.assertEqual(
            [event[0] for event in events],
            [
                NOW - 7 * day - 1,
                NOW - 7 * day,
                NOW + day,
                NOW + 14 * day,
                NOW + 14 * day + 1,
            ],
        )
        self.assertEqual([event[1] for event in events], ["x1", "x1", "x2", "x1", "x1"])

    def test_out_of_range_cards_and_unknown_quota_render(self):
        row = quota.ChannelQuota(
            1,
            "Unknown",
            "Codex",
            "启用",
            weekly=quota.QuotaWindow("周额度", None, 604800, None),
            reset_pools=(
                quota.ResetPool(
                    "全额重置卡",
                    3,
                    (NOW - 90 * 86400, NOW + 90 * 86400, NOW + 90 * 86400 + 1),
                ),
            ),
        )
        text = self.capture([row])
        self.assertIn("重置时间未知", text)
        self.assertNotIn("←", text)
        self.assertNotIn("→", text)
        self.assertNotIn("上游未提供有效期明细", text)
        self.assertEqual(len(renderer.card_events(row.reset_pools[0], NOW)), 3)
        self.assertEqual(text.splitlines().count("x1"), 3)


if __name__ == "__main__":
    unittest.main()

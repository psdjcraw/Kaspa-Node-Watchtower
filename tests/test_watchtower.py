import json
import tempfile
import unittest
from pathlib import Path

import watchtower


class WatchtowerUnitTests(unittest.TestCase):
    def test_positive_int_falls_back_for_invalid_values(self):
        self.assertEqual(watchtower.positive_int("25", 100), 25)
        self.assertEqual(watchtower.positive_int(0, 100), 100)
        self.assertEqual(watchtower.positive_int("-1", 100), 100)
        self.assertEqual(watchtower.positive_int("nope", 100), 100)

    def test_prune_jsonl_keeps_latest_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "items.jsonl"
            watchtower.save_jsonl(path, [{"n": 1}, {"n": 2}, {"n": 3}])

            pruned = watchtower.prune_jsonl(path, 2)

            self.assertEqual(pruned, 1)
            self.assertEqual(watchtower.load_jsonl(path), [{"n": 2}, {"n": 3}])

    def test_parse_relay_accepted(self):
        lines = [
            "2026-06-05 16:10:00.000+09:00 INFO Accepted 2 blocks from peer via relay",
            "2026-06-05 16:10:01.000+09:00 INFO unrelated",
            "2026-06-05 16:10:02.000+09:00 INFO Accepted 1 block from peer via relay",
        ]

        accepted = watchtower.parse_relay_accepted(lines)

        self.assertEqual([item.blocks for item in accepted], [2, 1])
        self.assertEqual(accepted[0].timestamp.isoformat(), "2026-06-05T16:10:00+09:00")

    def test_benchmark_summary_computes_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "benchmarks.jsonl"
            items = [
                {
                    "checked_at": "2026-06-05T10:00:00+09:00",
                    "virtual_daa_score": 100,
                    "block_count": 200,
                    "relay_blocks_in_window": 50,
                    "progress_window_minutes": 10,
                    "disk_free_gb": 300,
                    "peer_count": 8,
                    "active_peers": 8,
                    "is_synced": True,
                    "status": "ok",
                    "severity": "ok",
                },
                {
                    "checked_at": "2026-06-05T11:00:00+09:00",
                    "virtual_daa_score": 160,
                    "block_count": 260,
                    "relay_blocks_in_window": 70,
                    "progress_window_minutes": 10,
                    "disk_free_gb": 299.5,
                    "peer_count": 8,
                    "active_peers": 8,
                    "is_synced": True,
                    "status": "ok",
                    "severity": "ok",
                },
            ]
            watchtower.save_jsonl(path, items)

            summary = watchtower.build_benchmark_summary(path, limit=100)

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["snapshots"], 2)
            self.assertEqual(summary["daa_delta"], 60)
            self.assertEqual(summary["block_delta"], 60)
            self.assertEqual(summary["relay_rate"], "6.00/min")
            self.assertEqual(json.loads(summary["severity_counts"]), {"ok": 2})


if __name__ == "__main__":
    unittest.main()

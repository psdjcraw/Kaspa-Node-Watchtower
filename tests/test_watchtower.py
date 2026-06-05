import json
import copy
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

    def test_prometheus_metrics_include_extended_grpc_values(self):
        report = {
            "node_name": "test-node",
            "status": "ok",
            "severity": "ok",
            "checks": [{"name": "grpc_metrics", "ok": True}],
            "grpc_metrics": {
                "peer_count": 8,
                "outbound_peer_count": 7,
                "inbound_peer_count": 1,
                "active_peers": 8,
                "is_synced": True,
                "virtual_daa_score": 100,
                "block_count": 200,
                "header_count": 210,
                "mempool_size": 3,
                "tip_count": 4,
                "virtual_parent_count": 2,
                "difficulty": 12.5,
                "process": {
                    "resident_set_gib": 1.25,
                    "cpu_usage": 0.5,
                    "fd_num": 42,
                },
            },
            "progress": {
                "relay_blocks_in_window": 10,
                "relay_events_in_window": 5,
                "latest_relay_age_seconds": 1,
            },
            "disk": {"free_gb": 100, "free_percent": 20},
        }

        recovery_summary = {
            "attempts": 2,
            "executed": 1,
            "dry_runs": 1,
            "skipped": 0,
            "unavailable": 0,
            "last_started_at": "2026-06-05T10:00:00+09:00",
            "last_completed_at": "2026-06-05T10:01:00+09:00",
            "last_exit_code": 0,
        }

        metrics = watchtower.format_prometheus_metrics(report, {"snapshots": 0}, recovery_summary)

        self.assertIn("kaspa_watchtower_mempool_size", metrics)
        self.assertIn("kaspa_watchtower_tip_count", metrics)
        self.assertIn("kaspa_watchtower_process_fd_num", metrics)
        self.assertIn("kaspa_watchtower_recovery_attempts_total", metrics)
        self.assertIn("kaspa_watchtower_recovery_last_started_timestamp_seconds", metrics)

    def test_config_validation_rejects_invalid_numeric_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
            config.update(
                {
                    "node_name": "test-node",
                    "process_match": "kaspad",
                    "rpc_endpoint": "127.0.0.1:16210",
                    "grpc_endpoint": "127.0.0.1:16210",
                    "log_path": str(tmp_path / "log.txt"),
                    "data_dir": str(tmp_path / "data"),
                    "state_path": str(tmp_path / "state.json"),
                    "status_page_path": str(tmp_path / "status.html"),
                    "benchmark_path": str(tmp_path / "benchmarks.jsonl"),
                    "prometheus_metrics_path": str(tmp_path / "watchtower.prom"),
                    "canvas_status_page_path": "",
                }
            )
            Path(config["log_path"]).write_text("", encoding="utf-8")
            Path(config["data_dir"]).mkdir()
            config["thresholds"]["disk_free_percent_min"] = 101
            config["retention"]["benchmark_entries"] = 0

            failed = {
                check.name
                for check in watchtower.config_validation_checks(config)
                if not check.ok
            }

            self.assertIn("thresholds.disk_free_percent_min", failed)
            self.assertIn("retention.benchmark_entries", failed)


if __name__ == "__main__":
    unittest.main()

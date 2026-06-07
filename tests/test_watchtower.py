import json
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import watchtower


class WatchtowerUnitTests(unittest.TestCase):
    def test_positive_int_falls_back_for_invalid_values(self):
        self.assertEqual(watchtower.positive_int("25", 100), 25)
        self.assertEqual(watchtower.positive_int(0, 100), 100)
        self.assertEqual(watchtower.positive_int("-1", 100), 100)
        self.assertEqual(watchtower.positive_int("nope", 100), 100)

    def test_config_example_includes_default_threshold_keys(self):
        example = json.loads(Path("config.example.json").read_text(encoding="utf-8"))

        self.assertEqual(
            set(watchtower.DEFAULT_CONFIG["thresholds"]),
            set(example["thresholds"]),
        )

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

    def test_parse_processed_stats(self):
        lines = [
            (
                "2026-06-06 18:02:27.389+09:00 [INFO ] Processed 92 blocks and "
                "92 headers in the last 10.00s (1311 transactions; 34 UTXO-validated blocks)"
            )
        ]

        stats = watchtower.parse_processed_stats(lines)

        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0].blocks, 92)
        self.assertEqual(stats[0].headers, 92)
        self.assertEqual(stats[0].seconds, 10.0)
        self.assertEqual(stats[0].transactions, 1311)

    def test_history_item_keeps_mempool_size(self):
        item = watchtower.history_item(
            {
                "checked_at": "2026-06-06T18:10:00+09:00",
                "status": "ok",
                "severity": "ok",
                "checks": [],
                "grpc_metrics": {"mempool_size": 12},
                "progress": {
                    "latest_processed_age_seconds": 2.5,
                    "latest_processed": {
                        "transactions_per_second": 131.1,
                        "transactions": 1311,
                        "blocks": 92,
                        "seconds": 10.0,
                    },
                },
            }
        )

        self.assertEqual(item["mempool_size"], 12)
        self.assertEqual(item["latest_processed_age_seconds"], 2.5)
        self.assertEqual(item["latest_processed_transactions_per_second"], 131.1)
        self.assertEqual(item["latest_processed_transactions"], 1311)

    def test_benchmark_item_keeps_processed_stats(self):
        item = watchtower.benchmark_item(
            {
                "checked_at": "2026-06-06T18:10:00+09:00",
                "node_name": "test-node",
                "status": "ok",
                "severity": "ok",
                "checks": [],
                "grpc_metrics": {},
                "progress": {
                    "latest_processed_age_seconds": 3.2,
                    "latest_processed": {
                        "transactions_per_second": 131.1,
                        "transactions": 1311,
                        "blocks": 92,
                        "seconds": 10.0,
                    },
                },
                "disk": {},
            }
        )

        self.assertEqual(item["latest_processed_age_seconds"], 3.2)
        self.assertEqual(item["latest_processed_transactions_per_second"], 131.1)
        self.assertEqual(item["latest_processed_transactions"], 1311)

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
            self.assertEqual(summary["ok_snapshots"], 2)
            self.assertEqual(summary["warn_snapshots"], 0)
            self.assertEqual(summary["critical_snapshots"], 0)
            self.assertEqual(summary["ok_ratio"], 1.0)
            self.assertEqual(summary["min_peer_count"], 8)
            self.assertEqual(summary["min_disk_free_gb"], 299.5)

    def test_benchmark_summary_filters_to_latest_node_and_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "benchmarks.jsonl"
            items = [
                {
                    "checked_at": "2026-06-05T10:00:00+09:00",
                    "node_name": "kaspa-tn10-local",
                    "network_id": "testnet-10",
                    "virtual_daa_score": 5000,
                    "block_count": 9000,
                    "status": "ok",
                    "severity": "ok",
                },
                {
                    "checked_at": "2026-06-05T11:00:00+09:00",
                    "node_name": "kaspa-mainnet-local",
                    "network_id": "mainnet",
                    "virtual_daa_score": 100,
                    "block_count": 200,
                    "status": "ok",
                    "severity": "ok",
                },
                {
                    "checked_at": "2026-06-05T12:00:00+09:00",
                    "node_name": "kaspa-mainnet-local",
                    "network_id": "mainnet",
                    "virtual_daa_score": 160,
                    "block_count": 260,
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
                "network_hashes_per_second": 1_250_000_000_000_000,
                "network_hashrate_window_size": 1000,
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
                "latest_processed_age_seconds": 2.5,
                "latest_processed": {
                    "timestamp": "2026-06-06T10:00:00+09:00",
                    "blocks": 92,
                    "headers": 92,
                    "transactions": 1311,
                    "seconds": 10.0,
                    "blocks_per_second": 9.2,
                    "headers_per_second": 9.2,
                    "transactions_per_second": 131.1,
                },
            },
            "sync_progress": {
                "active": True,
                "baseline_available": True,
                "elapsed_minutes": 10,
                "daa_delta": 10,
                "block_delta": 20,
                "header_delta": 30,
                "daa_rate_per_hour": 60,
                "block_rate_per_hour": 120,
                "header_rate_per_hour": 180,
            },
            "monitoring": {
                "require_synced": True,
                "require_relay_progress_when_unsynced": True,
                "require_sync_progress_when_unsynced": True,
                "sync_progress_stall_minutes": 30,
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

        benchmark_summary = {
            "snapshots": 2,
            "ok_snapshots": 2,
            "warn_snapshots": 0,
            "critical_snapshots": 0,
            "ok_ratio": 1.0,
            "min_peer_count": 8,
            "min_disk_free_gb": 99.5,
        }

        metrics = watchtower.format_prometheus_metrics(report, benchmark_summary, recovery_summary)

        self.assertIn("kaspa_watchtower_mempool_size", metrics)
        self.assertIn("kaspa_watchtower_latest_processed_transactions", metrics)
        self.assertIn("kaspa_watchtower_latest_processed_transactions_per_second", metrics)
        self.assertIn("kaspa_watchtower_latest_processed_timestamp_seconds", metrics)
        self.assertIn("kaspa_watchtower_latest_processed_age_seconds", metrics)
        self.assertIn("131.1", metrics)
        self.assertIn("kaspa_watchtower_tip_count", metrics)
        self.assertIn("kaspa_watchtower_process_fd_num", metrics)
        self.assertIn("kaspa_watchtower_network_hashes_per_second", metrics)
        self.assertIn("1.25e+15", metrics)
        self.assertIn("kaspa_watchtower_sync_active", metrics)
        self.assertIn("kaspa_watchtower_sync_header_rate_per_hour", metrics)
        self.assertIn("kaspa_watchtower_require_synced", metrics)
        self.assertIn("kaspa_watchtower_sync_progress_stall_minutes", metrics)
        self.assertIn("kaspa_watchtower_recovery_attempts_total", metrics)
        self.assertIn("kaspa_watchtower_recovery_last_started_timestamp_seconds", metrics)
        self.assertIn("kaspa_watchtower_benchmark_ok_ratio", metrics)
        self.assertIn("kaspa_watchtower_benchmark_min_peer_count", metrics)
        self.assertIn("kaspa_watchtower_benchmark_min_disk_free_gb", metrics)

    def test_grafana_dashboard_includes_processed_freshness_panel(self):
        dashboard = json.loads(Path("grafana/kaspa-watchtower.json").read_text(encoding="utf-8"))
        panels = {panel.get("title"): panel for panel in dashboard.get("panels", [])}

        self.assertIn("Processed Stats Freshness", panels)
        targets = panels["Processed Stats Freshness"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_latest_processed_age_seconds{node="$node"}'
                for target in targets
            )
        )

    def test_grafana_dashboard_includes_mempool_panel(self):
        dashboard = json.loads(Path("grafana/kaspa-watchtower.json").read_text(encoding="utf-8"))
        panels = {panel.get("title"): panel for panel in dashboard.get("panels", [])}

        self.assertIn("Mempool Size", panels)
        targets = panels["Mempool Size"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_mempool_size{node="$node"}'
                for target in targets
            )
        )

    def test_unsynced_bootstrap_skips_sync_and_relay_progress_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "rusty-kaspa.log"
            timestamp = dt.datetime.now().astimezone().isoformat(" ").replace("T", " ")
            log_path.write_text(f"{timestamp} INFO Node is bootstrapping mainnet\n", encoding="utf-8")
            data_dir = tmp_path / "data"
            data_dir.mkdir()

            config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
            config.update(
                {
                    "node_name": "mainnet-bootstrap",
                    "process_match": "kaspad",
                    "rpc_endpoint": "127.0.0.1:16110",
                    "grpc_endpoint": "127.0.0.1:16110",
                    "log_path": str(log_path),
                    "data_dir": str(data_dir),
                }
            )
            config["thresholds"]["require_synced"] = False
            config["thresholds"]["require_relay_progress_when_unsynced"] = False

            with (
                mock.patch.object(watchtower, "find_processes", return_value=["123 kaspad"]),
                mock.patch.object(watchtower, "disk_usage", return_value={"exists": True, "free_gb": 100, "free_percent": 20}),
                mock.patch.object(watchtower, "check_tcp_endpoint", return_value={"configured": True, "ok": True, "detail": "ok"}),
                mock.patch.object(
                    watchtower,
                    "fetch_optional_grpc_metrics",
                    return_value={
                        "configured": True,
                        "ok": True,
                        "is_synced": False,
                        "peer_count": 8,
                        "active_peers": 8,
                    },
                ),
                mock.patch.object(watchtower, "dir_size", return_value="1G"),
            ):
                report = watchtower.build_report(config)

            checks = {check["name"]: check for check in report["checks"]}
            self.assertEqual(report["severity"], "ok")
            self.assertTrue(checks["sync_status"]["ok"])
            self.assertTrue(checks["block_progress"]["ok"])
            self.assertIn("skipped while unsynced", checks["block_progress"]["detail"])

    def test_synced_node_warns_on_stale_processed_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "rusty-kaspa.log"
            latest = dt.datetime.now().astimezone()
            processed = latest - dt.timedelta(seconds=270)
            log_path.write_text(
                "\n".join(
                    [
                        (
                            f"{processed.strftime('%Y-%m-%d %H:%M:%S.%f%z')[:-2]}:{processed.strftime('%z')[-2:]} "
                            "[INFO ] Processed 10 blocks and "
                            "10 headers in the last 10.00s (20 transactions; 10 UTXO-validated blocks)"
                        ),
                        f"{latest.strftime('%Y-%m-%d %H:%M:%S.%f%z')[:-2]}:{latest.strftime('%z')[-2:]} INFO health heartbeat",
                    ]
                ),
                encoding="utf-8",
            )
            data_dir = tmp_path / "data"
            data_dir.mkdir()

            config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
            config.update(
                {
                    "node_name": "mainnet-synced",
                    "process_match": "kaspad",
                    "rpc_endpoint": "127.0.0.1:16110",
                    "grpc_endpoint": "127.0.0.1:16110",
                    "log_path": str(log_path),
                    "data_dir": str(data_dir),
                }
            )
            config["thresholds"]["min_relay_blocks_in_window"] = 0

            with (
                mock.patch.object(watchtower, "find_processes", return_value=["123 kaspad"]),
                mock.patch.object(watchtower, "disk_usage", return_value={"exists": True, "free_gb": 100, "free_percent": 20}),
                mock.patch.object(watchtower, "check_tcp_endpoint", return_value={"configured": True, "ok": True, "detail": "ok"}),
                mock.patch.object(
                    watchtower,
                    "fetch_optional_grpc_metrics",
                    return_value={
                        "configured": True,
                        "ok": True,
                        "is_synced": True,
                        "peer_count": 8,
                        "active_peers": 8,
                    },
                ),
                mock.patch.object(watchtower, "dir_size", return_value="1G"),
            ):
                report = watchtower.build_report(config)

            checks = {check["name"]: check for check in report["checks"]}
            self.assertEqual(report["severity"], "warn")
            self.assertFalse(checks["processed_stats_freshness"]["ok"])
            self.assertIn("latest processed stats are", checks["processed_stats_freshness"]["detail"])
            self.assertIn("threshold=180s", checks["processed_stats_freshness"]["detail"])
            self.assertIn("inspect kaspad processed-stats log output", checks["processed_stats_freshness"]["detail"])
            self.assertGreaterEqual(report["progress"]["latest_processed_age_seconds"], 260.0)

    def test_unsynced_sync_progress_stall_warns(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        checked_at = "2026-06-05T22:00:00+09:00"
        report = {
            "node_name": "mainnet-bootstrap",
            "status": "ok",
            "severity": "ok",
            "checked_at": checked_at,
            "checks": [{"name": "process", "ok": True, "detail": "running"}],
            "grpc_metrics": {
                "ok": True,
                "is_synced": False,
                "network_id": "mainnet",
                "virtual_daa_score": 100,
                "block_count": 200,
                "header_count": 300,
            },
        }
        state = {
            "history": [
                {
                    "checked_at": "2026-06-05T21:20:00+09:00",
                    "network_id": "mainnet",
                    "virtual_daa_score": 100,
                    "block_count": 200,
                    "header_count": 300,
                }
            ]
        }

        watchtower.apply_stateful_checks(report, state, config)

        checks = {check["name"]: check for check in report["checks"]}
        self.assertEqual(report["severity"], "warn")
        self.assertFalse(checks["sync_progress"]["ok"])
        self.assertIn("daa_delta=+0", checks["sync_progress"]["detail"])
        self.assertEqual(report["sync_progress"]["header_rate_per_hour"], 0)

    def test_format_sync_report_includes_rates_and_benchmark_window(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "checked_at": "2026-06-05T22:20:00+09:00",
            "status": "ok",
            "severity": "ok",
            "checks": [],
            "grpc_metrics": {
                "network_id": "mainnet",
                "is_synced": False,
                "peer_count": 8,
                "active_peers": 8,
                "virtual_daa_score": 451,
                "block_count": 123,
                "header_count": 456,
                "tip_count": 2,
            },
            "sync_progress": {
                "detail": "daa_delta=+10 block_delta=+5 header_delta=+0 over 30.0m",
                "daa_rate_per_hour": 20,
                "block_rate_per_hour": 10,
                "header_rate_per_hour": 0,
            },
        }
        benchmark_summary = {
            "window": "a -> b (0.50h)",
            "daa_rate": "20.00/h",
            "block_rate": "10.00/h",
            "relay_rate": "0.00/min",
        }

        text = watchtower.format_sync_report(report, benchmark_summary)

        self.assertIn("Kaspa sync report: kaspa-mainnet-local", text)
        self.assertIn("sync_rates=daa=20.00/h blocks=10.00/h headers=0.00/h", text)
        self.assertIn("benchmark_window=a -> b (0.50h)", text)

    def test_format_summary_includes_processed_transaction_freshness(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "checked_at": "2026-06-06T10:00:00+09:00",
            "status": "ok",
            "severity": "ok",
            "checks": [],
            "grpc_metrics": {
                "network_id": "mainnet",
                "is_synced": True,
                "peer_count": 8,
                "active_peers": 8,
                "virtual_daa_score": 100,
            },
            "progress": {
                "relay_blocks_in_window": 10,
                "relay_events_in_window": 2,
                "window_minutes": 10,
                "latest_relay_age_seconds": 1.2,
                "latest_processed_age_seconds": 2.5,
                "latest_processed": {
                    "blocks": 92,
                    "transactions": 1311,
                    "seconds": 10.0,
                    "transactions_per_second": 131.1,
                },
            },
            "disk": {"exists": True, "free_gb": 100, "free_percent": 20},
        }

        text = watchtower.format_summary(report)

        self.assertIn("processed=tx_rate=131.10/s age=2.5s tx=1311 blocks=92 window=10.0s", text)

    def test_format_alert_reports_sync_completed_event(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "checked_at": "2026-06-05T22:30:00+09:00",
            "severity": "ok",
            "status": "ok",
            "checks": [],
            "latest_throughput": None,
            "grpc_metrics": {
                "ok": True,
                "is_synced": True,
                "peer_count": 8,
                "network_id": "mainnet",
                "virtual_daa_score": 500,
            },
            "progress": {
                "relay_blocks_in_window": 10,
                "relay_events_in_window": 5,
                "window_minutes": 10,
            },
            "recovery": {"action": "none"},
        }

        text = watchtower.format_alert(report, "ok", "ok", event="sync_completed")

        self.assertIn("sync completed", text)
        self.assertIn("상태: mainnet sync completed", text)
        self.assertIn("require_synced=true", text)

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
                check.name: check.detail
                for check in watchtower.config_validation_checks(config)
                if not check.ok
            }

            self.assertIn("thresholds.disk_free_percent_min", failed)
            self.assertIn("retention.benchmark_entries", failed)
            self.assertIn("expected number between 0 and 100", failed["thresholds.disk_free_percent_min"])
            self.assertIn("expected integer > 0", failed["retention.benchmark_entries"])

    def test_config_validation_rejects_unsupported_config_version(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["config_version"] = 999

        failed = {
            check.name: check.detail
            for check in watchtower.config_validation_checks(config)
            if not check.ok
        }

        self.assertIn("config_version", failed)
        self.assertIn("expected integer between 1 and 1", failed["config_version"])

    def test_validate_config_prints_failed_summary(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["node_name"] = ""

        with mock.patch("builtins.print") as mocked_print:
            status = watchtower.validate_config(config)

        output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertEqual(status, 1)
        self.assertIn("FAIL node_name: missing; expected non-empty node name", output)
        self.assertIn("Config validation failed:", output)
        self.assertIn("node_name", output)

    def test_config_validation_tolerates_non_object_sections(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["thresholds"] = "invalid"
        config["recovery"] = "invalid"

        checks = watchtower.config_validation_checks(config)

        self.assertTrue(any(check.name == "thresholds.alert_repeat_minutes" for check in checks))
        self.assertTrue(any(check.name == "recovery.mode" for check in checks))

    def test_format_recovery_decision_includes_next_action(self):
        report = {
            "node_name": "test-node",
            "status": "alert",
            "severity": "critical",
            "checks": [
                {"name": "process", "ok": False, "detail": "not running"},
                {"name": "rpc_tcp", "ok": False, "detail": "connect failed"},
            ],
        }

        text = watchtower.format_recovery_decision(
            report,
            mode="manual",
            restart_command=["launchctl", "kickstart", "-k", "service"],
            force=False,
            dry_run=True,
        )

        self.assertIn("Recovery decision:", text)
        self.assertIn("failed_checks=process,rpc_tcp", text)
        self.assertIn("restart_command_configured=True", text)
        self.assertIn("review command", text)

    def test_format_diagnostics_summary_is_sanitized(self):
        report = {
            "node_name": "test-node",
            "checked_at": "2026-06-06T10:00:00+09:00",
            "status": "alert",
            "severity": "critical",
            "checks": [{"name": "rpc_tcp", "ok": False, "detail": "connect failed"}],
            "grpc_metrics": {
                "network_id": "mainnet",
                "is_synced": False,
                "peer_count": 0,
                "active_peers": 0,
                "virtual_daa_score": 100,
            },
            "progress": {
                "relay_blocks_in_window": 0,
                "relay_events_in_window": 0,
                "window_minutes": 10,
                "latest_relay_age_seconds": None,
                "latest_processed_age_seconds": 240.0,
                "latest_processed": {
                    "blocks": 10,
                    "transactions": 20,
                    "seconds": 10.0,
                    "transactions_per_second": 2.0,
                },
            },
            "disk": {"exists": True, "free_gb": 10, "free_percent": 2},
            "recovery": {
                "action": "manual_approval_required",
                "mode": "manual",
                "restart_command_configured": True,
            },
        }

        text = watchtower.format_diagnostics_summary(report)

        self.assertIn("Kaspa diagnostics summary: test-node", text)
        self.assertIn("failed_checks=rpc_tcp", text)
        self.assertIn("processed=tx_rate=2.00/s age=240s tx=20 blocks=10 window=10.0s", text)
        self.assertIn("next=review failed checks", text)
        self.assertIn("sanitized=true", text)

    def test_format_incident_report_is_markdown_and_sanitized(self):
        report = {
            "node_name": "test-node",
            "checked_at": "2026-06-06T10:00:00+09:00",
            "status": "alert",
            "severity": "critical",
            "checks": [{"name": "rpc_tcp", "ok": False, "detail": "connect failed"}],
            "grpc_metrics": {"network_id": "mainnet"},
            "progress": {},
            "disk": {},
            "recovery": {
                "action": "manual_approval_required",
                "restart_command_configured": True,
            },
        }

        text = watchtower.format_incident_report(report)

        self.assertIn("# Kaspa Watchtower Incident Report: test-node", text)
        self.assertIn("- failed_checks: `rpc_tcp`", text)
        self.assertIn("## Sanitized Summary", text)
        self.assertIn("sanitized: true", text)

    def test_status_page_uses_incident_first_dashboard_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            report = {
                "node_name": "test-node",
                "checked_at": "2026-06-06T10:00:00+09:00",
                "status": "alert",
                "severity": "critical",
                "checks": [
                    {"name": "peer_count", "ok": False, "detail": "0 peers"},
                    {
                        "name": "processed_stats_freshness",
                        "ok": False,
                        "detail": "latest processed stats are 240.0s old",
                    },
                    {"name": "disk_free", "ok": True, "detail": "ok"},
                ],
                "grpc_metrics": {
                    "network_id": "mainnet",
                    "is_synced": False,
                    "peer_count": 0,
                    "active_peers": 0,
                    "virtual_daa_score": 100,
                    "tip_count": 2,
                    "mempool_size": 0,
                    "network_hashes_per_second": 1_250_000_000_000_000,
                    "network_hashrate_window_size": 1000,
                },
                "progress": {
                    "relay_blocks_in_window": 0,
                    "relay_events_in_window": 0,
                    "window_minutes": 10,
                    "latest_relay_age_seconds": None,
                    "latest_processed_age_seconds": 3.2,
                    "relay_samples": [
                        {"timestamp": "2026-06-06T09:59:58+09:00", "blocks": 9},
                        {"timestamp": "2026-06-06T10:00:00+09:00", "blocks": 16},
                    ],
                    "latest_processed": {
                        "timestamp": "2026-06-06T10:00:00+09:00",
                        "blocks": 92,
                        "headers": 92,
                        "seconds": 10.0,
                        "transactions": 1311,
                        "blocks_per_second": 9.2,
                        "transactions_per_second": 131.1,
                    },
                    "processed_samples": [
                        {
                            "timestamp": "2026-06-06T09:59:50+09:00",
                            "blocks_per_second": 8.7,
                            "transactions_per_second": 127.2,
                        },
                        {
                            "timestamp": "2026-06-06T10:00:00+09:00",
                            "blocks_per_second": 9.2,
                            "transactions_per_second": 131.1,
                        },
                    ],
                },
                "disk": {"exists": True, "free_gb": 10, "free_percent": 2},
                "recovery": {
                    "action": "manual_approval_required",
                    "mode": "manual",
                },
            }
            state = {
                "history": [
                    {"checked_at": "2026-06-06T09:58:00+09:00", "severity": "ok"},
                    {"checked_at": "2026-06-06T09:59:02+09:00", "severity": "warn", "mempool_size": 2},
                    {"checked_at": "2026-06-06T09:59:08+09:00", "severity": "warn", "mempool_size": 5},
                    {"checked_at": "2026-06-06T09:59:12+09:00", "severity": "critical", "mempool_size": 1},
                ]
            }
            output = tmp_path / "status.html"

            watchtower.write_status_page(output, report, state, benchmark_path=tmp_path / "missing.jsonl")

            html = output.read_text(encoding="utf-8")
            self.assertIn("Operator verdict", html)
            self.assertIn("Review failed checks", html)
            self.assertIn('class="incident critical"', html)
            self.assertIn('class="v-card critical"', html)
            self.assertIn('<section class="v-card warn">\n  <div class="v-label">Tx Rate</div>', html)
            self.assertIn("Severity Timeline", html)
            self.assertIn("severity-segment critical", html)
            self.assertIn("Triage Queue", html)
            self.assertIn('class="triage-card critical"', html)
            self.assertIn("Check peer connectivity", html)
            self.assertIn("transaction throughput freshness", html)
            self.assertIn("Command Center", html)
            self.assertIn("make incident-report", html)
            self.assertIn("make recover-dry-run", html)
            self.assertIn('class="command-copy"', html)
            self.assertIn('data-copy="make recover-dry-run"', html)
            self.assertIn("Copied", html)
            self.assertIn("KAS/USDT", html)
            self.assertIn("Hashrate", html)
            self.assertIn("1.25 PH/s", html)
            self.assertIn("network_hashes_per_second trend", html)
            self.assertIn("KAS/USDT 15m", html)
            self.assertIn("KAS/USDT 4h", html)
            self.assertIn("KAS/USDT 1D", html)
            self.assertIn("KAS/USDT 1W", html)
            self.assertIn("KAS/USDT 1M", html)
            self.assertIn("KAS/USDT vs BTC/USDT 1D", html)
            self.assertIn("KAS Exchange Volume 1D", html)
            self.assertIn("KAS/USDT Futures Positioning", html)
            self.assertIn("KAS/USDT Futures Trend 7D", html)
            self.assertIn("Bybit linear perp", html)
            self.assertIn("KAS/USDT Futures Liquidation Map 12H", html)
            self.assertIn("KAS/USDT Futures Liquidation Map 24H", html)
            self.assertIn("KAS/USDT Futures Liquidation Map 1W", html)
            self.assertIn("KAS/USDT Futures Liquidation Map 1M", html)
            self.assertIn("Signal Watch", html)
            self.assertIn("api.bybit.com/v5/market/tickers", html)
            self.assertIn("api.bybit.com/v5/market/kline", html)
            self.assertIn("category=linear&symbol=KASUSDT", html)
            self.assertIn("api.bybit.com/v5/market/open-interest", html)
            self.assertIn("api.gateio.ws/api/v4/spot/candlesticks", html)
            self.assertIn("api.mexc.com/api/v3/klines", html)
            self.assertIn("api.kucoin.com/api/v1/market/candles", html)
            self.assertIn("api.bitget.com/api/v2/spot/market/candles", html)
            self.assertIn("api.kraken.com/0/public/OHLC", html)
            self.assertIn("api.huobi.pro/market/history/kline", html)
            self.assertIn("symbol=BTCUSDT", html)
            self.assertIn('id="market-chart"', html)
            self.assertIn('id="market-chart-4h"', html)
            self.assertIn('id="market-chart-1d"', html)
            self.assertIn('id="market-chart-1w"', html)
            self.assertIn('id="market-chart-1m"', html)
            self.assertIn('id="market-cross-chart"', html)
            self.assertIn('id="market-volume-chart"', html)
            self.assertIn('id="market-volume-legend"', html)
            self.assertIn('id="futures-mark"', html)
            self.assertIn('id="futures-funding"', html)
            self.assertIn('id="futures-open-interest"', html)
            self.assertIn('id="futures-open-interest-value"', html)
            self.assertIn('id="futures-volume"', html)
            self.assertIn('id="futures-trend-chart"', html)
            self.assertIn('id="futures-trend-status"', html)
            self.assertIn('id="liquidation-chart-12h"', html)
            self.assertIn('id="liquidation-chart-24h"', html)
            self.assertIn('id="liquidation-chart-1w"', html)
            self.assertIn('id="liquidation-chart-1m"', html)
            self.assertIn('id="market-trend-15m"', html)
            self.assertIn('id="market-trend-4h"', html)
            self.assertIn('id="market-trend-1d"', html)
            self.assertIn('id="market-trend-1w"', html)
            self.assertIn('id="market-trend-1m"', html)
            self.assertIn('id="market-rsi-15m"', html)
            self.assertIn('id="market-rsi-4h"', html)
            self.assertIn('id="market-rsi-1d"', html)
            self.assertIn('id="market-rsi-1w"', html)
            self.assertIn('id="market-rsi-1m"', html)
            self.assertIn('id="market-signal-list"', html)
            self.assertIn("Trend pending", html)
            self.assertIn("RSI pending", html)
            self.assertIn("Waiting for candles", html)
            self.assertIn("min-height: 322px", html)
            self.assertIn("main > .panel + .panel", html)
            self.assertNotIn("\n    .panel + .panel { margin-top: 14px; }", html)
            self.assertIn("drawMarketCrossChart", html)
            self.assertIn("drawMarketVolumeChart", html)
            self.assertIn("marketVolumeRows", html)
            self.assertIn("marketVolumeDataset", html)
            self.assertIn("drawLiquidationMap", html)
            self.assertIn("buildLiquidationCells", html)
            self.assertIn("marketOpenInterestRows", html)
            self.assertIn("refreshLiquidationMap", html)
            self.assertIn("refreshFuturesPositioning", html)
            self.assertIn("drawFuturesTrend", html)
            self.assertIn("marketFundingRows", html)
            self.assertIn("refreshFuturesTrend", html)
            self.assertIn("formatMarketUsdt", html)
            self.assertIn("formatFundingPercent", html)
            self.assertIn("Estimated from Bybit linear OI/candles", html)
            self.assertIn("marketEmaPoints", html)
            self.assertIn("marketTrendState", html)
            self.assertIn("marketTrendBadge", html)
            self.assertIn("marketRsiValue", html)
            self.assertIn("marketRsiState", html)
            self.assertIn("marketRsiBadge", html)
            self.assertIn("marketRsiState(candles, 14)", html)
            self.assertIn("marketSignalState", html)
            self.assertIn("marketSignalWatch", html)
            self.assertIn("EMA cross up", html)
            self.assertIn("EMA cross down", html)
            self.assertIn("Overbought", html)
            self.assertIn("Oversold", html)
            self.assertIn("Close \" + distanceText + \" vs EMA", html)
            self.assertIn("Uptrend", html)
            self.assertIn("Downtrend", html)
            self.assertIn("Neutral", html)
            self.assertIn("market-ema-line", html)
            self.assertIn("market-trend-badge", html)
            self.assertIn("market-rsi-badge", html)
            self.assertIn("market-signal-row", html)
            self.assertIn("marketKlineUrl", html)
            self.assertIn("marketAxisTimeLabel", html)
            self.assertEqual(html.count('axisMode: "day"'), 2)
            self.assertIn('axisMode: "month"', html)
            self.assertIn('axisMode: "year"', html)
            self.assertIn("lookbackMs: 24 * 60 * 60 * 1000", html)
            self.assertIn("lookbackMs: 7 * 24 * 60 * 60 * 1000", html)
            self.assertIn("lookbackMonths: 1", html)
            self.assertIn("lookbackMs: 365 * 24 * 60 * 60 * 1000", html)
            self.assertIn("limit: 1000", html)
            self.assertIn("limit: 32", html)
            self.assertIn("emaPeriod: 21", html)
            self.assertIn("emaPeriod: 12", html)
            self.assertIn("emaPeriod: 10", html)
            self.assertIn("emaPeriod: 13", html)
            self.assertIn("emaPeriod: 6", html)
            self.assertNotIn("emaPeriod: 9", html)
            self.assertNotIn("emaPeriod: 20", html)
            self.assertNotIn("emaPeriod: 50", html)
            self.assertNotIn("emaPeriod: 100", html)
            self.assertNotIn("emaPeriod: 200", html)
            self.assertNotIn("emaPeriod: 365", html)
            self.assertIn('color: "#b42318"', html)
            self.assertIn('color: "#2563eb"', html)
            self.assertIn("formatMarketSignedPercent", html)
            self.assertIn("window.localStorage", html)
            self.assertIn("AbortController", html)
            self.assertIn("interval=240", html)
            self.assertIn("interval=D", html)
            self.assertIn("interval=W", html)
            self.assertIn("interval=M", html)
            self.assertIn("market-axis-label", html)
            self.assertIn('month: "2-digit"', html)
            self.assertIn('hour: "2-digit"', html)
            self.assertIn("Block Processing", html)
            self.assertIn("9.2/s", html)
            self.assertIn("Tx Rate", html)
            self.assertIn("Transaction Throughput", html)
            self.assertIn("131.1/s", html)
            self.assertIn("1311 tx / 10.0s", html)
            self.assertIn("3.2s old", html)
            self.assertIn("Recent transactions per second", html)
            self.assertIn("processed-chart", html)
            self.assertIn("Mempool Activity", html)
            self.assertLess(html.index("Transaction Throughput"), html.index("Mempool Activity"))
            self.assertIn("mempool-bars", html)
            self.assertIn('class="processed-chart mempool-bars"', html)
            self.assertIn('viewBox="0 0 720 164"', html)
            self.assertIn('data-bucket="10s"', html)
            self.assertIn("10-second buckets from status history", html)
            self.assertIn("Recent mempool size by 10 second bucket", html)
            self.assertIn("10s mempool size", html)
            self.assertIn("Relay Intake", html)
            self.assertIn("16 relay blocks", html)


if __name__ == "__main__":
    unittest.main()

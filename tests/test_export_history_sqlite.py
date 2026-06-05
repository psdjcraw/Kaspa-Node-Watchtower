import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "export_history_sqlite.py"
SPEC = importlib.util.spec_from_file_location("export_history_sqlite", SCRIPT_PATH)
export_history_sqlite = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(export_history_sqlite)


class ExportHistorySqliteTests(unittest.TestCase):
    def test_history_summary_uses_latest_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "history.sqlite"
            with sqlite3.connect(db_path) as connection:
                export_history_sqlite.create_schema(connection)
                export_history_sqlite.upsert_items(
                    connection,
                    "benchmark_snapshots",
                    export_history_sqlite.BENCHMARK_COLUMNS,
                    "checked_at",
                    [
                        {
                            "checked_at": "2026-05-01T00:00:00+09:00",
                            "status": "ok",
                            "severity": "ok",
                            "peer_count": 4,
                            "virtual_daa_score": 10,
                            "block_count": 20,
                            "disk_free_gb": 200,
                        },
                        {
                            "checked_at": "2026-06-05T10:00:00+09:00",
                            "node_name": "kaspa-mainnet-local",
                            "status": "ok",
                            "severity": "ok",
                            "peer_count": 8,
                            "virtual_daa_score": 100,
                            "block_count": 200,
                            "disk_free_gb": 300,
                        },
                        {
                            "checked_at": "2026-06-06T10:00:00+09:00",
                            "node_name": "kaspa-mainnet-local",
                            "status": "warn",
                            "severity": "warn",
                            "peer_count": 6,
                            "virtual_daa_score": 160,
                            "block_count": 260,
                            "disk_free_gb": 299.5,
                        },
                    ],
                )
                export_history_sqlite.upsert_items(
                    connection,
                    "recovery_attempts",
                    export_history_sqlite.RECOVERY_COLUMNS,
                    "started_at",
                    [
                        {
                            "started_at": "2026-06-06T11:00:00+09:00",
                            "completed_at": "2026-06-06T11:01:00+09:00",
                            "action": "manual_approval_required",
                            "dry_run": True,
                            "exit_code": 0,
                        }
                    ],
                )
                connection.commit()

                summary = export_history_sqlite.history_summary(connection, days=7)

            self.assertEqual(summary["benchmark_snapshots"], 2)
            self.assertEqual(summary["latest_severity"], "warn")
            self.assertEqual(summary["ok_ratio"], 0.5)
            self.assertEqual(summary["warn_snapshots"], 1)
            self.assertEqual(summary["critical_snapshots"], 0)
            self.assertEqual(summary["min_peer_count"], 6)
            self.assertEqual(summary["min_disk_free_gb"], 299.5)
            self.assertEqual(summary["daa_delta"], 60)
            self.assertEqual(summary["block_delta"], 60)
            self.assertEqual(summary["recovery_attempts"], 1)
            self.assertEqual(summary["recovery_dry_runs"], 1)

    def test_history_summary_filters_benchmarks_to_latest_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "history.sqlite"
            with sqlite3.connect(db_path) as connection:
                export_history_sqlite.create_schema(connection)
                export_history_sqlite.upsert_items(
                    connection,
                    "benchmark_snapshots",
                    export_history_sqlite.BENCHMARK_COLUMNS,
                    "checked_at",
                    [
                        {
                            "checked_at": "2026-06-05T10:00:00+09:00",
                            "node_name": "kaspa-tn10-local",
                            "status": "ok",
                            "severity": "ok",
                            "peer_count": 8,
                            "virtual_daa_score": 5000,
                            "block_count": 9000,
                            "disk_free_gb": 300,
                        },
                        {
                            "checked_at": "2026-06-06T09:00:00+09:00",
                            "node_name": "kaspa-mainnet-local",
                            "status": "ok",
                            "severity": "ok",
                            "peer_count": 8,
                            "virtual_daa_score": 100,
                            "block_count": 200,
                            "disk_free_gb": 300,
                        },
                        {
                            "checked_at": "2026-06-06T10:00:00+09:00",
                            "node_name": "kaspa-mainnet-local",
                            "status": "ok",
                            "severity": "ok",
                            "peer_count": 8,
                            "virtual_daa_score": 160,
                            "block_count": 260,
                            "disk_free_gb": 299.5,
                        },
                    ],
                )
                connection.commit()

                summary = export_history_sqlite.history_summary(connection, days=7)

            self.assertEqual(summary["benchmark_snapshots"], 2)
            self.assertEqual(summary["daa_delta"], 60)
            self.assertEqual(summary["block_delta"], 60)


if __name__ == "__main__":
    unittest.main()

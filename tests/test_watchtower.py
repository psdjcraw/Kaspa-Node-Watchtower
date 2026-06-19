import json
import copy
import datetime as dt
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
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
        self.assertEqual(set(watchtower.DEFAULT_CONFIG["indexer"]), set(example["indexer"]))
        self.assertEqual(set(watchtower.DEFAULT_CONFIG["indexer_watch"]), set(example["indexer_watch"]))
        self.assertEqual(set(watchtower.DEFAULT_CONFIG["sdk_probe"]), set(example["sdk_probe"]))

    def test_fetch_optional_sdk_metrics_disabled_by_default(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)

        status = watchtower.fetch_optional_sdk_metrics(config, "127.0.0.1:17110")

        self.assertFalse(status["enabled"])
        self.assertFalse(status["configured"])

    def test_fetch_optional_sdk_metrics_uses_probe(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["sdk_probe"]["enabled"] = True
        config["sdk_probe"]["endpoint"] = "127.0.0.1:17110"

        with mock.patch(
            "kaspa_sdk_probe.fetch_sdk_metrics",
            return_value={
                "ok": True,
                "sdk_installed": True,
                "peer_count": 8,
                "virtual_daa_score": 123,
            },
        ) as fetch:
            status = watchtower.fetch_optional_sdk_metrics(config, "127.0.0.1:16110")

        self.assertTrue(status["ok"])
        self.assertEqual(status["endpoint"], "127.0.0.1:17110")
        self.assertEqual(status["peer_count"], 8)
        fetch.assert_called_once_with("127.0.0.1:17110", network_id="mainnet", timeout=5.0, encoding="borsh")

    def test_fetch_optional_sdk_metrics_can_use_external_python(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["sdk_probe"]["enabled"] = True
        config["sdk_probe"]["endpoint"] = "127.0.0.1:17110"
        config["sdk_probe"]["python_bin"] = "/tmp/sdk-python"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"ok": True, "sdk_installed": True, "peer_count": 4}),
            stderr="",
        )

        with mock.patch("watchtower.subprocess.run", return_value=completed) as run:
            status = watchtower.fetch_optional_sdk_metrics(config, "")

        self.assertTrue(status["ok"])
        self.assertEqual(status["peer_count"], 4)
        self.assertEqual(status["python_bin"], "/tmp/sdk-python")
        self.assertEqual(run.call_args.args[0][0], "/tmp/sdk-python")

    def test_fetch_optional_sdk_metrics_can_collect_subscriptions(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["sdk_probe"]["enabled"] = True
        config["sdk_probe"]["endpoint"] = "127.0.0.1:17110"
        config["sdk_probe"]["python_bin"] = "/tmp/sdk-python"
        config["sdk_probe"]["subscription_enabled"] = True
        config["sdk_probe"]["subscription_duration_seconds"] = 2
        config["sdk_probe"]["subscription_watch_addresses"] = [{"label": "ops", "address": "kaspa:" + "q" * 61}]
        responses = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps({"ok": True, "sdk_installed": True}),
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {
                        "subscription_ok": True,
                        "subscription_events_total": 12,
                        "subscription_watch_addresses": 1,
                    }
                ),
                stderr="",
            ),
        ]

        with mock.patch("watchtower.subprocess.run", side_effect=responses) as run:
            status = watchtower.fetch_optional_sdk_metrics(config, "")

        self.assertTrue(status["ok"])
        self.assertTrue(status["subscription_ok"])
        self.assertEqual(status["subscription_events_total"], 12)
        self.assertEqual(status["subscription_watch_addresses"], 1)
        second_command = run.call_args_list[1].args[0]
        self.assertIn("--subscriptions", second_command)
        self.assertIn("--address", second_command)

    def test_sdk_subscription_watch_targets_merge_existing_watchlists(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        shared = "kaspa:" + "q" * 61
        wallet_only = "kaspa:" + "p" * 61
        mining = "kaspa:" + "r" * 61
        config["sdk_probe"]["subscription_watch_addresses"] = [{"label": "sdk", "address": shared}]
        config["wallet"]["watch_addresses"] = [{"label": "wallet", "address": wallet_only}]
        config["indexer_watch"]["watch_addresses"] = [{"label": "indexer", "address": shared}]
        config["mining"]["wallet_address"] = mining

        targets = watchtower.sdk_subscription_watch_targets(config)

        self.assertEqual({item["address"] for item in targets}, {shared, wallet_only, mining})

    def test_fetch_optional_indexer_status_reads_health_and_metrics(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["indexer"]["enabled"] = True
        config["indexer"]["max_lag_seconds"] = 60
        now = dt.datetime.now().astimezone()

        with mock.patch(
            "watchtower.fetch_json_url",
            side_effect=[
                {"status": "healthy", "version": "1.2.3"},
                {
                    "indexer_lag_seconds": 12,
                    "schema_version": 22,
                    "checkpoint": {"timestamp": now.isoformat()},
                },
            ],
        ):
            status = watchtower.fetch_optional_indexer_status(config)

        self.assertTrue(status["ok"])
        self.assertTrue(status["health_ok"])
        self.assertTrue(status["metrics_ok"])
        self.assertEqual(status["metrics"]["lag_seconds"], 12)
        self.assertEqual(status["metrics"]["schema_version"], 22)

    def test_fetch_optional_indexer_status_flags_stale_metrics(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["indexer"]["enabled"] = True
        config["indexer"]["max_lag_seconds"] = 10

        with mock.patch(
            "watchtower.fetch_json_url",
            side_effect=[
                {"status": "ok"},
                {"lag_seconds": 30, "timestamp": 1_700_000_000},
            ],
        ):
            status = watchtower.fetch_optional_indexer_status(config)

        self.assertFalse(status["ok"])
        self.assertFalse(status["lag_ok"])
        self.assertEqual(status["metrics"]["lag_seconds"], 30)

    def test_fetch_optional_indexer_status_treats_catchup_as_syncing(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["indexer"]["enabled"] = True
        config["indexer"]["max_checkpoint_age_seconds"] = 60

        health_payload = {
            "status": "DOWN",
            "indexer": {
                "status": "DOWN",
                "details": [
                    {"name": "checkpoint", "status": "DOWN", "reason": "1day 4h behind"},
                    {"name": "queue.transactions", "status": "WARN", "reason": "Utilization: 100%"},
                ],
            },
            "kaspad": {"status": "UP", "isSynced": True, "networkId": "mainnet"},
        }
        with (
            mock.patch("watchtower.fetch_indexer_health_payload", return_value=(health_payload, 503)),
            mock.patch("watchtower.fetch_json_url", return_value={"checkpoint": {"timestamp": 1_700_000_000}}),
        ):
            status = watchtower.fetch_optional_indexer_status(config)

        self.assertTrue(status["ok"])
        self.assertFalse(status["health_ok"])
        self.assertTrue(status["syncing"])
        self.assertEqual(status["state"], "syncing")
        self.assertEqual(status["health_http_status"], 503)
        self.assertTrue(status["checkpoint_fresh"])

    def test_build_report_adds_indexer_checks_when_enabled(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["node_name"] = "test-mainnet"
        config["process_match"] = "kaspad"
        config["log_path"] = "/tmp/missing-watchtower-test.log"
        config["data_dir"] = ""
        config["rpc_endpoint"] = ""
        config["grpc_endpoint"] = ""
        config["thresholds"]["require_rpc"] = False
        config["thresholds"]["require_grpc_metrics"] = False
        config["indexer"]["enabled"] = True

        with (
            mock.patch("watchtower.find_processes", return_value=["123 kaspad"]),
            mock.patch("watchtower.disk_usage", return_value={"exists": False}),
            mock.patch("watchtower.check_tcp_endpoint", return_value={"configured": False, "ok": False, "detail": "not configured"}),
            mock.patch("watchtower.fetch_optional_grpc_metrics", return_value={"configured": False, "ok": False}),
            mock.patch("watchtower.fetch_optional_wallet_balances", return_value={"enabled": False, "ok": True}),
            mock.patch("watchtower.fetch_optional_mining_status", return_value={"enabled": False, "ok": True}),
            mock.patch("watchtower.fetch_optional_whale_watch", return_value={"enabled": False, "ok": True}),
            mock.patch(
                "watchtower.fetch_optional_indexer_status",
                return_value={
                    "enabled": True,
                    "ok": False,
                    "health_ok": True,
                    "metrics_ok": True,
                    "lag_ok": False,
                    "checkpoint_fresh": True,
                    "detail": "lag=90s",
                    "metrics": {"lag_seconds": 90, "checkpoint_age_seconds": 12},
                },
            ),
        ):
            report = watchtower.build_report(config)

        checks = {check["name"]: check for check in report["checks"]}
        self.assertIn("indexer_health", checks)
        self.assertIn("indexer_lag", checks)
        self.assertFalse(checks["indexer_lag"]["ok"])
        self.assertEqual(report["indexer"]["metrics"]["lag_seconds"], 90)

    def test_indexer_lookup_fetches_transaction_api(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["indexer"]["enabled"] = True
        config["indexer"]["base_url"] = "http://indexer.local"

        with (
            mock.patch(
                "watchtower.fetch_json_url",
                return_value={"transaction_id": "abc123", "outputs": [{"value": 1}, {"value": 2}]},
            ) as fetch,
            mock.patch("builtins.print") as printed,
        ):
            code = watchtower.indexer_lookup(config, "tx", "abc123")

        self.assertEqual(code, 0)
        fetch.assert_called_once_with("http://indexer.local/api/transactions/abc123", timeout=2.0)
        printed.assert_called_once()
        self.assertIn("Kaspa indexer tx: abc123", printed.call_args.args[0])
        self.assertIn("outputs_count=2", printed.call_args.args[0])

    def test_indexer_lookup_fetches_balance_and_utxos_api(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["indexer"]["enabled"] = True
        config["indexer"]["base_url"] = "http://indexer.local"

        with (
            mock.patch("watchtower.fetch_json_url", side_effect=[{"balance_sompi": 123}, {"utxos": [{"outpoint": "a"}]}]) as fetch,
            mock.patch("builtins.print") as printed,
        ):
            balance_code = watchtower.indexer_lookup(config, "balance", "kaspa:qabc")
            utxos_code = watchtower.indexer_lookup(config, "utxos", "kaspa:qabc")

        self.assertEqual(balance_code, 0)
        self.assertEqual(utxos_code, 0)
        self.assertEqual(fetch.call_args_list[0].args[0], "http://indexer.local/api/addresses/kaspa%3Aqabc/balance")
        self.assertEqual(fetch.call_args_list[1].args[0], "http://indexer.local/api/addresses/kaspa%3Aqabc/utxos")
        self.assertIn("Kaspa indexer balance: kaspa:qabc", printed.call_args_list[0].args[0])
        self.assertIn("Kaspa indexer utxos: kaspa:qabc", printed.call_args_list[1].args[0])

    def test_discord_command_routes_indexer_lookup_without_node_report(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)

        with mock.patch("watchtower.indexer_lookup", return_value=0) as lookup:
            code = watchtower.discord_command(config, "balance", query_value="kaspa:qabc")

        self.assertEqual(code, 0)
        lookup.assert_called_once_with(config, "balance", "kaspa:qabc")

    def test_indexer_watch_config_adds_and_removes_addresses(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text(json.dumps({"node_name": "test"}), encoding="utf-8")

            added = watchtower.update_indexer_watch_config(
                config_path,
                add_address="kaspa:qabcdefghijklmnopqrst",
                label="mining",
            )
            self.assertTrue(added["enabled"])
            self.assertEqual(added["watch_addresses"], [{"address": "kaspa:qabcdefghijklmnopqrst", "label": "mining"}])

            loaded = watchtower.load_raw_config(config_path)
            self.assertTrue(loaded["indexer"]["enabled"])
            text = watchtower.format_indexer_watchlist({**watchtower.DEFAULT_CONFIG, **loaded})
            self.assertIn("mining: kaspa:qabcdefghijklmnopqrst", text)

            removed = watchtower.update_indexer_watch_config(config_path, remove_address="kaspa:qabcdefghijklmnopqrst")
            self.assertFalse(removed["enabled"])
            self.assertEqual(removed["watch_addresses"], [])

    def test_discord_command_updates_indexer_watchlist(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text(json.dumps({"node_name": "test"}), encoding="utf-8")
            config = watchtower.load_config(config_path)

            with mock.patch("builtins.print") as printed:
                code = watchtower.discord_command(
                    config,
                    "watch-add",
                    config_path=config_path,
                    query_value="kaspa:qabcdefghijklmnopqrst",
                    reason="mining",
                )

            self.assertEqual(code, 0)
            self.assertIn("added addresses=1 enabled=True", printed.call_args.args[0])
            loaded = watchtower.load_raw_config(config_path)
            self.assertEqual(loaded["indexer_watch"]["watch_addresses"][0]["label"], "mining")

    def test_discord_watch_list_prints_live_address_state(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        report = {
            "indexer_watch": {
                "enabled": True,
                "ok": True,
                "watch_addresses": [{"label": "mining", "address": "kaspa:qabc"}],
                "address_states": [
                    {
                        "label": "mining",
                        "address": "kaspa:qabc",
                        "ok": True,
                        "balance_sompi": 300000000,
                        "utxo_count": 2,
                        "tx_count": 5,
                        "last_checked_at": "2026-06-19T22:00:00+09:00",
                    }
                ],
                "events": [],
                "new_events": [],
                "detail": "watched=1 new_events=0 total_events=0",
            }
        }

        with (
            mock.patch("watchtower.build_stateful_report", return_value=(report, {})),
            mock.patch("builtins.print") as printed,
        ):
            code = watchtower.discord_command(config, "watch-list")

        self.assertEqual(code, 0)
        text = printed.call_args.args[0]
        self.assertIn("Kaspa indexer watchlist:", text)
        self.assertIn("enabled=True ok=True addresses=1 events=0 new=0", text)
        self.assertIn("mining: kaspa:qabc ready=True balance=3.00000000 KAS utxos=2 txs=5", text)
        self.assertIn("recent_events=none", text)

    def test_discord_watch_check_prints_readiness(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        report = {
            "indexer_watch": {
                "enabled": True,
                "ok": True,
                "watch_addresses": [{"label": "mining", "address": "kaspa:qabc"}],
                "address_states": [
                    {
                        "label": "mining",
                        "address": "kaspa:qabc",
                        "ok": True,
                        "balance_sompi": 300000000,
                        "utxo_count": 2,
                        "tx_count": 5,
                        "last_checked_at": "2026-06-19T22:00:00+09:00",
                    }
                ],
                "events": [],
                "new_events": [],
                "detail": "watched=1 new_events=0 total_events=0",
            },
            "sdk_metrics": {
                "enabled": True,
                "ok": True,
                "subscription_enabled": True,
                "subscription_ok": True,
                "subscription_events_total": 9,
                "subscription_last_event_age_seconds": 0.1,
                "subscription_watch_addresses": 1,
                "subscription_watch_targets": [{"label": "mining", "address": "kaspa:qabc"}],
                "events": [],
                "new_events": [],
            },
        }

        with (
            mock.patch("watchtower.build_stateful_report", return_value=(report, {})),
            mock.patch("builtins.print") as printed,
        ):
            code = watchtower.discord_command(config, "watch-check")

        self.assertEqual(code, 0)
        text = printed.call_args.args[0]
        self.assertIn("Kaspa watch readiness:", text)
        self.assertIn("ready=True indexer_ok=True sdk_ok=True addresses=1", text)
        self.assertIn("sdk_subscription=enabled=True ok=True live_events=9", text)
        self.assertIn("mining: kaspa:qabc indexer_ready=True sdk_target=True", text)
        self.assertIn("new_events=none", text)

    def test_watch_check_fails_when_indexer_watch_is_not_ready(self):
        report = {
            "indexer_watch": {
                "enabled": True,
                "ok": False,
                "watch_addresses": [{"label": "mining", "address": "kaspa:qabc"}],
                "address_states": [{"label": "mining", "address": "kaspa:qabc", "ok": False}],
            },
            "sdk_metrics": {"enabled": False},
        }

        self.assertFalse(watchtower.watch_readiness_ok(report))

    def test_indexer_watch_test_queries_address_endpoints(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["indexer"]["enabled"] = True
        address = "kaspa:qabcdefghijklmnopqrst"

        with (
            mock.patch(
                "watchtower.fetch_indexer_api",
                side_effect=[
                    {"transactions": [{"transaction_id": "tx1"}]},
                    {"balance_sompi": 123},
                    {"utxos": [{"outpoint": "a"}, {"outpoint": "b"}]},
                ],
            ) as fetch,
            mock.patch("builtins.print") as printed,
        ):
            code = watchtower.indexer_watch_test(config, address, "mining")

        self.assertEqual(code, 0)
        self.assertEqual(fetch.call_count, 3)
        text = printed.call_args.args[0]
        self.assertIn("Kaspa indexer watch-test:", text)
        self.assertIn("label=mining", text)
        self.assertIn("transactions=ok count=1", text)
        self.assertIn("utxos=ok count=2", text)

    def test_apply_indexer_watchlist_records_new_address_events_once(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["indexer"]["enabled"] = True
        config["indexer_watch"]["enabled"] = True
        config["indexer_watch"]["watch_addresses"] = [{"label": "mining", "address": "kaspa:qabc"}]
        report = {
            "node_name": "test-mainnet",
            "status": "ok",
            "severity": "ok",
            "checked_at": "2026-06-13T13:45:00+09:00",
            "checks": [],
            "recovery": {"action": "none"},
        }
        state = {}

        with mock.patch(
            "watchtower.fetch_indexer_api",
            side_effect=[
                {
                    "transactions": [
                        {"transaction_id": "tx1", "amount_sompi": 123456789},
                        {"tx_id": "tx2", "value_sompi": 200000000},
                    ]
                },
                {"balanceSompi": 300000000, "utxoCount": 4},
                {"utxos": [{"outpoint": "a"}, {"outpoint": "b"}]},
                {
                    "transactions": [
                        {"transaction_id": "tx1", "amount_sompi": 123456789},
                        {"tx_id": "tx2", "value_sompi": 200000000},
                    ]
                },
                {"balanceSompi": 300000000, "utxoCount": 4},
                {"utxos": [{"outpoint": "a"}, {"outpoint": "b"}]},
            ],
        ):
            event = watchtower.apply_indexer_watchlist(report, state, config)
            second_event = watchtower.apply_indexer_watchlist(report, state, config)

        self.assertEqual(event, "indexer_watch_event")
        self.assertIsNone(second_event)
        self.assertEqual(len(state["indexer_watch_events"]), 2)
        self.assertEqual(len(report["indexer_watch"]["new_events"]), 0)
        self.assertEqual(report["indexer_watch"]["address_states"][0]["balance_sompi"], 300000000)
        self.assertEqual(report["indexer_watch"]["address_states"][0]["balance_kas"], 3.0)
        self.assertEqual(report["indexer_watch"]["address_states"][0]["utxo_count"], 2)
        self.assertEqual(report["indexer_watch"]["address_states"][0]["tx_count"], 2)
        checks = {check["name"]: check for check in report["checks"]}
        self.assertTrue(checks["indexer_watchlist"]["ok"])

    def test_format_alert_includes_indexer_watch_events(self):
        report = {
            "node_name": "test-mainnet",
            "checked_at": "2026-06-13T13:45:00+09:00",
            "status": "ok",
            "severity": "ok",
            "checks": [],
            "latest_throughput": "",
            "progress": {"relay_blocks_in_window": 0, "relay_events_in_window": 0, "window_minutes": 10},
            "recovery": {"action": "none"},
            "indexer_watch": {
                "watch_addresses": [{"label": "mining", "address": "kaspa:qabc"}],
                "new_events": [
                    {
                        "label": "mining",
                        "type": "indexer_address_tx",
                        "address": "kaspa:qabc",
                        "tx_id": "abcdef1234567890",
                        "amount_sompi": 123456789,
                    }
                ],
            },
        }

        text = watchtower.format_alert(report, event="indexer_watch_event")

        self.assertIn("watched address tx", text)
        self.assertIn("Indexer watch: watched=1 new_events=1 total_events=0", text)
        self.assertIn("- mining source=indexer direction=indexer_address_tx", text)
        self.assertIn("tx=abcdef1234567890", text)
        self.assertIn("amount=1.23456789 KAS", text)

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

    def test_parse_miner_log_extracts_hashrate_and_shares(self):
        lines = [
            "2026-06-11 08:00:00.000+09:00 INFO speed 42.50 MH/s",
            "2026-06-11 08:01:00.000+09:00 INFO share accepted",
            "2026-06-11 08:02:00.000+09:00 INFO share rejected",
        ]

        parsed = watchtower.parse_miner_log(lines)

        self.assertEqual(parsed["hashrate_hs"], 42_500_000)
        self.assertEqual(parsed["accepted_shares"], 1)
        self.assertEqual(parsed["rejected_shares"], 1)
        self.assertEqual(parsed["last_share_at"], "2026-06-11T08:02:00+09:00")

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
            "mining": {
                "enabled": True,
                "ok": True,
                "running": True,
                "hashrate_hs": 42_500_000,
                "accepted_shares": 7,
                "rejected_shares": 1,
                "last_share_age_seconds": 12,
            },
            "whale_watch": {
                "enabled": True,
                "confirmed_enabled": True,
                "ok": True,
                "min_amount_sompi": 100_000_000_000_000,
                "min_amount_kas": 1_000_000,
                "confirmed_start_hash": "start",
                "mempool_entries": 3,
                "candidates": [{"tx_id": "new", "amount_sompi": 125_000_000_000_000}],
                "confirmed_candidates": [{"tx_id": "confirmed", "amount_sompi": 125_000_000_000_000}],
                "events": [
                    {
                        "observed_at": "2026-06-06T10:00:00+09:00",
                        "source": "mempool",
                        "tx_id": "abc123",
                        "amount_sompi": 125_000_000_000_000,
                    }
                ],
            },
            "indexer": {
                "enabled": True,
                "ok": True,
                "health_ok": True,
                "metrics_ok": True,
                "health_latency_ms": 15.5,
                "metrics_latency_ms": 8.2,
                "metrics": {
                    "lag_seconds": 12,
                    "checkpoint_age_seconds": 45,
                    "schema_version": 22,
                },
            },
            "indexer_watch": {
                "enabled": True,
                "ok": True,
                "watch_addresses": [{"label": "mining", "address": "kaspa:qabc"}],
                "address_states": [
                    {
                        "label": "mining",
                        "address": "kaspa:qabc",
                        "ok": True,
                        "balance_sompi": 300000000,
                        "balance_kas": 3.0,
                        "utxo_count": 2,
                        "tx_count": 5,
                        "last_checked_at": "2026-06-05T10:03:00+09:00",
                    }
                ],
                "events": [{"tx_id": "tx1"}],
                "new_events": [{"tx_id": "tx2"}],
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
        market_metrics = {
            "snapshots": 2,
            "successful_snapshots": 1,
            "last_ok": True,
            "last_checked_at": "2026-06-05T10:02:00+09:00",
            "source": "Bybit KAS/USDT",
            "latest_successful": {
                "spot_last_price": 0.0305,
                "spot_change_24h": 0.012,
                "spot_volume_24h": 42000000,
                "spot_price_median": 0.0304,
                "spot_price_min": 0.0301,
                "spot_price_max": 0.0308,
                "spot_price_dispersion_pct": 2.3026,
                "spot_price_sources": 7,
                "spot_price_source_errors": 0,
                "futures_basis_pct": -0.13,
                "futures_funding_rate": 0.0001,
                "futures_funding_apr_pct": 10.95,
                "futures_funding_z_score": 1.25,
                "futures_open_interest": 230000000,
                "futures_open_interest_value": 7010000,
                "futures_volume_24h": 78000000,
                "futures_oi_volume_ratio": 2.9487,
            },
        }
        multi_node_metrics = {
            "available": True,
            "verdict": "warn",
            "nodes": [
                {
                    "node_name": "mainnet-a",
                    "network": "mainnet",
                    "latest_severity": "ok",
                    "ok_ratio": 1.0,
                    "check_lag_minutes": 0,
                    "daa_lag": 0,
                    "block_lag": 0,
                    "peer_lag": 0,
                    "processed_age_lag_seconds": 0,
                    "flags": [],
                },
                {
                    "node_name": "mainnet-b",
                    "network": "mainnet",
                    "latest_severity": "warn",
                    "ok_ratio": 0.75,
                    "check_lag_minutes": 12,
                    "daa_lag": 300,
                    "block_lag": 280,
                    "peer_lag": 3,
                    "processed_age_lag_seconds": 75,
                    "flags": ["daa_lag", "stale_node"],
                },
            ],
        }

        metrics = watchtower.format_prometheus_metrics(
            report,
            benchmark_summary,
            recovery_summary,
            market_metrics,
            multi_node_metrics,
        )

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
        self.assertIn('kaspa_watchtower_market_spot_price_usdt{node="test-node",source="Bybit KAS/USDT"} 0.0305', metrics)
        self.assertIn("kaspa_watchtower_market_futures_basis_percent", metrics)
        self.assertIn("kaspa_watchtower_market_spot_price_dispersion_percent", metrics)
        self.assertIn('kaspa_watchtower_market_spot_price_sources{node="test-node",source="Bybit KAS/USDT"} 7', metrics)
        self.assertIn('kaspa_watchtower_market_futures_funding_z_score{node="test-node",source="Bybit KAS/USDT"} 1.25', metrics)
        self.assertIn("kaspa_watchtower_market_futures_open_interest_kas", metrics)
        self.assertIn("kaspa_watchtower_market_futures_oi_volume_ratio", metrics)
        self.assertIn('kaspa_watchtower_mining_running{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_mining_hashrate_hs{node="test-node"} 4.25e+07', metrics)
        self.assertIn('kaspa_watchtower_mining_accepted_shares{node="test-node"} 7', metrics)
        self.assertIn('kaspa_watchtower_whale_watch_enabled{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_whale_threshold_kas{node="test-node"} 1e+06', metrics)
        self.assertIn('kaspa_watchtower_whale_events_total{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_whale_latest_amount_kas{node="test-node"} 1.25e+06', metrics)
        self.assertIn('kaspa_watchtower_whale_confirmed_candidates{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_whale_confirmed_baseline_available{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_indexer_enabled{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_indexer_ok{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_indexer_syncing{node="test-node"} 0', metrics)
        self.assertIn('kaspa_watchtower_indexer_lag_seconds{node="test-node"} 12', metrics)
        self.assertIn('kaspa_watchtower_indexer_checkpoint_age_seconds{node="test-node"} 45', metrics)
        self.assertIn('kaspa_watchtower_indexer_watch_enabled{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_indexer_watch_events_total{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_indexer_watch_new_events{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_indexer_watch_address_ready{address="kaspa:qabc",label="mining",node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_indexer_watch_address_balance_kas{address="kaspa:qabc",label="mining",node="test-node"} 3', metrics)
        self.assertIn('kaspa_watchtower_indexer_watch_address_utxos{address="kaspa:qabc",label="mining",node="test-node"} 2', metrics)
        self.assertIn('kaspa_watchtower_indexer_watch_address_transactions{address="kaspa:qabc",label="mining",node="test-node"} 5', metrics)
        self.assertIn("2.3e+08", metrics)
        self.assertIn('kaspa_watchtower_multi_node_available{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_multi_node_verdict_value{node="test-node"} 1', metrics)
        self.assertIn('kaspa_watchtower_multi_node_risk_nodes{node="test-node"} 1', metrics)
        self.assertIn(
            'kaspa_watchtower_multi_node_daa_lag{history_node="mainnet-b",network="mainnet",node="test-node"} 300',
            metrics,
        )
        self.assertIn(
            'kaspa_watchtower_multi_node_flag{flag="stale_node",history_node="mainnet-b",network="mainnet",node="test-node"} 1',
            metrics,
        )

    def test_prometheus_metrics_emit_inactive_sync_progress(self):
        report = {
            "node_name": "test-node",
            "status": "ok",
            "severity": "ok",
            "checks": [],
            "grpc_metrics": {"is_synced": True},
            "progress": {
                "relay_blocks_in_window": 0,
                "relay_events_in_window": 0,
                "window_minutes": 10,
                "latest_relay_age_seconds": None,
            },
            "monitoring": {},
            "disk": {},
        }

        metrics = watchtower.format_prometheus_metrics(report, {}, {}, {})

        self.assertIn('kaspa_watchtower_sync_active{node="test-node"} 0', metrics)
        self.assertIn('kaspa_watchtower_sync_baseline_available{node="test-node"} 0', metrics)
        self.assertIn('kaspa_watchtower_sync_daa_rate_per_hour{node="test-node"} 0', metrics)

    def test_prometheus_metrics_emit_sdk_probe_metrics(self):
        report = {
            "node_name": "test-node",
            "status": "ok",
            "severity": "ok",
            "checks": [],
            "grpc_metrics": {},
            "sdk_metrics": {
                "enabled": True,
                "configured": True,
                "sdk_installed": True,
                "ok": True,
                "endpoint": "127.0.0.1:17110",
                "network_id": "mainnet",
                "encoding": "borsh",
                "rpc_latency_ms": 12.5,
                "peer_count": 8,
                "virtual_daa_score": 123456,
                "subscription_enabled": True,
                "subscription_ok": True,
                "subscription_events_total": 9,
                "subscription_last_event_age_seconds": 0.5,
                "subscription_block_added_total": 3,
                "subscription_virtual_chain_changed_total": 3,
                "subscription_virtual_daa_score_changed_total": 3,
                "subscription_watch_addresses": 1,
                "subscription_watch_targets": [{"label": "ops", "address": "kaspa:qtest"}],
                "subscription_utxos_added": 1,
                "events": [{"event_key": "old"}],
                "new_events": [{"event_key": "new"}],
            },
            "indexer_watch": {
                "watch_addresses": [{"label": "ops", "address": "kaspa:qtest"}],
                "events": [{"event_key": "idx"}],
                "new_events": [],
            },
            "progress": {
                "relay_blocks_in_window": 0,
                "window_minutes": 10,
                "latest_relay_age_seconds": None,
            },
            "monitoring": {},
            "disk": {},
        }

        metrics = watchtower.format_prometheus_metrics(report, {}, {}, {})

        self.assertIn('kaspa_watchtower_sdk_enabled{node="test-node"} 1', metrics)
        self.assertIn(
            'kaspa_watchtower_sdk_rpc_up{encoding="borsh",endpoint="127.0.0.1:17110",network="mainnet",node="test-node"} 1',
            metrics,
        )
        self.assertIn(
            'kaspa_watchtower_sdk_peer_count{encoding="borsh",endpoint="127.0.0.1:17110",network="mainnet",node="test-node"} 8',
            metrics,
        )
        self.assertIn(
            'kaspa_watchtower_sdk_subscription_events_total{encoding="borsh",endpoint="127.0.0.1:17110",network="mainnet",node="test-node"} 9',
            metrics,
        )
        self.assertIn(
            'kaspa_watchtower_sdk_subscription_utxos_added{encoding="borsh",endpoint="127.0.0.1:17110",network="mainnet",node="test-node"} 1',
            metrics,
        )
        self.assertIn(
            'kaspa_watchtower_sdk_event_history_total{encoding="borsh",endpoint="127.0.0.1:17110",network="mainnet",node="test-node"} 1',
            metrics,
        )
        self.assertIn(
            'kaspa_watchtower_sdk_new_events{encoding="borsh",endpoint="127.0.0.1:17110",network="mainnet",node="test-node"} 1',
            metrics,
        )
        self.assertIn('kaspa_watchtower_watch_source_addresses{node="test-node",source="both"} 1', metrics)
        self.assertIn('kaspa_watchtower_watch_source_events_total{node="test-node",source="sdk"} 1', metrics)

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

    def test_grafana_dashboard_includes_market_panels(self):
        dashboard = json.loads(Path("grafana/kaspa-watchtower.json").read_text(encoding="utf-8"))
        panels = {panel.get("title"): panel for panel in dashboard.get("panels", [])}

        self.assertIn("KAS/USDT Spot Price", panels)
        spot_targets = panels["KAS/USDT Spot Price"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_market_spot_price_usdt{node="$node"}'
                for target in spot_targets
            )
        )
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_market_spot_price_median_usdt{node="$node"}'
                for target in spot_targets
            )
        )
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_market_spot_price_dispersion_percent{node="$node"}'
                for target in spot_targets
            )
        )
        self.assertIn("KAS Futures Positioning", panels)
        futures_targets = panels["KAS Futures Positioning"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_market_futures_open_interest_kas{node="$node"}'
                for target in futures_targets
            )
        )
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_market_futures_basis_percent{node="$node"}'
                for target in futures_targets
            )
        )
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_market_futures_oi_volume_ratio{node="$node"}'
                for target in futures_targets
            )
        )
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_market_futures_funding_z_score{node="$node"}'
                for target in futures_targets
            )
        )

    def test_grafana_dashboard_includes_multi_node_panels(self):
        dashboard = json.loads(Path("grafana/kaspa-watchtower.json").read_text(encoding="utf-8"))
        panels = {panel.get("title"): panel for panel in dashboard.get("panels", [])}

        self.assertIn("Multi-Node Verdict", panels)
        verdict_targets = panels["Multi-Node Verdict"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_multi_node_verdict_value{node="$node"}'
                for target in verdict_targets
            )
        )
        self.assertIn("Multi-Node Node Lag", panels)
        lag_targets = panels["Multi-Node Node Lag"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_multi_node_daa_lag{node="$node"}'
                for target in lag_targets
            )
        )

    def test_grafana_dashboard_includes_sdk_probe_panels(self):
        dashboard = json.loads(Path("grafana/kaspa-watchtower.json").read_text(encoding="utf-8"))
        panels = {panel.get("title"): panel for panel in dashboard.get("panels", [])}

        self.assertIn("SDK RPC Up", panels)
        self.assertIn("SDK RPC Latency", panels)
        self.assertIn("SDK DAA / Peers", panels)
        self.assertIn("SDK Subscription Events", panels)
        self.assertIn("SDK Subscription Freshness", panels)
        self.assertIn("SDK UTXO Watch Fallback", panels)
        self.assertIn("SDK Persisted Watch Events", panels)
        self.assertIn("Watch Source Coverage", panels)
        self.assertIn("Indexer Watch Events", panels)
        self.assertIn("Watchlist Ready State", panels)
        self.assertIn("Watchlist Balance", panels)
        self.assertIn("Watchlist UTXO / Tx Count", panels)
        targets = panels["SDK DAA / Peers"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_sdk_virtual_daa_score{node="$node"}'
                for target in targets
            )
        )
        subscription_targets = panels["SDK Subscription Events"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_sdk_subscription_events_total{node="$node"}'
                for target in subscription_targets
            )
        )
        indexer_watch_targets = panels["Indexer Watch Events"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_indexer_watch_events_total{node="$node"}'
                for target in indexer_watch_targets
            )
        )
        ready_targets = panels["Watchlist Ready State"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_sdk_subscription_watch_addresses{node="$node"}'
                for target in ready_targets
            )
        )
        balance_targets = panels["Watchlist Balance"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_indexer_watch_address_balance_kas{node="$node"}'
                for target in balance_targets
            )
        )
        count_targets = panels["Watchlist UTXO / Tx Count"].get("targets") or []
        self.assertTrue(
            any(
                target.get("expr") == 'kaspa_watchtower_indexer_watch_address_utxos{node="$node"}'
                for target in count_targets
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

    def test_active_peer_count_failure_is_critical(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "rusty-kaspa.log"
            latest = dt.datetime.now().astimezone()
            log_path.write_text(
                f"{latest.strftime('%Y-%m-%d %H:%M:%S.%f%z')[:-2]}:{latest.strftime('%z')[-2:]} "
                "[INFO ] Processed 10 blocks and 10 headers in the last 10.00s "
                "(20 transactions; 10 UTXO-validated blocks)\n",
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
                        "active_peers": 0,
                    },
                ),
                mock.patch.object(watchtower, "dir_size", return_value="1G"),
            ):
                report = watchtower.build_report(config)

            checks = {check["name"]: check for check in report["checks"]}
            self.assertEqual(report["severity"], "critical")
            self.assertFalse(checks["active_peer_count"]["ok"])
            self.assertIn("0 active peers", checks["active_peer_count"]["detail"])
            self.assertIn("threshold=1", checks["active_peer_count"]["detail"])

    def test_operational_enrichment_tracks_health_incident_and_causes(self):
        report = {
            "node_name": "test-node",
            "status": "alert",
            "severity": "critical",
            "checked_at": "2026-06-10T09:00:00+09:00",
            "checks": [
                {"name": "process", "ok": False, "detail": "not running"},
                {"name": "disk_free", "ok": False, "detail": "4.00 GiB free"},
                {"name": "peer_count", "ok": True, "detail": "8 peers"},
            ],
            "progress": {},
            "grpc_metrics": {},
            "disk": {},
        }
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        state = {"current_incident": {"started_at": "2026-06-10T08:45:00+09:00"}}

        watchtower.enrich_operational_fields(report, config, state)

        self.assertEqual(report["health_score"], 55)
        self.assertTrue(report["incident"]["active"])
        self.assertEqual(report["incident"]["duration_seconds"], 900.0)
        self.assertEqual(report["failure_causes"], ["process down", "disk free space below threshold"])

    def test_maintenance_mutes_warning_but_not_critical_when_configured(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["maintenance"] = {
            "enabled": False,
            "mute_until": "2026-06-10T10:00:00+09:00",
            "critical_only": True,
            "reason": "planned restart",
        }
        warning_report = {
            "status": "alert",
            "severity": "warn",
            "checked_at": "2026-06-10T09:00:00+09:00",
            "checks": [],
        }
        critical_report = {**warning_report, "severity": "critical"}

        watchtower.enrich_operational_fields(warning_report, config, {})
        watchtower.enrich_operational_fields(critical_report, config, {})

        self.assertTrue(watchtower.alert_muted_by_maintenance(warning_report))
        self.assertFalse(watchtower.alert_muted_by_maintenance(critical_report))

    def test_update_maintenance_config_mutes_and_unmutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "node_name": "test-node",
                        "maintenance": {
                            "enabled": False,
                            "mute_until": "",
                            "critical_only": True,
                            "reason": "",
                        },
                    }
                ),
                encoding="utf-8",
            )
            now = dt.datetime(2026, 6, 10, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))

            status = watchtower.update_maintenance_config(
                path,
                mute_for_minutes=30,
                critical_only=False,
                reason="node upgrade",
                now=now,
            )

            self.assertTrue(status["active"])
            self.assertFalse(status["critical_only"])
            self.assertEqual(status["mute_until"], "2026-06-10T09:30:00+09:00")
            self.assertEqual(status["reason"], "node upgrade")

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["maintenance"]["mute_until"], "2026-06-10T09:30:00+09:00")

            status = watchtower.update_maintenance_config(path, unmute=True, now=now)

            self.assertFalse(status["active"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["maintenance"]["mute_until"], "")
            self.assertEqual(saved["maintenance"]["reason"], "")

    def test_update_mining_address_config_sets_and_clears_address(self):
        address = "kaspa:" + "q" * 61
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"node_name": "test-node"}), encoding="utf-8")

            mining = watchtower.update_mining_address_config(path, address=address)

            self.assertEqual(mining["wallet_address"], address)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["mining"]["wallet_address"], address)

            mining = watchtower.update_mining_address_config(path, clear=True)

            self.assertEqual(mining["wallet_address"], "")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["mining"]["wallet_address"], "")

    def test_update_mining_address_config_rejects_non_kaspa_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"node_name": "test-node"}), encoding="utf-8")

            with self.assertRaises(ValueError):
                watchtower.update_mining_address_config(path, address="not-an-address")

    def test_mining_wallet_address_falls_back_to_mining_watch_address(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        address = "kaspa:" + "p" * 61
        config["wallet"]["watch_addresses"] = [
            {"label": "ops", "address": "kaspa:" + "o" * 61},
            {"label": "mining", "address": address},
        ]

        resolved, source = watchtower.mining_wallet_address(config)

        self.assertEqual(resolved, address)
        self.assertEqual(source, "wallet.watch_addresses")

    def test_update_incident_state_resolves_active_incident(self):
        state = {
            "current_incident": {
                "started_at": "2026-06-10T08:45:00+09:00",
                "first_severity": "critical",
                "first_failed_checks": ["process"],
            }
        }
        report = {
            "checked_at": "2026-06-10T09:10:00+09:00",
            "status": "ok",
            "severity": "ok",
            "checks": [],
        }

        event = watchtower.update_incident_state(state, report)

        self.assertEqual(event, "incident_resolved")
        self.assertNotIn("current_incident", state)
        self.assertEqual(state["last_incident"]["resolved_at"], "2026-06-10T09:10:00+09:00")

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
            "indexer": {
                "enabled": True,
                "ok": True,
                "health_ok": True,
                "metrics_ok": True,
                "metrics": {"lag_seconds": 4, "checkpoint_age_seconds": 9},
            },
            "indexer_watch": {
                "enabled": True,
                "ok": True,
                "watch_addresses": [{"label": "mining", "address": "kaspa:qabc"}],
                "events": [{"tx_id": "tx1"}],
                "new_events": [],
            },
            "disk": {"exists": True, "free_gb": 100, "free_percent": 20},
        }

        text = watchtower.format_summary(report)

        self.assertIn("processed=tx_rate=131.10/s age=2.5s tx=1311 blocks=92 window=10.0s", text)
        self.assertIn(
            "indexer=enabled=True state=unknown ok=True health=True syncing=False metrics=True lag=4 checkpoint_age=9",
            text,
        )
        self.assertIn("indexer_watch=enabled=True ok=True addresses=1 events=1 new=0", text)

    def test_format_summary_treats_disabled_indexer_as_skipped(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "checked_at": "2026-06-13T21:30:00+09:00",
            "status": "ok",
            "severity": "ok",
            "checks": [],
            "grpc_metrics": {},
            "progress": {},
            "indexer": {"enabled": False},
            "indexer_watch": {"enabled": False, "ok": True},
            "disk": {"exists": True, "free_gb": 255, "free_percent": 27.5},
        }

        text = watchtower.format_summary(report)

        self.assertIn(
            "indexer=enabled=False state=disabled ok=True health=skipped "
            "syncing=False metrics=skipped lag=disabled checkpoint_age=disabled",
            text,
        )

    def test_format_discord_status_is_operator_friendly(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "checked_at": "2026-06-10T10:00:00+09:00",
            "status": "alert",
            "severity": "warn",
            "health_score": 85,
            "checks": [{"name": "disk_free", "ok": False, "detail": "low"}],
            "failure_causes": ["disk free space below threshold"],
            "grpc_metrics": {
                "network_id": "mainnet",
                "is_synced": True,
                "peer_count": 8,
                "active_peers": 8,
                "virtual_daa_score": 12345,
            },
            "incident": {
                "active": True,
                "duration_seconds": 600,
                "failed_checks": ["disk_free"],
                "causes": ["disk free space below threshold"],
            },
            "maintenance": {
                "active": True,
                "mute_until": "2026-06-10T10:30:00+09:00",
            },
            "wallet": {
                "enabled": True,
                "ok": True,
                "entries": [{"address": "kaspa:qqqq", "balance_sompi": 123456789}],
                "total_sompi": 123456789,
            },
            "indexer": {
                "enabled": True,
                "ok": True,
                "state": "syncing",
                "metrics": {"lag_seconds": 30, "checkpoint_age_seconds": 45},
            },
        }

        text = watchtower.format_discord_status(report)

        self.assertIn("Kaspa status: kaspa-mainnet-local", text)
        self.assertIn("status=alert severity=warn health_score=85", text)
        self.assertIn("node=network=mainnet synced=True peers=8 active=8 daa=12345", text)
        self.assertIn("ops=incident=10.0m maintenance=active", text)
        self.assertIn("wallet=enabled=True ok=True addresses=1 total=1.23456789 KAS", text)
        self.assertIn("indexer=enabled=True state=syncing ok=True lag=30 checkpoint_age=45", text)
        self.assertIn("failed_checks=disk_free", text)

    def test_wallet_balances_are_watch_only_grpc_reads(self):
        fetch = mock.Mock(
            return_value={
                "ok": True,
                "entries": [
                    {"address": "kaspa:qqtest1", "balance_sompi": 150000000},
                    {"address": "kaspa:qqtest2", "balance_sompi": 250000000},
                ],
            }
        )
        fetch_mempool = mock.Mock(return_value={"ok": True, "entries": []})
        fake_probe = mock.Mock(
            fetch_balances_by_addresses=fetch,
            fetch_mempool_entries_by_addresses=fetch_mempool,
        )
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["wallet"] = {
            "enabled": True,
            "watch_addresses": [
                {"label": "mining", "address": "kaspa:qqtest1"},
                {"label": "ops", "address": "kaspa:qqtest2"},
            ],
        }

        with mock.patch.dict(sys.modules, {"kaspa_grpc_probe": fake_probe}):
            wallet = watchtower.fetch_optional_wallet_balances(config, "127.0.0.1:16110")

        fetch.assert_called_once_with("127.0.0.1:16110", ["kaspa:qqtest1", "kaspa:qqtest2"])
        fetch_mempool.assert_called_once_with("127.0.0.1:16110", ["kaspa:qqtest1", "kaspa:qqtest2"])
        self.assertTrue(wallet["ok"])
        self.assertEqual(wallet["total_sompi"], 400000000)
        self.assertEqual(wallet["total_kas"], 4.0)
        self.assertEqual(wallet["entries"][0]["label"], "mining")

    def test_wallet_change_detection_skips_first_baseline(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["wallet"] = {
            "enabled": True,
            "alert_on_change": True,
            "alert_min_delta_sompi": 1,
            "watch_addresses": [{"label": "mining", "address": "kaspa:qqtest1"}],
        }
        report = {
            "wallet": {
                "enabled": True,
                "ok": True,
                "total_sompi": 150000000,
                "entries": [{"label": "mining", "address": "kaspa:qqtest1", "balance_sompi": 150000000}],
            }
        }

        event = watchtower.apply_wallet_change_detection(report, {}, config)

        self.assertIsNone(event)
        self.assertFalse(report["wallet"]["change"]["changed"])
        self.assertEqual(report["wallet"]["change"]["detail"], "baseline recorded")

    def test_wallet_change_detection_emits_change_event(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["wallet"] = {
            "enabled": True,
            "alert_on_change": True,
            "alert_min_delta_sompi": 100,
            "watch_addresses": [{"label": "mining", "address": "kaspa:qqtest1"}],
        }
        report = {
            "wallet": {
                "enabled": True,
                "ok": True,
                "total_sompi": 200000000,
                "entries": [{"label": "mining", "address": "kaspa:qqtest1", "balance_sompi": 200000000}],
            }
        }
        state = {
            "last_report": {
                "wallet": {
                    "enabled": True,
                    "ok": True,
                    "total_sompi": 150000000,
                    "entries": [{"label": "mining", "address": "kaspa:qqtest1", "balance_sompi": 150000000}],
                }
            }
        }

        event = watchtower.apply_wallet_change_detection(report, state, config)

        self.assertEqual(event, "wallet_changed")
        self.assertTrue(report["wallet"]["change"]["changed"])
        self.assertEqual(report["wallet"]["change"]["total_delta_sompi"], 50000000)
        self.assertEqual(report["wallet"]["change"]["entries"][0]["delta_sompi"], 50000000)

    def test_wallet_change_detection_honors_direction_policy(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["wallet"] = {
            "enabled": True,
            "alert_on_change": True,
            "alert_min_delta_sompi": 1,
            "alert_directions": "incoming",
            "watch_addresses": [{"label": "ops", "address": "kaspa:qqtest1"}],
        }
        report = {
            "wallet": {
                "enabled": True,
                "ok": True,
                "total_sompi": 100000000,
                "entries": [{"label": "ops", "address": "kaspa:qqtest1", "balance_sompi": 100000000}],
            }
        }
        state = {
            "last_report": {
                "wallet": {
                    "enabled": True,
                    "ok": True,
                    "total_sompi": 200000000,
                    "entries": [{"label": "ops", "address": "kaspa:qqtest1", "balance_sompi": 200000000}],
                }
            }
        }

        event = watchtower.apply_wallet_change_detection(report, state, config)

        self.assertIsNone(event)
        self.assertEqual(report["wallet"]["change"]["alert_entries"], [])

    def test_wallet_change_detection_flags_large_outgoing(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["wallet"] = {
            "enabled": True,
            "alert_on_change": True,
            "alert_min_delta_sompi": 1,
            "large_outgoing_alert_sompi": 50000000,
            "watch_addresses": [{"label": "ops", "address": "kaspa:qqtest1"}],
        }
        report = {
            "wallet": {
                "enabled": True,
                "ok": True,
                "total_sompi": 100000000,
                "entries": [{"label": "ops", "address": "kaspa:qqtest1", "balance_sompi": 100000000}],
            }
        }
        state = {
            "last_report": {
                "wallet": {
                    "enabled": True,
                    "ok": True,
                    "total_sompi": 200000000,
                    "entries": [{"label": "ops", "address": "kaspa:qqtest1", "balance_sompi": 200000000}],
                }
            }
        }

        event = watchtower.apply_wallet_change_detection(report, state, config)

        self.assertEqual(event, "wallet_large_outgoing")
        self.assertEqual(report["wallet"]["change"]["large_outgoing_entries"][0]["delta_sompi"], -100000000)

    def test_update_wallet_event_state_records_balance_changes(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["wallet"]["event_history_entries"] = 2
        report = {
            "checked_at": "2026-06-11T07:50:00+09:00",
            "wallet": {
                "change": {
                    "changed": True,
                    "entries": [
                        {
                            "address": "kaspa:qqtest1",
                            "label": "mining",
                            "previous_sompi": 100000000,
                            "current_sompi": 150000000,
                            "delta_sompi": 50000000,
                            "delta_kas": 0.5,
                        }
                    ],
                }
            },
        }
        state = {"wallet_events": [{"observed_at": "old"}, {"observed_at": "older"}]}

        events = watchtower.update_wallet_event_state(state, report, config)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["direction"], "incoming")
        self.assertEqual(events[0]["delta_sompi"], 50000000)
        self.assertEqual(len(state["wallet_events"]), 2)
        self.assertEqual(state["wallet_events"][-1]["label"], "mining")

    def test_update_wallet_event_state_dedupes_repeated_events(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        report = {
            "checked_at": "2026-06-11T07:50:00+09:00",
            "wallet": {
                "change": {
                    "changed": True,
                    "entries": [
                        {
                            "address": "kaspa:qqtest1",
                            "label": "mining",
                            "previous_sompi": 100000000,
                            "current_sompi": 150000000,
                            "delta_sompi": 50000000,
                        }
                    ],
                }
            },
        }
        existing = {
            "event_key": "2026-06-11T07:50:00+09:00|kaspa:qqtest1|50000000",
            "observed_at": "2026-06-11T07:50:00+09:00",
            "address": "kaspa:qqtest1",
            "delta_sompi": 50000000,
        }
        state = {"wallet_events": [existing]}

        events = watchtower.update_wallet_event_state(state, report, config)

        self.assertEqual(events, [])
        self.assertEqual(state["wallet_events"], [existing])

    def test_update_sdk_subscription_event_state_records_utxo_events(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["sdk_probe"]["event_history_entries"] = 2
        report = {
            "checked_at": "2026-06-19T21:00:00+09:00",
            "sdk_metrics": {
                "subscription_utxo_events": [
                    {
                        "direction": "incoming",
                        "address": "kaspa:qtest",
                        "tx_id": "tx1",
                        "amount_sompi": 123000000,
                    }
                ]
            },
        }
        state = {"sdk_subscription_events": [{"event_key": "old"}, {"event_key": "older"}]}

        events = watchtower.update_sdk_subscription_event_state(state, report, config)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_key"], "sdk_subscription|incoming|tx1|kaspa:qtest|123000000")
        self.assertEqual(len(state["sdk_subscription_events"]), 2)
        self.assertEqual(report["sdk_metrics"]["new_events"], events)

    def test_update_sdk_subscription_event_state_dedupes_repeated_events(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        existing = {
            "event_key": "sdk_subscription|incoming|tx1|kaspa:qtest|123000000",
            "direction": "incoming",
            "address": "kaspa:qtest",
            "tx_id": "tx1",
            "amount_sompi": 123000000,
        }
        report = {
            "checked_at": "2026-06-19T21:00:00+09:00",
            "sdk_metrics": {
                "subscription_utxo_events": [
                    {
                        "direction": "incoming",
                        "address": "kaspa:qtest",
                        "tx_id": "tx1",
                        "amount_sompi": 123000000,
                    }
                ]
            },
        }
        state = {"sdk_subscription_events": [existing]}

        events = watchtower.update_sdk_subscription_event_state(state, report, config)

        self.assertEqual(events, [])
        self.assertEqual(state["sdk_subscription_events"], [existing])

    def test_format_alert_includes_sdk_watch_events(self):
        report = {
            "node_name": "test-node",
            "checked_at": "2026-06-19T21:00:00+09:00",
            "status": "ok",
            "severity": "ok",
            "health_score": 100,
            "checks": [],
            "latest_throughput": None,
            "grpc_metrics": {},
            "progress": {
                "relay_blocks_in_window": 0,
                "relay_events_in_window": 0,
                "window_minutes": 10,
                "latest_relay_age_seconds": None,
            },
            "disk": {},
            "sdk_metrics": {
                "subscription_watch_addresses": 1,
                "new_events": [
                    {
                        "direction": "incoming",
                        "label": "mining",
                        "source": "sdk_subscription",
                        "address": "kaspa:qtest",
                        "tx_id": "abcdef1234567890",
                        "amount_sompi": 123000000,
                    }
                ],
            },
        }

        text = watchtower.format_alert(report, event="sdk_watch_event")

        self.assertIn("SDK watched address tx", text)
        self.assertIn("SDK watch: watched=1 new_events=1 total_events=0", text)
        self.assertIn("- mining source=sdk_subscription direction=incoming", text)
        self.assertIn("amount=1.23000000 KAS", text)

    def test_mining_reward_summary_uses_mining_incoming_events(self):
        now = dt.datetime(2026, 6, 11, 8, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        events = [
            {
                "observed_at": "2026-06-11T07:50:00+09:00",
                "direction": "incoming",
                "label": "mining",
                "delta_sompi": 200000000,
            },
            {
                "observed_at": "2026-06-10T07:50:00+09:00",
                "direction": "incoming",
                "label": "ops",
                "delta_sompi": 999000000,
            },
            {
                "observed_at": "2026-06-09T07:50:00+09:00",
                "direction": "outgoing",
                "label": "mining",
                "delta_sompi": -100000000,
            },
        ]

        summary = watchtower.mining_reward_summary(events, price_usdt=0.2, now=now)

        self.assertEqual(summary["candidate_events"], 1)
        self.assertEqual(summary["today_kas"], 2.0)
        self.assertEqual(summary["seven_day_kas"], 2.0)
        self.assertEqual(summary["today_usd"], 0.4)
        self.assertEqual(summary["latest_reward_at"], "2026-06-11T07:50:00+09:00")
        self.assertAlmostEqual(summary["latest_reward_age_hours"], 1 / 6)

    def test_whale_events_from_mempool_detects_single_large_output(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["whale_watch"] = {
            **config["whale_watch"],
            "enabled": True,
            "min_amount_sompi": 100_000_000_000_000,
            "explorer_base_url": "https://explorer.example",
        }
        mempool = {
            "ok": True,
            "entries": [
                {
                    "tx_id": "abc123",
                    "fee_sompi": 1000,
                    "outputs": [
                        {"address": "kaspa:" + "q" * 61, "amount_sompi": 99_000_000_000_000},
                        {"address": "kaspa:" + "p" * 61, "amount_sompi": 100_000_000_000_000},
                    ],
                    "total_output_sompi": 199_000_000_000_000,
                    "largest_output_sompi": 100_000_000_000_000,
                    "input_count": 2,
                    "output_count": 2,
                }
            ],
        }

        events = watchtower.whale_events_from_mempool(mempool, config, "2026-06-11T08:00:00+09:00")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "whale_tx_pending")
        self.assertEqual(events[0]["amount_sompi"], 100_000_000_000_000)
        self.assertEqual(events[0]["amount_kas"], 1_000_000)
        self.assertEqual(events[0]["tx_url"], "https://explorer.example/txs/abc123")
        self.assertTrue(events[0]["address_url"].startswith("https://explorer.example/addresses/kaspa:"))

    def test_whale_events_from_confirmed_detects_single_large_output(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["whale_watch"] = {
            **config["whale_watch"],
            "enabled": True,
            "min_amount_sompi": 100_000_000_000_000,
        }
        chain = {
            "ok": True,
            "entries": [
                {
                    "tx_id": "abc123",
                    "accepting_block_hash": "block123",
                    "outputs": [
                        {"address": "kaspa:" + "p" * 61, "amount_sompi": 125_000_000_000_000},
                    ],
                    "total_output_sompi": 125_000_000_000_000,
                    "largest_output_sompi": 125_000_000_000_000,
                    "input_count": 1,
                    "output_count": 1,
                }
            ],
        }

        events = watchtower.whale_events_from_confirmed(chain, config, "2026-06-11T08:05:00+09:00")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "whale_tx_confirmed")
        self.assertEqual(events[0]["source"], "confirmed")
        self.assertEqual(events[0]["accepting_block_hash"], "block123")

    def test_whale_explorer_url_requires_http_base(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["whale_watch"] = {
            **config["whale_watch"],
            "explorer_base_url": "ftp://explorer.example",
        }

        self.assertEqual(watchtower.whale_explorer_url(config, "tx", "abc123"), "")

    def test_update_whale_event_state_dedupes_repeated_events(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["whale_watch"]["event_history_entries"] = 5
        event = {
            "event_key": "mempool|abc123|100000000000000|kaspa:q",
            "observed_at": "2026-06-11T08:00:00+09:00",
            "type": "whale_tx_pending",
            "source": "mempool",
            "tx_id": "abc123",
            "address": "kaspa:q",
            "amount_sompi": 100_000_000_000_000,
        }
        state = {"whale_events": [event]}
        report = {"whale_watch": {"candidates": [dict(event)]}}

        events = watchtower.update_whale_event_state(state, report, config)

        self.assertEqual(events, [])
        self.assertEqual(state["whale_events"], [event])

    def test_update_whale_event_state_marks_pending_confirmed(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        pending = {
            "event_key": "mempool|abc123|100000000000000|kaspa:q",
            "observed_at": "2026-06-11T08:00:00+09:00",
            "type": "whale_tx_pending",
            "source": "mempool",
            "tx_id": "abc123",
            "address": "kaspa:q",
            "amount_sompi": 100_000_000_000_000,
        }
        confirmed = {
            "event_key": "confirmed|abc123|100000000000000|kaspa:q",
            "observed_at": "2026-06-11T08:05:00+09:00",
            "type": "whale_tx_confirmed",
            "source": "confirmed",
            "tx_id": "abc123",
            "address": "kaspa:q",
            "amount_sompi": 100_000_000_000_000,
            "accepting_block_hash": "block123",
        }
        state = {"whale_events": [pending]}
        report = {"whale_watch": {"candidates": [confirmed]}}

        events = watchtower.update_whale_event_state(state, report, config)

        self.assertEqual(events, [confirmed])
        self.assertEqual(state["whale_events"][0]["status"], "confirmed")
        self.assertEqual(state["whale_events"][0]["accepting_block_hash"], "block123")

    def test_wallet_policy_checks_warn_when_mining_reward_stale(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["wallet"] = {
            **config["wallet"],
            "enabled": True,
            "mining_reward_stale_hours": 1,
        }
        report = {
            "checked_at": "2026-06-11T08:00:00+09:00",
            "status": "ok",
            "severity": "ok",
            "checks": [],
            "recovery": {},
            "wallet": {
                "enabled": True,
                "events": [
                    {
                        "observed_at": "2026-06-11T06:00:00+09:00",
                        "direction": "incoming",
                        "label": "mining",
                        "delta_sompi": 100000000,
                    }
                ],
            },
        }

        watchtower.apply_wallet_policy_checks(report, config)

        checks = {check["name"]: check for check in report["checks"]}
        self.assertFalse(checks["mining_reward_freshness"]["ok"])
        self.assertEqual(report["severity"], "warn")

    def test_mining_policy_checks_warn_when_enabled_miner_not_running(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["mining"] = {
            **config["mining"],
            "enabled": True,
            "process_match": "definitely-not-running-kaspa-miner",
        }
        report = {
            "status": "ok",
            "severity": "ok",
            "checks": [],
            "mining": {
                "enabled": True,
                "running": False,
                "detail": "not running",
            },
        }

        watchtower.apply_mining_policy_checks(report, config)

        checks = {check["name"]: check for check in report["checks"]}
        self.assertFalse(checks["mining_process"]["ok"])
        self.assertEqual(report["severity"], "warn")

    def test_format_discord_wallet_lists_balances(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "wallet": {
                "enabled": True,
                "configured": True,
                "ok": True,
                "detail": "read ok",
                "entries": [
                    {
                        "label": "mining",
                        "address": "kaspa:qqqqqqqqqqqqqqqqqqqqqqqqqqqq",
                        "balance_sompi": 123456789,
                    }
                ],
                "total_sompi": 123456789,
                "change": {
                    "changed": True,
                    "total_delta_sompi": 100000000,
                },
            },
        }

        text = watchtower.format_discord_wallet(report)

        self.assertIn("Kaspa wallet watch: kaspa-mainnet-local", text)
        self.assertIn("total=1.23456789 KAS", text)
        self.assertIn("change=changed=True delta=1.00000000 KAS", text)
        self.assertIn("- mining: 1.23456789 KAS", text)

    def test_format_discord_wallet_txs_lists_pending_and_events(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "wallet": {
                "pending": {
                    "ok": True,
                    "entries": [
                        {
                            "address": "kaspa:qqqqqqqqqqqqqqqqqqqqqqqqqqqq",
                            "direction": "receiving",
                            "tx_id": "abcdef1234567890",
                            "amount_sompi": 100000000,
                            "fee_sompi": 1000,
                        }
                    ],
                },
                "events": [
                    {
                        "observed_at": "2026-06-11T07:50:00+09:00",
                        "direction": "incoming",
                        "label": "mining",
                        "address": "kaspa:qqqqqqqqqqqqqqqqqqqqqqqqqqqq",
                        "delta_sompi": 50000000,
                    }
                ],
            },
        }

        text = watchtower.format_discord_wallet_txs(report)

        self.assertIn("Kaspa wallet txs: kaspa-mainnet-local", text)
        self.assertIn("pending_ok=True pending=1 events=1", text)
        self.assertIn("- receiving: amount=1.00000000 KAS", text)
        self.assertIn("incoming mining delta=0.50000000 KAS", text)

    def test_format_discord_mining_lists_miner_state(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "mining": {
                "enabled": True,
                "configured": True,
                "ok": True,
                "running": True,
                "mode": "macos-gpu-experimental",
                "hashrate_hs": 42_500_000,
                "accepted_shares": 7,
                "rejected_shares": 1,
                "last_share_at": "2026-06-11T08:02:00+09:00",
                "pool_url": "stratum+tcp://pool.example:16110",
                "worker_name": "macos-gpu-test",
                "wallet_address": "kaspa:" + "q" * 61,
                "wallet_address_source": "mining.wallet_address",
                "detail": "running",
            },
        }

        text = watchtower.format_discord_mining(report)

        self.assertIn("Kaspa mining: kaspa-mainnet-local", text)
        self.assertIn("running=True mode=macos-gpu-experimental", text)
        self.assertIn("hashrate=42.50 MH/s accepted=7 rejected=1", text)
        self.assertIn("address_source=mining.wallet_address", text)

    def test_format_discord_whales_lists_recent_events(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "whale_watch": {
                "enabled": True,
                "ok": True,
                "min_amount_sompi": 100_000_000_000_000,
                "mempool_entries": 12,
                "candidates": [],
                "detail": "mempool read ok",
                "events": [
                    {
                        "observed_at": dt.datetime.now().astimezone().isoformat(),
                        "source": "mempool",
                        "tx_id": "abcdef1234567890",
                        "amount_sompi": 125_000_000_000_000,
                        "tx_url": "https://explorer.example/txs/abcdef1234567890",
                    }
                ],
            },
        }

        text = watchtower.format_discord_whales(report)

        self.assertIn("Kaspa whales: kaspa-mainnet-local", text)
        self.assertIn("threshold=1000000.00000000 KAS", text)
        self.assertIn("24h_count=1", text)
        self.assertIn("amount=1250000.00000000 KAS", text)
        self.assertIn("link=https://explorer.example/txs/abcdef1234567890", text)

    def test_format_whale_daily_report_includes_counts_and_link(self):
        report = {
            "whale_watch": {
                "enabled": True,
                "ok": True,
                "min_amount_sompi": 100_000_000_000_000,
                "mempool_entries": 4,
                "candidates": [],
                "confirmed_candidates": [],
                "detail": "mempool read ok",
                "confirmed_detail": "no virtual chain movement",
                "explorer_base_url": "https://explorer.example",
                "events": [
                    {
                        "observed_at": dt.datetime.now().astimezone().isoformat(),
                        "type": "whale_tx_confirmed",
                        "source": "confirmed",
                        "status": "new",
                        "tx_id": "abcdef1234567890",
                        "amount_sompi": 125_000_000_000_000,
                        "tx_url": "https://explorer.example/txs/abcdef1234567890",
                    }
                ],
            },
        }

        text = watchtower.format_whale_daily_report(report)

        self.assertIn("24h_count=1", text)
        self.assertIn("confirmed_24h=1", text)
        self.assertIn("24h_volume=1250000.00000000 KAS", text)
        self.assertIn("link=https://explorer.example/txs/abcdef1234567890", text)

    def test_format_discord_incidents_includes_current_and_recovery(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "status": "alert",
            "severity": "critical",
            "checks": [{"name": "peer_count", "ok": False, "detail": "0 peers"}],
            "incident": {
                "active": True,
                "started_at": "2026-06-10T09:30:00+09:00",
                "duration_seconds": 1800,
                "failed_checks": ["peer_count"],
                "causes": ["peer count below threshold"],
            },
        }
        state = {"last_incident": {"resolved_at": "2026-06-10T08:00:00+09:00"}}

        text = watchtower.format_discord_incidents(
            report,
            state,
            [{"action": "executed", "operator_required": True, "operator_reason": "post_recovery_unhealthy"}],
        )

        self.assertIn("current_active=True duration=30.0m", text)
        self.assertIn("current_failed_checks=peer_count", text)
        self.assertIn("last_resolved_at=2026-06-10T08:00:00+09:00", text)
        self.assertIn("latest_recovery=action=executed operator_required=True reason=post_recovery_unhealthy", text)

    def test_format_operator_incident_summary_includes_daily_ops_context(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "status": "alert",
            "severity": "warn",
            "health_score": 85,
            "checks": [{"name": "disk_free", "ok": False, "detail": "low"}],
            "failure_causes": ["disk free space below threshold"],
            "incident": {
                "active": True,
                "started_at": "2026-06-10T09:30:00+09:00",
                "duration_seconds": 900,
                "failed_checks": ["disk_free"],
                "causes": ["disk free space below threshold"],
            },
            "maintenance": {
                "active": True,
                "critical_only": True,
                "mute_until": "2026-06-10T10:30:00+09:00",
                "reason": "planned restart",
            },
        }
        state = {"last_incident": {"resolved_at": "2026-06-10T08:00:00+09:00"}}

        text = watchtower.format_operator_incident_summary(
            report,
            state,
            [{"action": "dry_run", "severity_before": "warn", "severity_after": "unknown", "reason": "manual mode"}],
        )

        self.assertIn("health_score=85", text)
        self.assertIn("incident_duration=15.0m", text)
        self.assertIn("incident_failed_checks=disk_free", text)
        self.assertIn("maintenance_active=True", text)
        self.assertIn("maintenance_until=2026-06-10T10:30:00+09:00", text)
        self.assertIn("latest_recovery=action=dry_run before=warn after=unknown reason=manual mode", text)

    def test_discord_query_commands_succeed_even_when_node_alerts(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "checked_at": "2026-06-10T10:00:00+09:00",
            "status": "alert",
            "severity": "critical",
            "health_score": 70,
            "checks": [{"name": "grpc_metrics", "ok": False, "detail": "missing"}],
            "grpc_metrics": {},
            "incident": {"active": True, "duration_seconds": 60},
            "maintenance": {"active": False},
        }
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)

        with (
            mock.patch.object(watchtower, "build_stateful_report", return_value=(report, {})),
            mock.patch.object(watchtower, "recent_recovery_records", return_value=[]),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(watchtower.discord_command(config, "status"), 0)
            self.assertEqual(watchtower.discord_command(config, "incidents"), 0)

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

    def test_format_alert_reports_indexer_ready_event(self):
        report = {
            "node_name": "kaspa-mainnet-local",
            "checked_at": "2026-06-13T17:40:00+09:00",
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
            "indexer": {
                "enabled": True,
                "state": "up",
                "metrics": {
                    "checkpoint_age_seconds": 12,
                    "lag_seconds": 0,
                },
            },
            "progress": {
                "relay_blocks_in_window": 10,
                "relay_events_in_window": 5,
                "window_minutes": 10,
            },
            "recovery": {"action": "none"},
        }

        text = watchtower.format_alert(report, "ok", "ok", event="indexer_ready")

        self.assertIn("indexer ready", text)
        self.assertIn("상태: indexer catch-up completed", text)
        self.assertIn("state=up", text)
        self.assertIn("checkpoint_age=12", text)

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

    def test_config_validation_checks_migration_paths_and_node_name(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["node_name"] = "Local Mainnet Node"
        config["sqlite_history_path"] = "/missing-parent/watchtower.txt"
        config["prometheus_metrics_path"] = "state/watchtower.metrics"

        failed = {
            check.name: check.detail
            for check in watchtower.config_validation_checks(config)
            if not check.ok
        }

        self.assertIn("node_name.format", failed)
        self.assertIn("sqlite_history_path", failed)
        self.assertIn("sqlite_history_path.suffix", failed)
        self.assertIn("prometheus_metrics_path.suffix", failed)
        self.assertIn("expected path ending in .sqlite or .db", failed["sqlite_history_path.suffix"])
        self.assertIn("expected path ending in .prom", failed["prometheus_metrics_path.suffix"])

    def test_config_validation_checks_indexer_settings(self):
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
        config["indexer"]["base_url"] = "localhost:8500"
        config["indexer"]["timeout_seconds"] = 0
        config["indexer"]["require_metrics"] = "yes"
        config["indexer_watch"]["event_history_entries"] = 0
        config["indexer_watch"]["watch_addresses"] = [{"label": "bad"}]

        failed = {
            check.name: check.detail
            for check in watchtower.config_validation_checks(config)
            if not check.ok
        }

        self.assertIn("indexer.base_url", failed)
        self.assertIn("indexer.timeout_seconds", failed)
        self.assertIn("indexer.require_metrics", failed)
        self.assertIn("indexer_watch.event_history_entries", failed)
        self.assertIn("indexer_watch.watch_addresses", failed)
        self.assertIn("expected empty or http(s) URL", failed["indexer.base_url"])

    def test_config_validation_checks_multi_node_env_thresholds(self):
        with mock.patch.dict(os.environ, {"MULTI_NODE_DAA_LAG_WARNING": "nope"}):
            failed = {
                check.name: check.detail
                for check in watchtower.config_validation_checks(copy.deepcopy(watchtower.DEFAULT_CONFIG))
                if not check.ok
            }

        self.assertIn("env.MULTI_NODE_DAA_LAG_WARNING", failed)
        self.assertIn("expected integer >= 0", failed["env.MULTI_NODE_DAA_LAG_WARNING"])

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
            policy_decision={
                "allowed": True,
                "reason": "policy_satisfied",
                "consecutive_failures": 3,
                "min_consecutive_failures": 3,
                "incident_duration_seconds": 600,
                "min_incident_seconds": 300,
                "maintenance_active": False,
            },
        )

        self.assertIn("Recovery decision:", text)
        self.assertIn("failed_checks=process,rpc_tcp", text)
        self.assertIn("restart_command_configured=True", text)
        self.assertIn("policy=allowed=True reason=policy_satisfied", text)
        self.assertIn("review command", text)

    def test_recovery_policy_allows_persistent_critical_incident(self):
        report = {
            "node_name": "test-node",
            "checked_at": "2026-06-10T09:10:00+09:00",
            "status": "alert",
            "severity": "critical",
            "checks": [{"name": "peer_count", "ok": False, "detail": "0 peers"}],
            "incident": {"active": True, "duration_seconds": 600},
            "maintenance": {"active": False},
            "grpc_metrics": {},
            "progress": {},
        }
        state = {
            "history": [
                {
                    "checked_at": "2026-06-10T09:00:00+09:00",
                    "status": "alert",
                    "severity": "critical",
                    "failed_checks": ["peer_count"],
                },
                {
                    "checked_at": "2026-06-10T09:05:00+09:00",
                    "status": "alert",
                    "severity": "critical",
                    "failed_checks": ["peer_count"],
                },
            ]
        }

        decision = watchtower.recovery_policy_decision(report, state, copy.deepcopy(watchtower.DEFAULT_CONFIG))

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["reason"], "policy_satisfied")
        self.assertEqual(decision["consecutive_failures"], 3)

    def test_recovery_policy_blocks_warning_and_maintenance(self):
        report = {
            "node_name": "test-node",
            "checked_at": "2026-06-10T09:10:00+09:00",
            "status": "alert",
            "severity": "warn",
            "checks": [{"name": "disk_free", "ok": False, "detail": "low"}],
            "incident": {"active": True, "duration_seconds": 600},
            "maintenance": {"active": False},
            "grpc_metrics": {},
            "progress": {},
        }
        state = {"history": []}
        config = copy.deepcopy(watchtower.DEFAULT_CONFIG)

        decision = watchtower.recovery_policy_decision(report, state, config)
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "severity_not_critical")

        report["severity"] = "critical"
        report["maintenance"] = {"active": True}
        decision = watchtower.recovery_policy_decision(report, state, config)
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "maintenance_active")

    def test_recover_skips_when_policy_not_satisfied(self):
        before = {
            "node_name": "test-node",
            "checked_at": "2026-06-10T09:00:00+09:00",
            "status": "alert",
            "severity": "critical",
            "checks": [{"name": "peer_count", "ok": False, "detail": "0 peers"}],
            "grpc_metrics": {},
            "progress": {},
            "disk": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
            config["state_path"] = str(Path(tmp) / "state.json")
            config["recovery_history_path"] = str(Path(tmp) / "recovery.jsonl")
            config["recovery"]["restart_command"] = ["restart-kaspad"]

            with (
                mock.patch.object(watchtower, "build_report", return_value=before),
                mock.patch.object(watchtower, "run_command_result") as mocked_restart,
                mock.patch("builtins.print") as mocked_print,
            ):
                status = watchtower.recover(config)

            output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
            records = watchtower.load_jsonl(Path(config["recovery_history_path"]))
            self.assertEqual(status, 0)
            self.assertFalse(mocked_restart.called)
            self.assertIn("Recovery skipped: policy blocked", output)
            self.assertEqual(records[-1]["action"], "skipped")
            self.assertTrue(records[-1]["reason"].startswith("policy:"))

    def test_recover_marks_operator_required_when_post_check_stays_unhealthy(self):
        before = {
            "node_name": "test-node",
            "checked_at": "2026-06-10T09:00:00+09:00",
            "status": "alert",
            "severity": "critical",
            "checks": [{"name": "peer_count", "ok": False, "detail": "0 peers"}],
            "grpc_metrics": {},
            "progress": {},
            "disk": {},
        }
        after = {
            "node_name": "test-node",
            "checked_at": "2026-06-10T09:01:00+09:00",
            "status": "alert",
            "severity": "critical",
            "checks": [{"name": "rpc_tcp", "ok": False, "detail": "connect failed"}],
            "grpc_metrics": {},
            "progress": {},
            "disk": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            config = copy.deepcopy(watchtower.DEFAULT_CONFIG)
            config["state_path"] = str(Path(tmp) / "state.json")
            config["recovery_history_path"] = str(Path(tmp) / "recovery.jsonl")
            config["recovery"]["restart_command"] = ["restart-kaspad"]
            config["recovery"]["post_recovery_wait_seconds"] = 0

            completed = subprocess.CompletedProcess(
                ["restart-kaspad"],
                0,
                stdout="restarted\n",
                stderr="",
            )
            with (
                mock.patch.object(watchtower, "build_report", side_effect=[before, after]),
                mock.patch.object(watchtower, "run_command_result", return_value=completed),
                mock.patch("builtins.print") as mocked_print,
            ):
                status = watchtower.recover(config, force=True)

            output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
            records = watchtower.load_jsonl(Path(config["recovery_history_path"]))
            self.assertEqual(status, 1)
            self.assertIn("Operator required: post-recovery check still unhealthy", output)
            self.assertEqual(records[-1]["operator_required"], True)
            self.assertEqual(records[-1]["operator_reason"], "post_recovery_unhealthy")
            self.assertEqual(records[-1]["failed_checks_after"], ["rpc_tcp"])

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

    def test_market_snapshot_formats_spot_and_futures_context(self):
        snapshot = {
            "ok": True,
            "source": "Bybit KAS/USDT",
            "spot": {
                "last_price": "0.031234",
                "change_24h": "0.0123",
                "high_24h": "0.033",
                "low_24h": "0.030",
                "volume_24h": "98765432",
                "price_dispersion": {
                    "median": 0.0312,
                    "min": 0.0310,
                    "max": 0.0315,
                    "dispersion_pct": 1.602564,
                    "sources": 7,
                    "errors": 0,
                },
            },
            "futures": {
                "mark_price": "0.0313",
                "index_price": "0.0312",
                "basis_pct": 0.3205128205,
                "funding_rate": "0.00005",
                "funding_apr_pct": 5.475,
                "funding_z_score": 1.75,
                "next_funding_time": "1780828800000",
                "open_interest": "230000000",
                "open_interest_value": "7200000",
                "volume_24h": "51000000",
                "oi_volume_ratio": 4.5098039216,
            },
        }

        text = watchtower.format_market_snapshot(snapshot)

        self.assertIn("Kaspa market snapshot: Bybit KAS/USDT", text)
        self.assertIn("spot=price=$0.03123 24h=+1.23%", text)
        self.assertIn("volume=98.77M KAS", text)
        self.assertIn("spot_dispersion=median=$0.03120", text)
        self.assertIn("dispersion=+1.60%", text)
        self.assertIn("sources=7 errors=0", text)
        self.assertIn("basis=+0.32%", text)
        self.assertIn("funding=+0.01%", text)
        self.assertIn("funding_apr=+5.47%", text)
        self.assertIn("funding_z=+1.75sd", text)
        self.assertIn("open_interest=230.00M KAS", text)
        self.assertIn("oi_value=$7.20M", text)
        self.assertIn("oi_volume=4.51x", text)

    def test_fetch_market_snapshot_computes_basis_and_apr(self):
        spot_payload = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "lastPrice": "0.030",
                        "price24hPcnt": "-0.02",
                        "highPrice24h": "0.032",
                        "lowPrice24h": "0.029",
                        "volume24h": "1000000",
                    }
                ]
            },
        }
        futures_payload = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "markPrice": "0.0303",
                        "indexPrice": "0.0300",
                        "fundingRate": "0.0001",
                        "fundingIntervalHour": "8",
                        "nextFundingTime": "1780828800000",
                        "openInterest": "2000000",
                        "openInterestValue": "60600",
                        "volume24h": "3000000",
                    }
                ]
            },
        }
        price_payloads = [
            [{"last": "0.0302"}],
            {"price": "0.0301"},
            {"data": {"price": "0.0304"}},
            {"data": [{"lastPr": "0.0305"}]},
            {"result": {"KASUSD": {"c": ["0.0306"]}}},
            {"tick": {"close": 0.0303}},
        ]
        with mock.patch("watchtower.fetch_json_url", side_effect=[spot_payload, futures_payload, *price_payloads]):
            snapshot = watchtower.fetch_market_snapshot(timeout=1)

        self.assertTrue(snapshot["ok"])
        self.assertAlmostEqual(snapshot["futures"]["basis_pct"], 1.0)
        self.assertAlmostEqual(snapshot["futures"]["funding_apr_pct"], 10.95)
        self.assertAlmostEqual(snapshot["futures"]["oi_volume_ratio"], 2 / 3)
        self.assertEqual(snapshot["spot"]["price_dispersion"]["sources"], 7)
        self.assertAlmostEqual(snapshot["spot"]["price_dispersion"]["median"], 0.0303)
        self.assertAlmostEqual(snapshot["spot"]["price_dispersion"]["dispersion_pct"], (0.0306 - 0.0300) / 0.0303 * 100)

    def test_market_spot_price_dispersion_summarizes_sources(self):
        summary = watchtower.market_spot_price_dispersion(
            [
                {"source": "A", "price": 0.030},
                {"source": "B", "price": 0.032},
                {"source": "C", "price": 0.031},
            ],
            errors=1,
        )

        self.assertEqual(summary["sources"], 3)
        self.assertEqual(summary["errors"], 1)
        self.assertAlmostEqual(summary["median"], 0.031)
        self.assertAlmostEqual(summary["dispersion_pct"], (0.032 - 0.030) / 0.031 * 100)

    def test_market_snapshot_item_adds_positioning_risk_metrics(self):
        snapshot = {
            "ok": True,
            "source": "Bybit KAS/USDT",
            "spot": {},
            "futures": {
                "funding_rate": "0.0003",
                "open_interest": "9000000",
                "volume_24h": "3000000",
            },
        }
        history = [
            {"ok": True, "futures_funding_rate": 0.0},
            {"ok": True, "futures_funding_rate": 0.0002},
        ]

        item = watchtower.market_snapshot_item(snapshot, history=history)

        self.assertAlmostEqual(item["futures_oi_volume_ratio"], 3.0)
        self.assertAlmostEqual(item["futures_funding_z_score"], 2.0)

    def test_fetch_market_snapshot_returns_unavailable_on_api_failure(self):
        with mock.patch("watchtower.fetch_json_url", side_effect=ValueError("rate limited")):
            snapshot = watchtower.fetch_market_snapshot(timeout=1)

        self.assertFalse(snapshot["ok"])
        self.assertIn("rate limited", snapshot["error"])

    def test_market_snapshot_item_round_trips_to_sqlite_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            benchmarks = tmp_path / "benchmarks.jsonl"
            markets = tmp_path / "market-snapshots.jsonl"
            db = tmp_path / "history.sqlite"
            watchtower.save_jsonl(
                benchmarks,
                [
                    {
                        "checked_at": "2026-06-07T10:00:00+09:00",
                        "node_name": "test-node",
                        "status": "ok",
                        "severity": "ok",
                        "peer_count": 8,
                        "virtual_daa_score": 100,
                        "block_count": 200,
                        "disk_free_gb": 300,
                    }
                ],
            )
            watchtower.save_jsonl(
                markets,
                [
                    {
                        "checked_at": "2026-06-07T10:01:00+09:00",
                        "source": "Bybit KAS/USDT",
                        "ok": True,
                        "spot_last_price": 0.0305,
                        "spot_change_24h": 0.012,
                        "spot_volume_24h": 42000000,
                        "spot_price_median": 0.0304,
                        "spot_price_min": 0.0301,
                        "spot_price_max": 0.0308,
                        "spot_price_dispersion_pct": 2.3026,
                        "spot_price_sources": 7,
                        "spot_price_source_errors": 0,
                        "futures_basis_pct": -0.13,
                        "futures_funding_rate": 0.0001,
                        "futures_funding_apr_pct": 10.95,
                        "futures_funding_z_score": 1.5,
                        "futures_open_interest": 230000000,
                        "futures_open_interest_value": 7010000,
                        "futures_volume_24h": 78000000,
                        "futures_oi_volume_ratio": 2.9487,
                    }
                ],
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/export_history_sqlite.py",
                    "--db",
                    str(db),
                    "--benchmarks",
                    str(benchmarks),
                    "--market",
                    str(markets),
                    "--upgrades",
                    str(tmp_path / "missing-upgrades.jsonl"),
                    "--recovery",
                    str(tmp_path / "missing-recovery.jsonl"),
                    "--summary",
                    "--days",
                    "7",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("market_snapshots imported=1 total=1", completed.stdout)
        self.assertIn("market_snapshots=1 successful=1", completed.stdout)
        self.assertIn("market_latest=2026-06-07T10:01:00+09:00 source=Bybit KAS/USDT spot=$0.03050", completed.stdout)
        self.assertIn("market_spot_dispersion=median=$0.03040", completed.stdout)
        self.assertIn("dispersion=+2.30%", completed.stdout)
        self.assertIn("sources=7 errors=0", completed.stdout)
        self.assertIn("market_futures=basis=-0.13%", completed.stdout)
        self.assertIn("funding_z=+1.50sd", completed.stdout)
        self.assertIn("oi_volume=2.95x", completed.stdout)

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
            history_db = tmp_path / "watchtower-history.sqlite"
            with closing(sqlite3.connect(history_db)) as connection:
                connection.execute(
                    """
                    create table benchmark_snapshots (
                      checked_at text primary key,
                      node_name text,
                      status text,
                      severity text,
                      peer_count integer,
                      virtual_daa_score integer,
                      block_count integer,
                      latest_processed_age_seconds real
                    )
                    """
                )
                connection.executemany(
                    """
                    insert into benchmark_snapshots (
                      checked_at, node_name, status, severity, peer_count,
                      virtual_daa_score, block_count, latest_processed_age_seconds
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("2026-06-06T10:10:00+09:00", "mainnet-a", "ok", "ok", 8, 10000, 20000, 5),
                        ("2026-06-06T09:50:00+09:00", "mainnet-b", "ok", "ok", 8, 10000, 20000, 5),
                    ],
                )
                connection.commit()

            watchtower.write_status_page(
                output,
                report,
                state,
                benchmark_path=tmp_path / "missing.jsonl",
                recovery_records=[
                    {
                        "started_at": "2026-06-06T10:01:00+09:00",
                        "action": "executed",
                        "severity_before": "critical",
                        "severity_after": "critical",
                        "failed_checks_before": ["peer_count"],
                        "operator_required": True,
                        "operator_reason": "post_recovery_unhealthy",
                    }
                ],
                sqlite_history=history_db,
            )

            html = output.read_text(encoding="utf-8")
            self.assertIn("Operator verdict", html)
            self.assertIn("Operator Required", html)
            self.assertIn("Yes · post_recovery_unhealthy", html)
            self.assertIn("YES", html)
            self.assertIn('class="header-link" href="stream.html"', html)
            self.assertIn(">Stream</a>", html)
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
            self.assertIn('class="status-tabs"', html)
            self.assertIn('data-tab-target="tab-market"', html)
            self.assertIn('data-tab-target="tab-futures"', html)
            self.assertIn('data-tab-target="tab-network"', html)
            self.assertIn('data-tab-target="tab-ops"', html)
            self.assertIn('data-tab-target="tab-history"', html)
            self.assertIn('id="tab-market" class="tab-panel active"', html)
            self.assertIn('id="tab-futures" class="tab-panel"', html)
            self.assertIn('id="tab-network" class="tab-panel"', html)
            self.assertIn('id="tab-ops" class="tab-panel"', html)
            self.assertIn('id="tab-history" class="tab-panel"', html)
            self.assertIn("Multi-Node History", html)
            self.assertIn("mainnet-b", html)
            self.assertIn("stale_node", html)
            self.assertIn('data-timeframe-target="15m"', html)
            self.assertIn('data-timeframe-panel="15m"', html)
            self.assertIn('data-timeframe-target="all"', html)
            self.assertIn('data-timeframe-panel="all"', html)
            self.assertIn('data-liquidation-target="12h"', html)
            self.assertIn('data-liquidation-panel="12h"', html)
            self.assertIn("Hashrate", html)
            self.assertIn("1.25 PH/s", html)
            self.assertIn("network_hashes_per_second trend", html)
            self.assertIn("KAS/USDT 15m", html)
            self.assertIn("KAS/USDT 4h", html)
            self.assertIn("KAS/USDT 1D", html)
            self.assertIn("KAS/USDT 1W", html)
            self.assertIn("KAS/USDT 1M", html)
            self.assertIn("KAS/USDT All Bollinger", html)
            self.assertIn("Market Indicators", html)
            self.assertIn("RSI, MACD, Bollinger, volume, and BTC-relative by timeframe", html)
            self.assertIn("RSI --", html)
            self.assertIn("<span>MACD</span><strong>pending</strong>", html)
            self.assertIn("<span>BB</span><strong>pending</strong>", html)
            self.assertIn("<span>Vol</span><strong>pending</strong>", html)
            self.assertIn("<span>BTC</span><strong>pending</strong>", html)
            self.assertIn("KAS/USDT vs BTC/USDT 15m", html)
            self.assertIn("KAS/USDT vs BTC/USDT 1D", html)
            self.assertIn("KAS/USDT vs BTC/USDT 4h", html)
            self.assertIn("KAS/USDT vs BTC/USDT 1W", html)
            self.assertIn("KAS/USDT vs BTC/USDT 1M", html)
            self.assertLess(html.index("KAS/USDT vs BTC/USDT 15m"), html.index("KAS/USDT vs BTC/USDT 4h"))
            self.assertLess(html.index("KAS/USDT vs BTC/USDT 4h"), html.index("KAS/USDT vs BTC/USDT 1D"))
            self.assertLess(html.index("KAS/USDT vs BTC/USDT 1D"), html.index("KAS/USDT vs BTC/USDT 1W"))
            self.assertLess(html.index("KAS/USDT vs BTC/USDT 1W"), html.index("KAS/USDT vs BTC/USDT 1M"))
            self.assertIn("KAS Exchange Volume 1D", html)
            self.assertIn("KAS/USDT Futures Positioning", html)
            self.assertIn("KAS/USDT Futures Trend 7D", html)
            self.assertIn("Market Data Sources", html)
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
            self.assertIn('id="market-chart-all"', html)
            self.assertIn('id="market-cross-chart-15m"', html)
            self.assertIn('id="market-cross-chart"', html)
            self.assertIn('id="market-cross-chart-4h"', html)
            self.assertIn('id="market-cross-chart-1w"', html)
            self.assertIn('id="market-cross-chart-1m"', html)
            self.assertIn('id="market-volume-chart"', html)
            self.assertIn('id="market-volume-legend"', html)
            self.assertIn('id="futures-mark"', html)
            self.assertIn('id="futures-index"', html)
            self.assertIn('id="futures-basis"', html)
            self.assertIn('id="futures-funding"', html)
            self.assertIn('id="futures-funding-apr"', html)
            self.assertIn('id="futures-open-interest"', html)
            self.assertIn('id="futures-open-interest-value"', html)
            self.assertIn('id="futures-volume"', html)
            self.assertIn('id="futures-trend-chart"', html)
            self.assertIn('id="futures-trend-status"', html)
            self.assertIn('id="market-source-list"', html)
            self.assertIn('id="liquidation-chart-12h"', html)
            self.assertIn('id="liquidation-chart-24h"', html)
            self.assertIn('id="liquidation-chart-1w"', html)
            self.assertIn('id="liquidation-chart-1m"', html)
            self.assertIn('id="market-trend-15m"', html)
            self.assertIn('id="market-trend-4h"', html)
            self.assertIn('id="market-trend-1d"', html)
            self.assertIn('id="market-trend-1w"', html)
            self.assertIn('id="market-trend-1m"', html)
            self.assertIn('id="market-trend-all"', html)
            self.assertIn('id="market-rsi-15m"', html)
            self.assertIn('id="market-rsi-4h"', html)
            self.assertIn('id="market-rsi-1d"', html)
            self.assertIn('id="market-rsi-1w"', html)
            self.assertIn('id="market-rsi-1m"', html)
            self.assertIn('id="market-rsi-all"', html)
            self.assertIn('id="market-rsi-card-15m"', html)
            self.assertIn('id="market-rsi-card-4h"', html)
            self.assertIn('id="market-rsi-card-1d"', html)
            self.assertIn('id="market-rsi-card-1w"', html)
            self.assertIn('id="market-rsi-card-1m"', html)
            self.assertIn('id="market-signal-list"', html)
            self.assertIn("Trend pending", html)
            self.assertIn("RSI pending", html)
            self.assertIn("Waiting for candles", html)
            self.assertIn("min-height: 322px", html)
            self.assertIn("main > .panel + .panel", html)
            self.assertIn(".tab-panel:not(.active)", html)
            self.assertIn(".timeframe-panel:not(.active)", html)
            self.assertIn(".liquidation-panel:not(.active)", html)
            self.assertNotIn("\n    .panel + .panel { margin-top: 14px; }", html)
            self.assertIn("activateDashboardGroup", html)
            self.assertIn("statusActiveTabKey", html)
            self.assertIn('data-tab-target="tab-wallet"', html)
            self.assertIn('id="tab-wallet"', html)
            self.assertIn("Wallet Events", html)
            self.assertIn("Mining Rewards", html)
            self.assertIn("Mining Today", html)
            self.assertIn('data-tab-target="tab-mining"', html)
            self.assertIn('id="tab-mining"', html)
            self.assertIn("Mining Status", html)
            self.assertIn("macOS GPU Plan", html)
            self.assertIn('data-tab-target="tab-indexer"', html)
            self.assertIn('id="tab-indexer"', html)
            self.assertIn("Indexer Watchlist", html)
            self.assertIn("Watched Address Events", html)
            self.assertIn("make discord-watch-add", html)
            self.assertIn('data-tab-target="tab-whales"', html)
            self.assertIn('id="tab-whales"', html)
            self.assertIn("Whale Watch", html)
            self.assertIn("Whale Events", html)
            self.assertIn("restoreDashboardSelection", html)
            self.assertIn("window.history.replaceState", html)
            self.assertIn("kaspa-watchtower-active-tab", html)
            self.assertIn("kaspa-watchtower-active-timeframe", html)
            self.assertIn("kaspa-watchtower-active-liquidation", html)
            self.assertIn("drawMarketCrossChart", html)
            self.assertIn("marketConfig.cross.map(refreshMarketCrossChart)", html)
            self.assertIn("independentRange: true", html)
            self.assertIn("const xTime = (time)", html)
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
            self.assertIn("marketBollingerPoints", html)
            self.assertIn("marketPathFromPoints", html)
            self.assertIn("marketTrendState", html)
            self.assertIn("marketTrendBadge", html)
            self.assertIn("marketRsiValue", html)
            self.assertIn("marketRsiState", html)
            self.assertIn("marketRsiBadge", html)
            self.assertIn("marketRsiCard", html)
            self.assertIn("marketUpdateIndicatorRows", html)
            self.assertIn("marketIndicatorRow", html)
            self.assertIn("marketMacdState", html)
            self.assertIn("marketBollingerPositionState", html)
            self.assertIn("marketVolumeSpikeState", html)
            self.assertIn("marketRelativeStrengthState", html)
            self.assertIn("market-indicator-card-grid", html)
            self.assertIn("market-indicator-row", html)
            self.assertIn("marketRsiState(candles, 14)", html)
            self.assertIn("marketSignalState", html)
            self.assertIn("marketSignalWatch", html)
            self.assertIn("marketShouldRefresh", html)
            self.assertIn("marketRefreshTimes", html)
            self.assertIn("marketSourceStatus", html)
            self.assertIn("marketSourceStates", html)
            self.assertIn("marketSourceDetail", html)
            self.assertIn("marketErrorDetail", html)
            self.assertIn("marketSourceOrder", html)
            self.assertIn("KAS/BTC cross 15m", html)
            self.assertIn("KAS/BTC cross 4h", html)
            self.assertIn("KAS/BTC cross 1D", html)
            self.assertIn("KAS/BTC cross 1W", html)
            self.assertIn("KAS/BTC cross 1M", html)
            self.assertIn("marketRenderSourceStates", html)
            self.assertIn("waiting for refresh", html)
            self.assertIn("refreshMs: 60 * 1000", html)
            self.assertIn("refreshMs: 5 * 60 * 1000", html)
            self.assertIn("refreshMs: 10 * 60 * 1000", html)
            self.assertIn("refreshMs: 60 * 60 * 1000", html)
            self.assertIn("EMA cross up", html)
            self.assertIn("EMA cross down", html)
            self.assertIn("Overbought", html)
            self.assertIn("Oversold", html)
            self.assertIn("Close \" + distanceText + \" vs EMA", html)
            self.assertIn("Uptrend", html)
            self.assertIn("Downtrend", html)
            self.assertIn("Neutral", html)
            self.assertIn("market-ema-line", html)
            self.assertIn("market-bollinger-line", html)
            self.assertIn("market-bollinger-fill", html)
            self.assertIn('textContent = "BB" + String(bollingerConfig.period)', html)
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
            self.assertIn("bollinger: { period: 20, deviations: 2 }", html)
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
            self.assertIn("symbol=BTCUSDT&interval=15&limit=96", html)
            self.assertIn("symbol=BTCUSDT&interval=240&limit=48", html)
            self.assertIn("symbol=BTCUSDT&interval=W&limit=60", html)
            self.assertIn("symbol=BTCUSDT&interval=M&limit=1000", html)
            self.assertIn("market-axis-label", html)
            self.assertIn('month: "2-digit"', html)
            self.assertIn('hour: "2-digit"', html)
            self.assertIn("Block Processing", html)
            self.assertIn("9.2/s", html)
            self.assertIn("BPS Highway", html)
            self.assertIn("20-Lane BPS Highway", html)
            self.assertIn("9.2 BPS", html)
            self.assertIn('class="bps-highway ok"', html)
            self.assertIn('class="bps-highway-canvas"', html)
            self.assertIn("three@0.184.0", html)
            self.assertIn("THREE.PerspectiveCamera", html)
            self.assertIn("THREE.BoxGeometry", html)
            self.assertIn("renderKaspaCanvasFallback", html)
            self.assertIn("canvas-fallback", html)
            self.assertIn("three-ready", html)
            self.assertIn("three-fallback", html)
            self.assertEqual(html.count('class="bps-lane active"'), 20)
            self.assertIn("rusty-kaspa processed-stats log", html)
            self.assertIn("3.2s old", html)
            self.assertIn("131.1 tx/s", html)
            self.assertIn("1311 tx / 10.0s window", html)
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

    def test_stream_page_uses_1080p_rotating_scene_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            report = {
                "node_name": "stream-node",
                "checked_at": "2026-06-07T17:00:00+09:00",
                "status": "ok",
                "severity": "ok",
                "checks": [
                    {"name": "sync_status", "ok": True, "detail": "synced"},
                    {"name": "peer_count", "ok": True, "detail": "8 peers"},
                    {"name": "block_progress", "ok": True, "detail": "relay ok"},
                    {"name": "processed_stats_freshness", "ok": True, "detail": "fresh"},
                    {"name": "disk_free", "ok": True, "detail": "ok"},
                ],
                "grpc_metrics": {
                    "network_id": "mainnet",
                    "is_synced": True,
                    "peer_count": 8,
                    "active_peers": 8,
                    "virtual_daa_score": 123456,
                    "tip_count": 3,
                    "mempool_size": 42,
                    "network_hashes_per_second": 1_250_000_000_000_000,
                    "network_hashrate_window_size": 1000,
                    "pruning_point_hash": "abcdef1234567890",
                },
                "progress": {
                    "relay_blocks_in_window": 11,
                    "relay_events_in_window": 10,
                    "window_minutes": 10,
                    "latest_processed_age_seconds": 2.0,
                    "relay_samples": [
                        {"timestamp": "2026-06-07T16:59:50+09:00", "blocks": 9},
                        {"timestamp": "2026-06-07T17:00:00+09:00", "blocks": 11},
                    ],
                    "latest_processed": {
                        "timestamp": "2026-06-07T17:00:00+09:00",
                        "blocks": 92,
                        "seconds": 10.0,
                        "transactions": 2161,
                        "blocks_per_second": 9.2,
                        "transactions_per_second": 216.1,
                    },
                    "processed_samples": [
                        {
                            "timestamp": "2026-06-07T16:59:50+09:00",
                            "blocks_per_second": 8.8,
                            "transactions_per_second": 200.1,
                        },
                        {
                            "timestamp": "2026-06-07T17:00:00+09:00",
                            "blocks_per_second": 9.2,
                            "transactions_per_second": 216.1,
                        },
                    ],
                },
                "sync_progress": {"daa_delta": 120},
                "disk": {"exists": True, "free_gb": 50, "free_percent": 25},
                "recovery": {"action": "none", "mode": "manual"},
            }
            state = {
                "history": [
                    {
                        "checked_at": "2026-06-07T16:59:40+09:00",
                        "severity": "ok",
                        "mempool_size": 22,
                        "peer_count": 7,
                        "virtual_daa_score": 123440,
                    },
                    {
                        "checked_at": "2026-06-07T16:59:50+09:00",
                        "severity": "ok",
                        "mempool_size": 35,
                        "peer_count": 8,
                        "virtual_daa_score": 123450,
                    },
                ]
            }
            market_path = tmp_path / "market.jsonl"
            watchtower.save_jsonl(
                market_path,
                [
                    {
                        "checked_at": "2026-06-07T16:58:00+09:00",
                        "source": "Bybit KAS/USDT",
                        "ok": True,
                        "spot_last_price": 0.0305,
                        "spot_change_24h": 0.012,
                        "spot_volume_24h": 12345678,
                        "futures_mark_price": 0.0304,
                        "futures_index_price": 0.0305,
                        "futures_basis_pct": -0.13,
                        "futures_funding_rate": 0.0001,
                        "futures_funding_apr_pct": 10.95,
                        "futures_next_funding_time": 1780800000000,
                        "futures_open_interest": 1234567,
                        "futures_open_interest_value": 37654,
                        "futures_volume_24h": 23456789,
                    }
                ],
            )
            output = tmp_path / "stream.html"

            watchtower.write_stream_page(
                output,
                report,
                state,
                benchmark_path=tmp_path / "missing-benchmarks.jsonl",
                market_snapshot_path=market_path,
            )

            html = output.read_text(encoding="utf-8")
            self.assertIn("Kaspa Watchtower Stream", html)
            self.assertIn('class="stream-stage"', html)
            self.assertIn('data-stream-width="1920"', html)
            self.assertIn('data-stream-height="1080"', html)
            self.assertIn('data-default-interval-ms="5000"', html)
            self.assertIn("Overall Status", html)
            self.assertIn("Network Health", html)
            self.assertIn("Transaction Throughput", html)
            self.assertIn("Mempool Activity", html)
            self.assertIn("KAS/USDT Market", html)
            self.assertIn("Futures Positioning", html)
            self.assertIn("216.1/s", html)
            self.assertIn("BPS Highway", html)
            self.assertIn("20-Lane BPS Highway", html)
            self.assertIn("9.2 BPS", html)
            self.assertIn('class="bps-highway-canvas"', html)
            self.assertIn("three@0.184.0", html)
            self.assertIn("rusty-kaspa processed-stats log", html)
            self.assertIn("216.1 tx/s", html)
            self.assertIn("Disk Free", html)
            self.assertIn("24h High", html)
            self.assertIn("24h Low", html)
            self.assertIn("Market Snapshots", html)
            self.assertIn("Benchmark OK", html)
            self.assertIn("Failed Checks", html)
            self.assertIn("Benchmark Window", html)
            self.assertIn("Mempool Size By 10s Bucket", html)
            self.assertIn("10-second bars", html)
            self.assertIn("$0.03050", html)
            self.assertIn("Open Interest", html)
            self.assertIn("font-size: 56px", html)
            self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr))", html)
            self.assertIn("streamIntervalMs", html)
            self.assertIn("setInterval", html)
            self.assertIn('params.get("interval")', html)
            self.assertIn('params.get("scene")', html)
            self.assertIn("ArrowRight", html)
            self.assertIn("scaleStage", html)


if __name__ == "__main__":
    unittest.main()

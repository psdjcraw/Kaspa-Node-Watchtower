import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_prometheus_alerts.sh"


def response(alerts):
    return {"status": "success", "data": {"alerts": alerts}}


def watchtower_alert(name, state="firing", node="mainnet", severity="critical", description="detail"):
    return {
        "state": state,
        "labels": {
            "service": "kaspa-watchtower",
            "alertname": name,
            "node": node,
            "severity": severity,
        },
        "annotations": {"description": description},
    }


class PrometheusAlertBridgeTest(unittest.TestCase):
    def run_bridge(self, tmp_dir: Path, payload: dict, state_name: str = "state.json") -> subprocess.CompletedProcess:
        payload_path = tmp_dir / "alerts.json"
        state_path = tmp_dir / state_name
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        env = {
            "KASPA_WATCHTOWER_PROMETHEUS_ALERTS_FILE": str(payload_path),
            "KASPA_WATCHTOWER_PROMETHEUS_ALERT_STATE": str(state_path),
        }
        return subprocess.run(
            [str(SCRIPT)],
            cwd=ROOT,
            env={**env},
            text=True,
            capture_output=True,
            check=False,
        )

    def test_active_alert_emits_once_then_suppresses_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            payload = response([watchtower_alert("KaspaWatchtowerCritical")])

            first = self.run_bridge(tmp_dir, payload)
            second = self.run_bridge(tmp_dir, payload)

        self.assertEqual(first.returncode, 0)
        self.assertIn("Kaspa Prometheus alerts changed", first.stdout)
        self.assertIn("active_alerts=1 new_alerts=1 resolved_alerts=0", first.stdout)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "")

    def test_duplicate_prometheus_items_are_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            alert = watchtower_alert("KaspaWatchtowerCritical")

            result = self.run_bridge(tmp_dir, response([alert, alert]))
            state = json.loads((tmp_dir / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0)
        self.assertIn("active_alerts=1 new_alerts=1 resolved_alerts=0", result.stdout)
        self.assertEqual(len(state["fingerprints"]), 1)

    def test_partial_recovery_reports_resolved_and_new_active_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            first_payload = response(
                [
                    watchtower_alert("KaspaWatchtowerCritical", description="old critical"),
                    watchtower_alert("KaspaWatchtowerRiskNodes", severity="warning", description="old warning"),
                ]
            )
            second_payload = response(
                [
                    watchtower_alert("KaspaWatchtowerRiskNodes", severity="warning", description="old warning"),
                    watchtower_alert("KaspaWatchtowerNoPeers", severity="warning", description="new warning"),
                ]
            )

            self.run_bridge(tmp_dir, first_payload)
            result = self.run_bridge(tmp_dir, second_payload)

        self.assertEqual(result.returncode, 0)
        self.assertIn("active_alerts=2 new_alerts=1 resolved_alerts=1", result.stdout)
        self.assertIn("resolved:", result.stdout)
        self.assertIn("KaspaWatchtowerCritical", result.stdout)
        self.assertIn("KaspaWatchtowerNoPeers", result.stdout)
        self.assertNotIn("old warning", result.stdout)

    def test_full_recovery_emits_once_even_after_corrupt_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            active_payload = response([watchtower_alert("KaspaWatchtowerCritical")])
            healthy_payload = response([])

            self.run_bridge(tmp_dir, active_payload)
            recovered = self.run_bridge(tmp_dir, healthy_payload)
            suppressed = self.run_bridge(tmp_dir, healthy_payload)

            corrupt_state = tmp_dir / "corrupt.json"
            corrupt_state.write_text("{not json", encoding="utf-8")
            corrupt = self.run_bridge(tmp_dir, active_payload, state_name="corrupt.json")

        self.assertEqual(recovered.returncode, 0)
        self.assertIn("Kaspa Prometheus alerts recovered", recovered.stdout)
        self.assertEqual(suppressed.returncode, 0)
        self.assertEqual(suppressed.stdout, "")
        self.assertEqual(corrupt.returncode, 0)
        self.assertIn("new_alerts=1", corrupt.stdout)


if __name__ == "__main__":
    unittest.main()

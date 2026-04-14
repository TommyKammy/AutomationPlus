import json
import tempfile
import unittest
from pathlib import Path

import automationplus.restart_decision as restart_decision


class RestartDecisionTests(unittest.TestCase):
    def _loop_status_payload(
        self,
        *,
        degraded_state: str,
        restart_eligible: bool,
        operator_hold: bool,
        captured_at: str = "2026-04-15T09:10:01Z",
        signature_count: int = 1,
    ) -> dict:
        signature = None
        if degraded_state != "healthy":
            signature = {
                "id": "sig-transient-demo",
                "reason": "pane_dead",
                "class": "transient" if degraded_state != "unsafe-unknown" else "unknown",
                "count": signature_count,
                "firstSeenAt": captured_at,
                "lastSeenAt": captured_at,
                "normalizedSummary": "loop error: econnreset while refreshing queue state",
            }

        return {
            "schemaVersion": 1,
            "capturedAt": captured_at,
            "status": "degraded" if degraded_state != "healthy" else "healthy",
            "failurePolicy": {
                "schemaVersion": 1,
                "degradedState": degraded_state,
                "restartEligible": restart_eligible,
                "operatorHold": operator_hold,
                "summary": "Loop failure classification for testing.",
                "signature": signature,
            },
            "runtime": {
                "state": "running",
                "health": "degraded" if degraded_state != "healthy" else "healthy",
                "hostMode": "tmux",
                "sessionName": "automationplus-loop",
                "windowName": "loop",
                "paneId": "%1",
                "pid": 4242,
                "currentCommand": "node",
                "currentPath": "/tmp/supervisor",
                "paneDead": degraded_state != "healthy",
                "tail": ["2026-04-15T09:10:00Z loop error: ECONNRESET while refreshing queue state"],
            },
        }

    def test_write_restart_decision_allows_first_transient_failure_with_remaining_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            decision_path = root / ".codex-supervisor" / "health" / "restart-decision.json"
            budget_path = root / ".codex-supervisor" / "health" / "restart-budget.json"

            artifact = restart_decision.write_restart_decision_artifact(
                output_path=decision_path,
                budget_path=budget_path,
                loop_status_payload=self._loop_status_payload(
                    degraded_state="transient-failure",
                    restart_eligible=True,
                    operator_hold=False,
                ),
                evaluated_at="2026-04-15T09:10:05Z",
                max_restarts=2,
                window_seconds=900,
            )

            self.assertTrue(artifact["decision"]["allowed"])
            self.assertEqual(artifact["decision"]["reasonCode"], "transient_restart_allowed")
            self.assertEqual(artifact["budget"]["used"], 1)
            self.assertEqual(artifact["budget"]["remaining"], 1)
            on_disk = json.loads(decision_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk, artifact)

    def test_write_restart_decision_denies_when_restart_budget_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            decision_path = root / ".codex-supervisor" / "health" / "restart-decision.json"
            budget_path = root / ".codex-supervisor" / "health" / "restart-budget.json"
            budget_path.parent.mkdir(parents=True, exist_ok=True)
            budget_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "history": [
                            {"evaluatedAt": "2026-04-15T09:00:00Z", "allowed": True},
                            {"evaluatedAt": "2026-04-15T09:05:00Z", "allowed": True},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            artifact = restart_decision.write_restart_decision_artifact(
                output_path=decision_path,
                budget_path=budget_path,
                loop_status_payload=self._loop_status_payload(
                    degraded_state="transient-failure",
                    restart_eligible=True,
                    operator_hold=False,
                ),
                evaluated_at="2026-04-15T09:10:05Z",
                max_restarts=2,
                window_seconds=900,
            )

            self.assertFalse(artifact["decision"]["allowed"])
            self.assertEqual(artifact["decision"]["reasonCode"], "restart_budget_exhausted")
            self.assertEqual(artifact["budget"]["used"], 2)
            self.assertEqual(artifact["budget"]["remaining"], 0)

    def test_write_restart_decision_denies_unsafe_failure_classification(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            decision_path = root / ".codex-supervisor" / "health" / "restart-decision.json"
            budget_path = root / ".codex-supervisor" / "health" / "restart-budget.json"

            artifact = restart_decision.write_restart_decision_artifact(
                output_path=decision_path,
                budget_path=budget_path,
                loop_status_payload=self._loop_status_payload(
                    degraded_state="unsafe-unknown",
                    restart_eligible=False,
                    operator_hold=True,
                ),
                evaluated_at="2026-04-15T09:10:05Z",
                max_restarts=2,
                window_seconds=900,
            )

            self.assertFalse(artifact["decision"]["allowed"])
            self.assertEqual(artifact["decision"]["reasonCode"], "unsafe_failure_policy")
            self.assertEqual(artifact["decision"]["action"], "hold")
            self.assertEqual(artifact["blocking"]["route"], "hold")
            self.assertTrue(artifact["blocking"]["requiresOperatorAction"])
            self.assertTrue(Path(artifact["blockArtifactPath"]).is_file())
            self.assertEqual(artifact["budget"]["used"], 0)
            self.assertEqual(artifact["budget"]["remaining"], 2)

    def test_write_restart_decision_quarantines_repeated_failure_with_blocking_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            decision_path = root / ".codex-supervisor" / "health" / "restart-decision.json"
            budget_path = root / ".codex-supervisor" / "health" / "restart-budget.json"

            artifact = restart_decision.write_restart_decision_artifact(
                output_path=decision_path,
                budget_path=budget_path,
                loop_status_payload=self._loop_status_payload(
                    degraded_state="repeated-failure",
                    restart_eligible=False,
                    operator_hold=True,
                    signature_count=2,
                ),
                evaluated_at="2026-04-15T09:10:05Z",
                max_restarts=2,
                window_seconds=900,
            )

            self.assertFalse(artifact["decision"]["allowed"])
            self.assertEqual(artifact["decision"]["reasonCode"], "repeated_failure_auto_stop")
            self.assertEqual(artifact["decision"]["action"], "stop")
            self.assertEqual(artifact["blocking"]["route"], "quarantine")
            self.assertEqual(artifact["blocking"]["signatureCount"], 2)
            self.assertIn("repeated failure", artifact["blocking"]["summary"].lower())
            block_artifact = json.loads(
                Path(artifact["blockArtifactPath"]).read_text(encoding="utf-8")
            )
            self.assertEqual(block_artifact["artifactType"], "restart_control_block")
            self.assertEqual(block_artifact["route"], "quarantine")


if __name__ == "__main__":
    unittest.main()

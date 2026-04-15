import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automationplus.health_mirror as health_mirror
import automationplus.loop_status as loop_status
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

    def test_write_restart_decision_uses_persisted_failure_history_from_loop_status(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir) / "supervisor"
            workspace_root = Path(tempdir) / "workspace"
            decision_path = workspace_root / ".codex-supervisor" / "health" / "restart-decision.json"
            budget_path = workspace_root / ".codex-supervisor" / "health" / "restart-budget.json"
            health_snapshot_path = (
                workspace_root / ".codex-supervisor" / "health" / "loop-health.json"
            )
            supervisor_root.mkdir(parents=True, exist_ok=True)
            health_snapshot_path.parent.mkdir(parents=True, exist_ok=True)

            runtime = {
                "state": "running",
                "hostMode": "tmux",
                "sessionName": "automationplus-loop",
                "windowName": "loop",
                "paneId": "%1",
                "panePid": 4242,
                "paneCurrentCommand": "node",
                "paneCurrentPath": str(supervisor_root),
                "paneDead": True,
                "tail": [
                    "2026-04-15T09:10:00Z loop error: ECONNRESET while refreshing queue state",
                ],
            }
            normalized = health_mirror._normalize_failure_text(runtime["tail"][0])
            signature_id = health_mirror._signature_id("pane_dead", normalized)
            health_snapshot_path.write_text(
                (
                    "{\n"
                    '  "failureRegistry": {\n'
                    '    "schemaVersion": 1,\n'
                    '    "entries": {\n'
                    f'      "{signature_id}": {{\n'
                    f'        "id": "{signature_id}",\n'
                    '        "reason": "pane_dead",\n'
                    '        "signatureClass": "transient",\n'
                    '        "summary": "2026-04-15T09:08:00Z loop error: ECONNRESET while refreshing queue state",\n'
                    f'        "normalizedSummary": "{normalized}",\n'
                    '        "firstSeenAt": "2026-04-15T09:08:01Z",\n'
                    '        "lastSeenAt": "2026-04-15T09:08:01Z",\n'
                    '        "seenCount": 1\n'
                    "      }\n"
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "automationplus.loop_status.health_mirror._read_tmux_runtime",
                return_value=runtime,
            ):
                loop_status_payload = loop_status.collect_loop_status(
                    supervisor_root=supervisor_root,
                    workspace_root=workspace_root,
                )

            artifact = restart_decision.write_restart_decision_artifact(
                output_path=decision_path,
                budget_path=budget_path,
                loop_status_payload=loop_status_payload,
                evaluated_at="2026-04-15T09:10:05Z",
                max_restarts=2,
                window_seconds=900,
            )

        self.assertEqual(loop_status_payload["failurePolicy"]["degradedState"], "repeated-failure")
        self.assertEqual(loop_status_payload["failurePolicy"]["signature"]["count"], 2)
        self.assertFalse(artifact["decision"]["allowed"])
        self.assertEqual(artifact["decision"]["reasonCode"], "repeated_failure_auto_stop")
        self.assertEqual(artifact["blocking"]["signatureId"], signature_id)

    def test_write_restart_decision_restart_not_eligible_includes_blocking_without_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            decision_path = root / ".codex-supervisor" / "health" / "restart-decision.json"
            budget_path = root / ".codex-supervisor" / "health" / "restart-budget.json"
            block_artifact_path = restart_decision._restart_control_block_path(decision_path)
            block_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            block_artifact_path.write_text('{"stale": true}\n', encoding="utf-8")

            artifact = restart_decision.write_restart_decision_artifact(
                output_path=decision_path,
                budget_path=budget_path,
                loop_status_payload=self._loop_status_payload(
                    degraded_state="steady-state",
                    restart_eligible=False,
                    operator_hold=False,
                ),
                evaluated_at="2026-04-15T09:10:05Z",
                max_restarts=2,
                window_seconds=900,
            )

            self.assertFalse(artifact["decision"]["allowed"])
            self.assertEqual(artifact["decision"]["reasonCode"], "restart_not_eligible")
            self.assertEqual(artifact["decision"]["action"], "stop")
            self.assertEqual(artifact["blocking"]["route"], None)
            self.assertEqual(artifact["blocking"]["reasonCode"], "restart_not_eligible")
            self.assertTrue(artifact["blocking"]["requiresOperatorAction"])
            self.assertIn("not eligible", artifact["blocking"]["summary"].lower())
            self.assertNotIn("blockArtifactPath", artifact)
            self.assertFalse(block_artifact_path.exists())

    def test_write_restart_decision_persists_control_block_before_blocked_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            decision_path = root / ".codex-supervisor" / "health" / "restart-decision.json"
            budget_path = root / ".codex-supervisor" / "health" / "restart-budget.json"
            writes: list[tuple[Path, dict]] = []

            def capture_write(path: Path, payload: dict) -> None:
                writes.append((Path(path).resolve(), dict(payload)))

            with mock.patch.object(restart_decision, "_write_json_atomic", side_effect=capture_write):
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

            block_artifact_path = restart_decision._restart_control_block_path(decision_path)
            self.assertEqual(
                [path for path, _payload in writes],
                [budget_path.resolve(), block_artifact_path, decision_path.resolve()],
            )
            self.assertEqual(
                writes[-1][1]["blockArtifactPath"],
                str(block_artifact_path),
            )
            self.assertEqual(artifact["blockArtifactPath"], str(block_artifact_path))

    def test_write_restart_decision_removes_stale_control_block_after_decision_write(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            decision_path = root / ".codex-supervisor" / "health" / "restart-decision.json"
            budget_path = root / ".codex-supervisor" / "health" / "restart-budget.json"
            block_artifact_path = restart_decision._restart_control_block_path(decision_path)
            calls: list[tuple[str, Path]] = []

            def capture_write(path: Path, payload: dict) -> None:
                del payload
                calls.append(("write", Path(path).resolve()))

            def capture_remove(path: Path) -> None:
                calls.append(("remove", Path(path).resolve()))

            with mock.patch.object(restart_decision, "_write_json_atomic", side_effect=capture_write):
                with mock.patch.object(
                    restart_decision,
                    "_remove_file_if_present",
                    side_effect=capture_remove,
                ):
                    restart_decision.write_restart_decision_artifact(
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

            self.assertEqual(
                calls,
                [
                    ("write", budget_path.resolve()),
                    ("write", decision_path.resolve()),
                    ("remove", block_artifact_path),
                ],
            )


if __name__ == "__main__":
    unittest.main()

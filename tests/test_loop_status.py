import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automationplus.health_mirror as health_mirror
import automationplus.loop_status as loop_status


class LoopStatusTests(unittest.TestCase):
    def test_collect_loop_status_reports_healthy_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir) / "supervisor"
            workspace_root = Path(tempdir) / "workspace"
            supervisor_root.mkdir(parents=True, exist_ok=True)
            workspace_root.mkdir(parents=True, exist_ok=True)

            with mock.patch(
                "automationplus.loop_status.health_mirror.collect_loop_health_snapshot",
                return_value={
                    "schemaVersion": 1,
                    "capturedAt": "2026-04-15T00:00:00Z",
                    "loopRuntime": {
                        "state": "running",
                        "hostMode": "tmux",
                        "sessionName": "automationplus-loop",
                        "windowName": "loop",
                        "paneId": "%1",
                        "panePid": 4242,
                        "paneCurrentCommand": "node",
                        "paneCurrentPath": str(supervisor_root),
                        "paneDead": False,
                        "tail": ["2026-04-15T00:00:00Z issue=#26 state=reproducing"],
                    },
                    "supervisor": {
                        "root": str(supervisor_root),
                        "activeIssueNumber": 26,
                        "activeIssue": {"issue_number": 26, "state": "reproducing"},
                        "turnInProgress": {"issueNumber": 26, "state": "reproducing"},
                        "decisionCycle": {
                            "issue": {"number": 26},
                            "decision": {"nextState": "reproducing"},
                        },
                    },
                    "drift": {
                        "issueNumberMatches": True,
                        "workspaceMatches": True,
                        "stateMatches": True,
                    },
                    "failurePolicy": {
                        "schemaVersion": 1,
                        "degradedState": "healthy",
                        "restartEligible": False,
                        "operatorHold": False,
                        "summary": "No loop failure classification is active.",
                        "signature": None,
                    },
                },
            ), mock.patch(
                "automationplus.loop_status.health_mirror.apply_persisted_failure_tracking",
                side_effect=lambda snapshot, _artifact_path: snapshot,
            ):
                payload = loop_status.collect_loop_status(
                    supervisor_root=supervisor_root,
                    workspace_root=workspace_root,
                )

        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["failurePolicy"]["degradedState"], "healthy")
        self.assertEqual(payload["runtime"]["state"], "running")
        self.assertEqual(payload["runtime"]["health"], "healthy")
        self.assertEqual(payload["launcher"]["service"]["state"], "running")
        self.assertEqual(payload["launcher"]["discovery"]["sessionName"], "automationplus-loop")
        self.assertTrue(payload["launcher"]["contract"]["readOnly"])

    def test_collect_loop_status_reports_off_shape_when_loop_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir) / "supervisor"
            workspace_root = Path(tempdir) / "workspace"
            supervisor_root.mkdir(parents=True, exist_ok=True)
            workspace_root.mkdir(parents=True, exist_ok=True)

            with mock.patch(
                "automationplus.loop_status.health_mirror.collect_loop_health_snapshot",
                return_value={
                    "schemaVersion": 1,
                    "capturedAt": "2026-04-15T00:05:00Z",
                    "loopRuntime": {
                        "state": "off",
                        "hostMode": "tmux",
                        "sessionName": "automationplus-loop",
                        "windowName": None,
                        "paneId": None,
                        "panePid": None,
                        "paneCurrentCommand": None,
                        "paneCurrentPath": None,
                        "paneDead": None,
                        "tail": [],
                    },
                    "supervisor": {
                        "root": str(supervisor_root),
                        "activeIssueNumber": None,
                        "activeIssue": None,
                        "turnInProgress": None,
                        "decisionCycle": None,
                    },
                    "drift": {
                        "issueNumberMatches": None,
                        "workspaceMatches": None,
                        "stateMatches": None,
                    },
                    "failurePolicy": {
                        "schemaVersion": 1,
                        "degradedState": "healthy",
                        "restartEligible": False,
                        "operatorHold": False,
                        "summary": "No loop failure classification is active.",
                        "signature": None,
                    },
                },
            ), mock.patch(
                "automationplus.loop_status.health_mirror.apply_persisted_failure_tracking",
                side_effect=lambda snapshot, _artifact_path: snapshot,
            ):
                payload = loop_status.collect_loop_status(
                    supervisor_root=supervisor_root,
                    workspace_root=workspace_root,
                )

        self.assertEqual(payload["status"], "off")
        self.assertEqual(payload["runtime"]["state"], "off")
        self.assertEqual(payload["runtime"]["health"], "off")
        self.assertEqual(payload["launcher"]["service"]["state"], "stopped")
        self.assertEqual(payload["launcher"]["service"]["pid"], None)

    def test_collect_loop_status_surfaces_degraded_failure_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir) / "supervisor"
            workspace_root = Path(tempdir) / "workspace"
            supervisor_root.mkdir(parents=True, exist_ok=True)
            workspace_root.mkdir(parents=True, exist_ok=True)

            with mock.patch(
                "automationplus.loop_status.health_mirror.collect_loop_health_snapshot",
                return_value={
                    "schemaVersion": 1,
                    "capturedAt": "2026-04-15T00:06:00Z",
                    "loopRuntime": {
                        "state": "running",
                        "hostMode": "tmux",
                        "sessionName": "automationplus-loop",
                        "windowName": "loop",
                        "paneId": "%1",
                        "panePid": 4242,
                        "paneCurrentCommand": "node",
                        "paneCurrentPath": str(supervisor_root),
                        "paneDead": True,
                        "tail": ["2026-04-15T00:05:59Z loop error: ECONNRESET while refreshing queue state"],
                    },
                    "supervisor": {
                        "root": str(supervisor_root),
                        "activeIssueNumber": 27,
                        "activeIssue": {"issue_number": 27, "state": "reproducing"},
                        "turnInProgress": {"issueNumber": 27, "state": "reproducing"},
                        "decisionCycle": {
                            "issue": {"number": 27},
                            "decision": {"nextState": "reproducing"},
                        },
                    },
                    "drift": {
                        "issueNumberMatches": True,
                        "workspaceMatches": True,
                        "stateMatches": True,
                    },
                    "failurePolicy": {
                        "schemaVersion": 1,
                        "degradedState": "repeated-failure",
                        "restartEligible": False,
                        "operatorHold": True,
                        "summary": "2026-04-15T00:05:59Z loop error: ECONNRESET while refreshing queue state",
                        "signature": {
                            "id": "sig-demo",
                            "reason": "pane_dead",
                            "class": "transient",
                            "count": 2,
                            "firstSeenAt": "2026-04-15T00:03:00Z",
                            "lastSeenAt": "2026-04-15T00:06:00Z",
                            "normalizedSummary": "loop error: econnreset while refreshing queue state",
                        },
                    },
                },
            ), mock.patch(
                "automationplus.loop_status.health_mirror.apply_persisted_failure_tracking",
                side_effect=lambda snapshot, _artifact_path: snapshot,
            ):
                payload = loop_status.collect_loop_status(
                    supervisor_root=supervisor_root,
                    workspace_root=workspace_root,
                )

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["runtime"]["health"], "degraded")
        self.assertEqual(payload["failurePolicy"]["degradedState"], "repeated-failure")
        self.assertTrue(payload["failurePolicy"]["operatorHold"])

    def test_collect_loop_status_reuses_persisted_failure_registry_across_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir) / "supervisor"
            workspace_root = Path(tempdir) / "workspace"
            output_path = (
                workspace_root / ".codex-supervisor" / "health" / "loop-health.json"
            )
            supervisor_root.mkdir(parents=True, exist_ok=True)
            output_path.parent.mkdir(parents=True, exist_ok=True)

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
                    "2026-04-15T00:05:59Z loop error: ECONNRESET while refreshing queue state",
                ],
            }
            normalized = health_mirror._normalize_failure_text(runtime["tail"][0])
            signature_id = health_mirror._signature_id("pane_dead", normalized)
            output_path.write_text(
                (
                    "{\n"
                    '  "failureRegistry": {\n'
                    '    "schemaVersion": 1,\n'
                    '    "entries": {\n'
                    f'      "{signature_id}": {{\n'
                    f'        "id": "{signature_id}",\n'
                    '        "reason": "pane_dead",\n'
                    '        "signatureClass": "transient",\n'
                    '        "summary": "2026-04-15T00:03:00Z loop error: ECONNRESET while refreshing queue state",\n'
                    f'        "normalizedSummary": "{normalized}",\n'
                    '        "firstSeenAt": "2026-04-15T00:03:01Z",\n'
                    '        "lastSeenAt": "2026-04-15T00:03:01Z",\n'
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
                payload = loop_status.collect_loop_status(
                    supervisor_root=supervisor_root,
                    workspace_root=workspace_root,
                )

        self.assertEqual(payload["failurePolicy"]["degradedState"], "repeated-failure")
        self.assertEqual(payload["failurePolicy"]["signature"]["id"], signature_id)
        self.assertEqual(payload["failurePolicy"]["signature"]["count"], 2)
        self.assertTrue(payload["failurePolicy"]["operatorHold"])


if __name__ == "__main__":
    unittest.main()

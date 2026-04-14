import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automationplus.health_mirror as health_mirror


class LoopHealthMirrorTests(unittest.TestCase):
    def test_write_loop_health_snapshot_treats_retryable_http_status_as_transient(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir) / "supervisor"
            output_path = Path(tempdir) / "workspace" / ".codex-supervisor" / "health" / "loop-health.json"
            supervisor_root.mkdir(parents=True, exist_ok=True)

            runtime = {
                "state": "running",
                "hostMode": "tmux",
                "sessionName": "automationplus-loop",
                "windowName": "loop",
                "paneId": "%0",
                "panePid": 9969,
                "paneCurrentCommand": "node",
                "paneCurrentPath": str(supervisor_root),
                "paneDead": True,
                "tail": [
                    "2026-04-15T09:10:00Z loop error: HTTP 503 from launcher API",
                ],
            }

            with mock.patch(
                "automationplus.health_mirror._read_tmux_runtime",
                return_value=runtime,
            ):
                snapshot = health_mirror.write_loop_health_snapshot(
                    output_path=output_path,
                    supervisor_root=supervisor_root,
                    captured_at="2026-04-15T09:10:01Z",
                )

        self.assertEqual(snapshot["failurePolicy"]["degradedState"], "transient-failure")
        self.assertFalse(snapshot["failurePolicy"]["operatorHold"])
        self.assertEqual(snapshot["failurePolicy"]["signature"]["class"], "transient")
        self.assertEqual(
            snapshot["failurePolicy"]["signature"]["normalizedSummary"],
            "loop error: http <retryable-http-status> from launcher api",
        )

    def test_collect_loop_health_snapshot_classifies_unknown_failures_as_operator_hold(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir)

            with mock.patch(
                "automationplus.health_mirror._read_tmux_runtime",
                return_value={
                    "state": "running",
                    "hostMode": "tmux",
                    "sessionName": "automationplus-loop",
                    "windowName": "loop",
                    "paneId": "%0",
                    "panePid": 9969,
                    "paneCurrentCommand": "node",
                    "paneCurrentPath": str(supervisor_root),
                    "paneDead": True,
                    "tail": ["2026-04-15T09:15:00Z loop aborted with opaque crash payload"],
                },
            ):
                snapshot = health_mirror.collect_loop_health_snapshot(
                    supervisor_root=supervisor_root,
                    captured_at="2026-04-15T09:15:01Z",
                )

        self.assertEqual(snapshot["failurePolicy"]["degradedState"], "unsafe-unknown")
        self.assertFalse(snapshot["failurePolicy"]["restartEligible"])
        self.assertTrue(snapshot["failurePolicy"]["operatorHold"])
        self.assertEqual(snapshot["failurePolicy"]["signature"]["count"], 1)

    def test_write_loop_health_snapshot_persists_failure_signatures_and_repeated_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir) / "supervisor"
            output_path = Path(tempdir) / "workspace" / ".codex-supervisor" / "health" / "loop-health.json"
            supervisor_root.mkdir(parents=True, exist_ok=True)

            runtime = {
                "state": "running",
                "hostMode": "tmux",
                "sessionName": "automationplus-loop",
                "windowName": "loop",
                "paneId": "%0",
                "panePid": 9969,
                "paneCurrentCommand": "node",
                "paneCurrentPath": str(supervisor_root),
                "paneDead": True,
                "tail": [
                    "2026-04-15T09:10:00Z loop error: ECONNRESET while refreshing queue state",
                ],
            }

            with mock.patch(
                "automationplus.health_mirror._read_tmux_runtime",
                return_value=runtime,
            ):
                first_snapshot = health_mirror.write_loop_health_snapshot(
                    output_path=output_path,
                    supervisor_root=supervisor_root,
                    captured_at="2026-04-15T09:10:01Z",
                )

            self.assertEqual(first_snapshot["failurePolicy"]["degradedState"], "transient-failure")
            self.assertEqual(first_snapshot["failurePolicy"]["signature"]["count"], 1)
            signature_id = first_snapshot["failurePolicy"]["signature"]["id"]
            self.assertEqual(
                first_snapshot["failureRegistry"]["entries"][signature_id]["seenCount"],
                1,
            )

            with mock.patch(
                "automationplus.health_mirror._read_tmux_runtime",
                return_value=runtime,
            ):
                second_snapshot = health_mirror.write_loop_health_snapshot(
                    output_path=output_path,
                    supervisor_root=supervisor_root,
                    captured_at="2026-04-15T09:12:01Z",
                )

        self.assertEqual(second_snapshot["failurePolicy"]["degradedState"], "repeated-failure")
        self.assertEqual(second_snapshot["failurePolicy"]["signature"]["count"], 2)
        self.assertEqual(second_snapshot["failurePolicy"]["signature"]["id"], signature_id)
        self.assertEqual(
            second_snapshot["failureRegistry"]["entries"][signature_id]["seenCount"],
            2,
        )

    def test_write_loop_health_snapshot_recovers_from_corrupted_persisted_seen_count(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir) / "supervisor"
            output_path = Path(tempdir) / "workspace" / ".codex-supervisor" / "health" / "loop-health.json"
            supervisor_root.mkdir(parents=True, exist_ok=True)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            runtime = {
                "state": "running",
                "hostMode": "tmux",
                "sessionName": "automationplus-loop",
                "windowName": "loop",
                "paneId": "%0",
                "panePid": 9969,
                "paneCurrentCommand": "node",
                "paneCurrentPath": str(supervisor_root),
                "paneDead": True,
                "tail": [
                    "2026-04-15T09:10:00Z loop error: ECONNRESET while refreshing queue state",
                ],
            }

            normalized = health_mirror._normalize_failure_text(runtime["tail"][0])
            signature_id = health_mirror._signature_id("pane_dead", normalized)
            output_path.write_text(
                json.dumps(
                    {
                        "failureRegistry": {
                            "schemaVersion": 1,
                            "entries": {
                                signature_id: {
                                    "id": signature_id,
                                    "reason": "pane_dead",
                                    "signatureClass": "transient",
                                    "summary": runtime["tail"][0],
                                    "normalizedSummary": normalized,
                                    "firstSeenAt": "2026-04-15T09:00:01Z",
                                    "lastSeenAt": "2026-04-15T09:05:01Z",
                                    "seenCount": "not-a-number",
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "automationplus.health_mirror._read_tmux_runtime",
                return_value=runtime,
            ):
                snapshot = health_mirror.write_loop_health_snapshot(
                    output_path=output_path,
                    supervisor_root=supervisor_root,
                    captured_at="2026-04-15T09:10:01Z",
                )

        self.assertEqual(snapshot["failurePolicy"]["degradedState"], "transient-failure")
        self.assertEqual(snapshot["failurePolicy"]["signature"]["count"], 1)
        self.assertEqual(
            snapshot["failureRegistry"]["entries"][signature_id]["seenCount"],
            1,
        )

    def test_collect_loop_health_snapshot_treats_malformed_json_artifacts_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir)
            local_state_path = supervisor_root / ".local" / "state.json"
            turn_in_progress_path = supervisor_root / ".codex-supervisor" / "turn-in-progress.json"

            local_state_path.parent.mkdir(parents=True, exist_ok=True)
            turn_in_progress_path.parent.mkdir(parents=True, exist_ok=True)

            local_state_path.write_text('{"activeIssueNumber": ', encoding="utf-8")
            turn_in_progress_path.write_text('{"issueNumber": ', encoding="utf-8")

            with mock.patch(
                "automationplus.health_mirror._read_tmux_runtime",
                return_value={
                    "state": "running",
                    "hostMode": "tmux",
                    "sessionName": "automationplus-loop",
                    "windowName": "loop",
                    "paneId": "%0",
                    "panePid": 9969,
                    "paneCurrentCommand": "node",
                    "paneCurrentPath": str(supervisor_root),
                    "paneDead": False,
                    "tail": [],
                },
            ):
                snapshot = health_mirror.collect_loop_health_snapshot(
                    supervisor_root=supervisor_root,
                    captured_at="2026-04-14T09:15:00Z",
                )

        self.assertEqual(snapshot["capturedAt"], "2026-04-14T09:15:00Z")
        self.assertIsNone(snapshot["supervisor"]["activeIssueNumber"])
        self.assertIsNone(snapshot["supervisor"]["activeIssue"])
        self.assertIsNone(snapshot["supervisor"]["turnInProgress"])
        self.assertIsNone(snapshot["drift"]["issueNumberMatches"])
        self.assertIsNone(snapshot["drift"]["stateMatches"])

    def test_collect_loop_health_snapshot_mirrors_tmux_and_supervisor_state(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            supervisor_root = Path(tempdir)
            local_state_path = supervisor_root / ".local" / "state.json"
            turn_in_progress_path = supervisor_root / ".codex-supervisor" / "turn-in-progress.json"
            replay_snapshot_path = (
                supervisor_root
                / ".codex-supervisor"
                / "replay"
                / "decision-cycle-snapshot.json"
            )

            local_state_path.parent.mkdir(parents=True, exist_ok=True)
            turn_in_progress_path.parent.mkdir(parents=True, exist_ok=True)
            replay_snapshot_path.parent.mkdir(parents=True, exist_ok=True)

            local_state_path.write_text(
                json.dumps(
                    {
                        "activeIssueNumber": 5,
                        "issues": {
                            "5": {
                                "issue_number": 5,
                                "state": "reproducing",
                                "branch": "codex/issue-5",
                                "workspace": "/tmp/workspaces/issue-5",
                                "updated_at": "2026-04-14T08:59:22.105Z",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            turn_in_progress_path.write_text(
                json.dumps(
                    {
                        "issueNumber": 5,
                        "state": "reproducing",
                        "startedAt": "2026-04-14T08:59:23.217Z",
                    }
                ),
                encoding="utf-8",
            )
            replay_snapshot_path.write_text(
                json.dumps(
                    {
                        "capturedAt": "2026-04-14T08:59:23.191Z",
                        "issue": {
                            "number": 5,
                            "title": "Build supervisor health mirror for loop observation",
                        },
                        "local": {
                            "record": {
                                "issue_number": 5,
                                "state": "reproducing",
                                "branch": "codex/issue-5",
                                "workspace": "/tmp/workspaces/issue-5",
                                "updated_at": "2026-04-14T08:59:22.105Z",
                            }
                        },
                        "decision": {
                            "nextState": "reproducing",
                            "shouldRunCodex": True,
                            "blockedReason": None,
                            "failureContext": None,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "automationplus.health_mirror._read_tmux_runtime",
                return_value={
                    "state": "running",
                    "hostMode": "tmux",
                    "sessionName": "automationplus-loop",
                    "windowName": "loop",
                    "paneId": "%0",
                    "panePid": 9969,
                    "paneCurrentCommand": "node",
                    "paneCurrentPath": str(supervisor_root),
                    "paneDead": False,
                    "tail": [
                        "2026-04-14T08:57:17.915Z issue=#4 state=merging",
                        "2026-04-14T08:59:23.195Z issue=#5 state=reproducing",
                    ],
                },
            ):
                snapshot = health_mirror.collect_loop_health_snapshot(
                    supervisor_root=supervisor_root,
                    session_name="automationplus-loop",
                    capture_lines=2,
                    captured_at="2026-04-14T09:00:00Z",
                )

        self.assertEqual(snapshot["schemaVersion"], 1)
        self.assertEqual(snapshot["capturedAt"], "2026-04-14T09:00:00Z")
        self.assertEqual(snapshot["loopRuntime"]["state"], "running")
        self.assertEqual(snapshot["loopRuntime"]["hostMode"], "tmux")
        self.assertEqual(
            snapshot["loopRuntime"]["tail"],
            [
                "2026-04-14T08:57:17.915Z issue=#4 state=merging",
                "2026-04-14T08:59:23.195Z issue=#5 state=reproducing",
            ],
        )
        self.assertEqual(snapshot["supervisor"]["activeIssueNumber"], 5)
        self.assertEqual(snapshot["supervisor"]["turnInProgress"]["issueNumber"], 5)
        self.assertEqual(snapshot["supervisor"]["decisionCycle"]["issue"]["number"], 5)
        self.assertEqual(snapshot["supervisor"]["decisionCycle"]["decision"]["nextState"], "reproducing")
        self.assertEqual(
            snapshot["drift"],
            {
                "issueNumberMatches": True,
                "workspaceMatches": True,
                "stateMatches": True,
            },
        )

    def test_run_tmux_raises_health_mirror_error_on_timeout(self) -> None:
        with mock.patch(
            "automationplus.health_mirror.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["tmux", "has-session", "-t", "automationplus-loop"],
                timeout=5,
            ),
        ):
            with self.assertRaisesRegex(
                health_mirror.HealthMirrorError,
                r"timed out after 5s: tmux has-session -t automationplus-loop",
            ):
                health_mirror._run_tmux(
                    "has-session",
                    "-t",
                    "automationplus-loop",
                    timeout=5,
                )


if __name__ == "__main__":
    unittest.main()

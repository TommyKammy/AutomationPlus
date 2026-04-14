import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automationplus.health_mirror as health_mirror


class LoopHealthMirrorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

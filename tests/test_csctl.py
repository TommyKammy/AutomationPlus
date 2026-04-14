import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

import automationplus.csctl as csctl


REPO_ROOT = Path(__file__).resolve().parents[1]
CSCTL = REPO_ROOT / "csctl"


class CsctlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.config_dir = self.workspace / ".codex-supervisor"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.repo_root = REPO_ROOT

        self.helper = self.workspace / "fake_diag.py"
        self.helper.write_text(
            textwrap.dedent(
                """\
                import json
                import sys

                command = sys.argv[1]
                if command == "fail":
                    print(json.dumps({"error": "backend failed"}))
                    raise SystemExit(3)
                if command == "fail-no-json":
                    print("backend exploded", file=sys.stderr)
                    raise SystemExit(4)
                if command == "invalid-json-success":
                    print("not valid json")
                    raise SystemExit(0)
                if command == "empty-success":
                    raise SystemExit(0)
                if command == "requeue":
                    print(json.dumps({
                        "action": "requeue",
                        "issueNumber": int(sys.argv[2]),
                        "summary": "Requeued issue.",
                    }))
                    raise SystemExit(0)

                print(json.dumps({
                    "source_command": command,
                    "args": sys.argv[2:],
                }))
                """
            ),
            encoding="utf-8",
        )
        self.fake_supervisor = self.workspace / "fake_supervisor.py"
        self.fake_supervisor.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import os
                import sys

                args = sys.argv[1:]
                if len(args) < 3 or args[-2] != "--config":
                    print("missing required --config", file=sys.stderr)
                    raise SystemExit(2)

                command = args[0]
                command_args = args[1:-2]
                config_path = args[-1]
                bad_json_command = os.environ.get("FAKE_SUPERVISOR_BAD_JSON_COMMAND")

                if bad_json_command == command:
                    print("{not valid json")
                    raise SystemExit(0)

                if command == "status":
                    print("backend=shadow-backend")
                    print("source_command=shadow-source")
                    print("supervisor_command=shadow-supervisor")
                    print("config_path=/tmp/shadow-config.json")
                    print("issue_number=999")
                    print("argv=shadow-argv")
                    print("state=reproducing")
                    print(f"config_path={config_path}")
                    raise SystemExit(0)

                if command == "doctor":
                    print("doctor summary without separators")
                    raise SystemExit(0)

                if command == "explain":
                    if command_args != ["17"]:
                        print(f"unexpected explain args: {command_args}", file=sys.stderr)
                        raise SystemExit(3)
                    print("issue=#17")
                    print("runnable=yes")
                    raise SystemExit(0)

                if command == "issue-lint":
                    if command_args != ["17"]:
                        print(f"unexpected issue-lint args: {command_args}", file=sys.stderr)
                        raise SystemExit(4)
                    print("execution_ready=yes")
                    raise SystemExit(0)

                if command == "run-once":
                    if command_args not in ([], ["--dry-run"]):
                        print(f"unexpected run-once args: {command_args}", file=sys.stderr)
                        raise SystemExit(6)
                    suffix = " (dry-run)" if command_args == ["--dry-run"] else ""
                    print(f"run-once complete{suffix}")
                    raise SystemExit(0)

                if command == "requeue":
                    if command_args != ["17"]:
                        print(f"unexpected requeue args: {command_args}", file=sys.stderr)
                        raise SystemExit(7)
                    print('{"backend":"shadow-backend","source_command":"shadow-source","supervisor_command":"shadow-supervisor","issue_number":"999","argv":["shadow"],"config_path":"/tmp/shadow-config.json","action":"requeue","issueNumber":17,"summary":"Requeued issue #17.","outcome":"mutated"}')
                    raise SystemExit(0)

                if command == "prune-orphaned-workspaces":
                    if command_args:
                        print(f"unexpected prune args: {command_args}", file=sys.stderr)
                        raise SystemExit(8)
                    print('{"action":"prune-orphaned-workspaces","outcome":"completed","summary":"Pruned 0 orphaned workspaces.","pruned":[],"skipped":[]}')
                    raise SystemExit(0)

                if command == "reset-corrupt-json-state":
                    if command_args:
                        print(f"unexpected reset args: {command_args}", file=sys.stderr)
                        raise SystemExit(9)
                    print('{"action":"reset-corrupt-json-state","outcome":"completed","summary":"No corrupt JSON state files were present.","reset":[]}')
                    raise SystemExit(0)

                print(f"unexpected command: {command}", file=sys.stderr)
                raise SystemExit(5)
                """
            ),
            encoding="utf-8",
        )
        self.fake_supervisor.chmod(0o755)
        self.fake_supervisor_config = self.workspace / "fake-supervisor.config.json"
        self.fake_supervisor_config.write_text("{}", encoding="utf-8")

        config = {
            "diagnostics": {
                "status-json": [sys.executable, str(self.helper), "status-json"],
                "doctor-json": [sys.executable, str(self.helper), "doctor-json"],
                "explain-json": [sys.executable, str(self.helper), "explain-json"],
                "issue-lint-json": [sys.executable, str(self.helper), "issue-lint-json"],
                "loop-status": [sys.executable, str(self.helper), "loop-status"],
                "restart-decision": [sys.executable, str(self.helper), "restart-decision"],
                "run-once": [sys.executable, str(self.helper), "run-once"],
                "requeue": [sys.executable, str(self.helper), "requeue"],
                "prune-orphaned-workspaces": [
                    sys.executable,
                    str(self.helper),
                    "prune-orphaned-workspaces",
                ],
                "reset-corrupt-json-state": [
                    sys.executable,
                    str(self.helper),
                    "reset-corrupt-json-state",
                ],
            }
        }
        (self.config_dir / "config.json").write_text(
            json.dumps(config),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_csctl(self, *args: str, env: Optional[dict] = None) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(CSCTL), *args]
        merged_env = os.environ.copy()
        merged_env.update(env or {})
        return subprocess.run(
            cmd,
            cwd=self.workspace,
            env=merged_env,
            capture_output=True,
            text=True,
        )

    def write_bridge_override(self) -> Path:
        override = self.workspace / "bridge-override.json"
        override.write_text(
            json.dumps(
                {
                    "diagnostics": {
                        "status-json": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "status-json",
                        ],
                        "doctor-json": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "doctor-json",
                        ],
                        "explain-json": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "explain-json",
                        ],
                        "issue-lint-json": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "issue-lint-json",
                        ],
                        "loop-status": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "loop-status",
                        ],
                        "restart-decision": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "restart-decision",
                        ],
                        "run-once": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "run-once",
                        ],
                        "requeue": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "requeue",
                        ],
                        "prune-orphaned-workspaces": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "prune-orphaned-workspaces",
                        ],
                        "reset-corrupt-json-state": [
                            sys.executable,
                            str(self.repo_root / "scripts" / "diagnostics_backend.py"),
                            "reset-corrupt-json-state",
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        return override

    def test_status_json_uses_default_config_and_wraps_result(self) -> None:
        result = self.run_csctl("status-json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "command": "status-json",
                "config": str((self.config_dir / "config.json").resolve()),
                "result": {
                    "source_command": "status-json",
                    "args": [],
                },
            },
        )

    def test_repo_default_config_wires_all_safe_mutation_backends(self) -> None:
        repo_config = json.loads((REPO_ROOT / ".codex-supervisor" / "config.json").read_text(encoding="utf-8"))
        diagnostics = repo_config.get("diagnostics")

        self.assertIsInstance(diagnostics, dict)
        for command in (
            "run-once",
            "requeue",
            "prune-orphaned-workspaces",
            "reset-corrupt-json-state",
        ):
            with self.subTest(command=command):
                self.assertIn(command, diagnostics)
                self.assertIsInstance(diagnostics[command], list)
                self.assertTrue(diagnostics[command])

    def test_repo_default_config_wires_all_read_only_backends(self) -> None:
        repo_config = json.loads((REPO_ROOT / ".codex-supervisor" / "config.json").read_text(encoding="utf-8"))
        diagnostics = repo_config.get("diagnostics")

        self.assertIsInstance(diagnostics, dict)
        for command in (
            "status-json",
            "doctor-json",
            "explain-json",
            "issue-lint-json",
            "loop-status",
            "restart-decision",
        ):
            with self.subTest(command=command):
                self.assertIn(command, diagnostics)
                self.assertIsInstance(diagnostics[command], list)
                self.assertTrue(diagnostics[command])

    def test_all_read_only_commands_are_exposed(self) -> None:
        for command in (
            "status-json",
            "doctor-json",
            "loop-status",
            "restart-decision",
        ):
            with self.subTest(command=command):
                result = self.run_csctl(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["command"], command)
                self.assertEqual(payload["result"]["source_command"], command)

    def test_restart_decision_bridge_writes_review_artifacts(self) -> None:
        override = self.write_bridge_override()
        supervisor_root = self.workspace / "supervisor"
        workspace_root = self.workspace / "repo"
        supervisor_root.mkdir(parents=True, exist_ok=True)
        workspace_root.mkdir(parents=True, exist_ok=True)

        result = self.run_csctl(
            "restart-decision",
            "--config",
            str(override),
            env={
                "AUTOMATIONPLUS_LOOP_STATUS_SUPERVISOR_ROOT": str(supervisor_root),
                "AUTOMATIONPLUS_LOOP_STATUS_WORKSPACE_ROOT": str(workspace_root),
                "AUTOMATIONPLUS_RESTART_MAX_RESTARTS": "2",
                "AUTOMATIONPLUS_RESTART_WINDOW_SECONDS": "900",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "restart-decision")
        self.assertEqual(payload["result"]["artifactType"], "restart_decision")
        self.assertEqual(payload["result"]["source_command"], "restart-decision")
        self.assertTrue(Path(payload["result"]["artifactPath"]).is_file())
        self.assertTrue(Path(payload["result"]["budgetPath"]).is_file())

    def test_issue_scoped_read_only_commands_require_issue_number(self) -> None:
        for command in ("explain-json", "issue-lint-json"):
            with self.subTest(command=command):
                missing = self.run_csctl(command)
                self.assertEqual(missing.returncode, 1, missing.stderr)
                missing_payload = json.loads(missing.stdout)
                self.assertEqual(missing_payload["error"]["code"], "invalid_arguments")
                self.assertIn("requires one issue number", missing_payload["error"]["message"])

                result = self.run_csctl(command, "17")
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["command"], command)
                self.assertEqual(payload["result"]["source_command"], command)
                self.assertEqual(payload["result"]["args"], ["17"])

    def test_all_safe_mutation_commands_are_exposed(self) -> None:
        expected_results = {
            "run-once": {
                "source_command": "run-once",
                "args": [],
            },
            "requeue": {
                "action": "requeue",
                "issueNumber": 17,
                "summary": "Requeued issue.",
            },
            "prune-orphaned-workspaces": {
                "source_command": "prune-orphaned-workspaces",
                "args": [],
            },
            "reset-corrupt-json-state": {
                "source_command": "reset-corrupt-json-state",
                "args": [],
            },
        }

        for command, expected_result in expected_results.items():
            with self.subTest(command=command):
                args = (command, "17") if command == "requeue" else (command,)
                result = self.run_csctl(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["command"], command)
                self.assertEqual(payload["result"], expected_result)

    def test_run_once_accepts_dry_run_without_generic_passthrough(self) -> None:
        result = self.run_csctl("run-once", "--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "run-once")
        self.assertEqual(
            payload["result"],
            {
                "source_command": "run-once",
                "args": ["--dry-run"],
            },
        )

    def test_requeue_requires_one_positive_issue_number(self) -> None:
        missing = self.run_csctl("requeue")
        self.assertEqual(missing.returncode, 1, missing.stderr)
        missing_payload = json.loads(missing.stdout)
        self.assertEqual(missing_payload["error"]["code"], "invalid_arguments")
        self.assertIn("requires one issue number", missing_payload["error"]["message"])

        invalid = self.run_csctl("requeue", "0")
        self.assertEqual(invalid.returncode, 1, invalid.stderr)
        invalid_payload = json.loads(invalid.stdout)
        self.assertEqual(invalid_payload["error"]["code"], "invalid_arguments")
        self.assertIn("positive integer", invalid_payload["error"]["message"])

    def test_dry_run_is_refused_for_non_run_once_mutations(self) -> None:
        result = self.run_csctl("prune-orphaned-workspaces", "--dry-run")

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "invalid_arguments")
        self.assertEqual(payload["error"]["arguments"], ["--dry-run"])

    def test_wrapper_exposes_backend_failure_in_normalized_envelope(self) -> None:
        override = self.workspace / "override.json"
        override.write_text(
            json.dumps(
                {
                    "diagnostics": {
                        "status-json": [sys.executable, str(self.helper), "fail"]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_csctl("status-json", "--config", str(override))

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "status-json")
        self.assertEqual(payload["config"], str(override.resolve()))
        self.assertEqual(payload["error"]["code"], "backend_failed")
        self.assertEqual(payload["error"]["exit_code"], 3)
        self.assertEqual(payload["error"]["stderr_json"]["error"], "backend failed")

    def test_wrapper_exposes_backend_failure_without_json_output(self) -> None:
        override = self.workspace / "override-no-json.json"
        override.write_text(
            json.dumps(
                {
                    "diagnostics": {
                        "status-json": [sys.executable, str(self.helper), "fail-no-json"]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_csctl("status-json", "--config", str(override))

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "backend_failed")
        self.assertEqual(payload["error"]["exit_code"], 4)
        self.assertEqual(payload["error"]["stderr"], "backend exploded")
        self.assertNotIn("stderr_json", payload["error"])

    def test_status_json_bridge_invokes_supervisor_status_with_real_backend_shape(self) -> None:
        override = self.write_bridge_override()

        result = self.run_csctl(
            "status-json",
            "--config",
            str(override),
            env={
                "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CMD_JSON": json.dumps(
                    [sys.executable, str(self.fake_supervisor)]
                ),
                "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CONFIG": str(self.fake_supervisor_config),
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "status-json")
        self.assertEqual(payload["result"]["backend"], "codex-supervisor")
        self.assertEqual(payload["result"]["source_command"], "status-json")
        self.assertEqual(payload["result"]["supervisor_command"], "status")
        self.assertIsNone(payload["result"]["issue_number"])
        self.assertIsInstance(payload["result"]["argv"], list)
        self.assertEqual(payload["result"]["config_path"], str(self.fake_supervisor_config.resolve()))
        self.assertEqual(payload["result"]["state"], "reproducing")

    def test_bridge_failures_are_normalized_when_supervisor_output_is_malformed(self) -> None:
        override = self.write_bridge_override()

        result = self.run_csctl(
            "doctor-json",
            "--config",
            str(override),
            env={
                "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CMD_JSON": json.dumps(
                    [sys.executable, str(self.fake_supervisor)]
                ),
                "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CONFIG": str(self.fake_supervisor_config),
            },
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "backend_failed")
        self.assertEqual(payload["error"]["stderr_json"]["code"], "invalid_supervisor_output")
        self.assertEqual(payload["error"]["stderr_json"]["supervisor_command"], "doctor")

    def test_json_bridge_failures_are_normalized_when_supervisor_output_is_malformed(self) -> None:
        override = self.write_bridge_override()

        result = self.run_csctl(
            "requeue",
            "17",
            "--config",
            str(override),
            env={
                "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CMD_JSON": json.dumps(
                    [sys.executable, str(self.fake_supervisor)]
                ),
                "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CONFIG": str(self.fake_supervisor_config),
                "FAKE_SUPERVISOR_BAD_JSON_COMMAND": "requeue",
            },
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "backend_failed")
        self.assertEqual(payload["error"]["stderr_json"]["code"], "invalid_supervisor_output")
        self.assertEqual(payload["error"]["stderr_json"]["supervisor_command"], "requeue")
        self.assertEqual(
            payload["error"]["stderr_json"]["config_path"],
            str(self.fake_supervisor_config.resolve()),
        )

    def test_mutation_bridge_normalizes_safe_supervisor_commands(self) -> None:
        override = self.write_bridge_override()
        env = {
            "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CMD_JSON": json.dumps(
                [sys.executable, str(self.fake_supervisor)]
            ),
            "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CONFIG": str(self.fake_supervisor_config),
        }

        cases = (
            ("run-once", (), {"summary": "run-once complete", "dry_run": False}),
            ("run-once", ("--dry-run",), {"summary": "run-once complete (dry-run)", "dry_run": True}),
            ("requeue", ("17",), {"action": "requeue", "issueNumber": 17, "outcome": "mutated"}),
            (
                "prune-orphaned-workspaces",
                (),
                {"action": "prune-orphaned-workspaces", "outcome": "completed"},
            ),
            (
                "reset-corrupt-json-state",
                (),
                {"action": "reset-corrupt-json-state", "outcome": "completed"},
            ),
        )

        for command, extra_args, expected in cases:
            with self.subTest(command=command, extra_args=extra_args):
                result = self.run_csctl(command, *extra_args, "--config", str(override), env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["command"], command)
                self.assertEqual(payload["result"]["backend"], "codex-supervisor")
                self.assertEqual(payload["result"]["source_command"], command)
                self.assertEqual(payload["result"]["supervisor_command"], command)
                self.assertEqual(
                    payload["result"]["issue_number"],
                    "17" if command == "requeue" else None,
                )
                self.assertIsInstance(payload["result"]["argv"], list)
                self.assertEqual(
                    payload["result"]["config_path"],
                    str(self.fake_supervisor_config.resolve()),
                )
                for key, value in expected.items():
                    self.assertEqual(payload["result"][key], value)

    def test_wrapper_rejects_unknown_passthrough_arguments(self) -> None:
        result = self.run_csctl("status-json", "--mutating-flag")

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], "invalid_arguments")
        self.assertEqual(payload["error"]["arguments"], ["--mutating-flag"])

    def test_wrapper_normalizes_missing_command(self) -> None:
        result = self.run_csctl()

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], False)
        self.assertIsNone(payload["command"])
        self.assertEqual(payload["error"]["code"], "invalid_arguments")
        self.assertIn("required", payload["error"]["message"])

    def test_wrapper_normalizes_invalid_command_choice(self) -> None:
        result = self.run_csctl("mutate-json")

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], False)
        self.assertIsNone(payload["command"])
        self.assertEqual(payload["error"]["code"], "invalid_arguments")
        self.assertIn("invalid choice", payload["error"]["message"])

    def test_wrapper_normalizes_missing_config_value(self) -> None:
        result = self.run_csctl("status-json", "--config")

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "status-json")
        self.assertEqual(payload["error"]["code"], "invalid_arguments")
        self.assertIn("expected one argument", payload["error"]["message"])

    def test_wrapper_normalizes_invalid_backend_json(self) -> None:
        override = self.workspace / "override-invalid-json.json"
        override.write_text(
            json.dumps(
                {
                    "diagnostics": {
                        "status-json": [sys.executable, str(self.helper), "invalid-json-success"]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_csctl("status-json", "--config", str(override))

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "status-json")
        self.assertEqual(payload["error"]["code"], "invalid_backend_output")
        self.assertEqual(payload["error"]["stdout"], "not valid json")

    def test_wrapper_normalizes_empty_backend_stdout(self) -> None:
        override = self.workspace / "override-empty.json"
        override.write_text(
            json.dumps(
                {
                    "diagnostics": {
                        "status-json": [sys.executable, str(self.helper), "empty-success"]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_csctl("status-json", "--config", str(override))

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "status-json")
        self.assertEqual(payload["error"]["code"], "invalid_backend_output")
        self.assertIn("empty stdout", payload["error"]["message"])

    def test_load_config_wraps_os_errors(self) -> None:
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(csctl.CsctlError) as ctx:
                csctl._load_config(self.workspace / "config.json")

        self.assertEqual(ctx.exception.code, "config_read_failed")
        self.assertIn("denied", ctx.exception.message)
        self.assertEqual(ctx.exception.details["os_error"], "denied")

    def test_run_backend_wraps_os_errors(self) -> None:
        with mock.patch("subprocess.run", side_effect=PermissionError("blocked")):
            with self.assertRaises(csctl.CsctlError) as ctx:
                csctl._run_backend(["fake-backend"])

        self.assertEqual(ctx.exception.code, "backend_launch_failed")
        self.assertIn("blocked", ctx.exception.message)
        self.assertEqual(ctx.exception.details["os_error"], "blocked")


if __name__ == "__main__":
    unittest.main()

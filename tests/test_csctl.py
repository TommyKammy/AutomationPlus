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

        config = {
            "diagnostics": {
                "status-json": [sys.executable, str(self.helper), "status-json"],
                "doctor-json": [sys.executable, str(self.helper), "doctor-json"],
                "explain-json": [sys.executable, str(self.helper), "explain-json"],
                "issue-lint-json": [sys.executable, str(self.helper), "issue-lint-json"],
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

    def test_all_read_only_commands_are_exposed(self) -> None:
        for command in (
            "status-json",
            "doctor-json",
            "explain-json",
            "issue-lint-json",
        ):
            with self.subTest(command=command):
                result = self.run_csctl(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["command"], command)
                self.assertEqual(payload["result"]["source_command"], command)

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

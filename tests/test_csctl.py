import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Optional


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


if __name__ == "__main__":
    unittest.main()

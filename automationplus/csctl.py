import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional


SUPPORTED_COMMANDS = (
    "status-json",
    "doctor-json",
    "explain-json",
    "issue-lint-json",
)
DEFAULT_CONFIG_PATH = Path(".codex-supervisor/config.json")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="csctl")
    parser.add_argument("command", choices=SUPPORTED_COMMANDS)
    parser.add_argument("--config", dest="config_path")
    return parser


def _resolve_config_path(config_override: Optional[str]) -> Path:
    if config_override:
        return Path(config_override).expanduser().resolve()
    return (Path.cwd() / DEFAULT_CONFIG_PATH).resolve()


def _load_config(config_path: Path) -> dict:
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CsctlError(
            "config_not_found",
            f"Config file not found: {config_path}",
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CsctlError(
            "invalid_config",
            f"Config file is not valid JSON: {config_path}",
        ) from exc

    if not isinstance(data, dict):
        raise CsctlError("invalid_config", "Config root must be a JSON object")
    return data


def _command_argv(config: dict, command: str) -> List[str]:
    diagnostics = config.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise CsctlError(
            "invalid_config",
            "Config must define a diagnostics object",
        )

    argv = diagnostics.get(command)
    if not isinstance(argv, list) or not argv or not all(
        isinstance(item, str) and item for item in argv
    ):
        raise CsctlError(
            "invalid_config",
            f"Config diagnostics.{command} must be a non-empty string array",
        )
    return argv


def _run_backend(argv: Iterable[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise CsctlError(
            "backend_not_found",
            f"Backend executable not found: {list(argv)[0]}",
        ) from exc


def _parse_backend_output(result: subprocess.CompletedProcess) -> object:
    stdout = result.stdout.strip()
    if not stdout:
        raise CsctlError("invalid_backend_output", "Backend produced empty stdout")

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CsctlError(
            "invalid_backend_output",
            "Backend stdout is not valid JSON",
            details={"stdout": stdout},
        ) from exc


class CsctlError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    return 0 if payload["ok"] else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args, backend_args = parser.parse_known_args(argv)

    config_path = _resolve_config_path(args.config_path)

    try:
        config = _load_config(config_path)
        backend_argv = _command_argv(config, args.command) + backend_args
        backend_result = _run_backend(backend_argv)
        backend_payload = _parse_backend_output(backend_result)
    except CsctlError as exc:
        return _emit(
            {
                "ok": False,
                "command": args.command,
                "config": str(config_path),
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    **exc.details,
                },
            }
        )

    if backend_result.returncode != 0:
        return _emit(
            {
                "ok": False,
                "command": args.command,
                "config": str(config_path),
                "error": {
                    "code": "backend_failed",
                    "message": "Backend command exited with a non-zero status",
                    "exit_code": backend_result.returncode,
                    "stderr": backend_result.stderr.strip(),
                    "stderr_json": backend_payload,
                },
            }
        )

    return _emit(
        {
            "ok": True,
            "command": args.command,
            "config": str(config_path),
            "result": backend_payload,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())

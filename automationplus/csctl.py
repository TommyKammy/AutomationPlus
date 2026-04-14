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
    except OSError as exc:
        raise CsctlError(
            "config_read_failed",
            f"Failed reading config file: {config_path}: {exc}",
            details={"os_error": str(exc)},
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
    argv_list = list(argv)
    try:
        return subprocess.run(
            argv_list,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise CsctlError(
            "backend_not_found",
            f"Backend executable not found: {argv_list[0]}",
        ) from exc
    except OSError as exc:
        raise CsctlError(
            "backend_launch_failed",
            f"Failed to launch backend command: {exc}",
            details={"os_error": str(exc)},
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


def _maybe_parse_json(text: str) -> Optional[object]:
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


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
    args, extra_args = parser.parse_known_args(argv)

    config_path = _resolve_config_path(args.config_path)

    try:
        if extra_args:
            raise CsctlError(
                "invalid_arguments",
                f"Unsupported arguments for {args.command}: {' '.join(extra_args)}",
                details={"arguments": extra_args},
            )
        config = _load_config(config_path)
        backend_argv = _command_argv(config, args.command)
        backend_result = _run_backend(backend_argv)
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
        stderr = backend_result.stderr.strip()
        stdout = backend_result.stdout.strip()
        structured_error = _maybe_parse_json(stderr) or _maybe_parse_json(stdout)
        error_payload = {
            "code": "backend_failed",
            "message": "Backend command exited with a non-zero status",
            "exit_code": backend_result.returncode,
            "stderr": stderr,
        }
        if stdout:
            error_payload["stdout"] = stdout
        if structured_error is not None:
            error_payload["stderr_json"] = structured_error

        return _emit(
            {
                "ok": False,
                "command": args.command,
                "config": str(config_path),
                "error": error_payload,
            }
        )

    backend_payload = _parse_backend_output(backend_result)

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

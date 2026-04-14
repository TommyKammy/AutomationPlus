#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

COMMAND_MAP = {
    "status-json": {
        "supervisor_command": "status",
        "needs_issue_number": False,
        "supports_dry_run": False,
        "output_mode": "parsed_kv",
    },
    "doctor-json": {
        "supervisor_command": "doctor",
        "needs_issue_number": False,
        "supports_dry_run": False,
        "output_mode": "parsed_kv",
    },
    "explain-json": {
        "supervisor_command": "explain",
        "needs_issue_number": True,
        "supports_dry_run": False,
        "output_mode": "parsed_kv",
    },
    "issue-lint-json": {
        "supervisor_command": "issue-lint",
        "needs_issue_number": True,
        "supports_dry_run": False,
        "output_mode": "parsed_kv",
    },
    "loop-status": {
        "supervisor_command": None,
        "needs_issue_number": False,
        "supports_dry_run": False,
        "output_mode": "loop_status",
    },
    "run-once": {
        "supervisor_command": "run-once",
        "needs_issue_number": False,
        "supports_dry_run": True,
        "output_mode": "summary_text",
    },
    "requeue": {
        "supervisor_command": "requeue",
        "needs_issue_number": True,
        "supports_dry_run": False,
        "output_mode": "json",
    },
    "prune-orphaned-workspaces": {
        "supervisor_command": "prune-orphaned-workspaces",
        "needs_issue_number": False,
        "supports_dry_run": False,
        "output_mode": "json",
    },
    "reset-corrupt-json-state": {
        "supervisor_command": "reset-corrupt-json-state",
        "needs_issue_number": False,
        "supports_dry_run": False,
        "output_mode": "json",
    },
}
KEY_PATTERN = re.compile(r"(?<!\S)([A-Za-z0-9_:-]+)=")
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automationplus import loop_status

DEFAULT_SUPERVISOR_ROOT = REPO_ROOT.parent.parent / "AutomationPlus-codex-supervisor"
DEFAULT_SUPERVISOR_CMD = ["node", str(DEFAULT_SUPERVISOR_ROOT / "dist" / "index.js")]
DEFAULT_SUPERVISOR_CONFIG = DEFAULT_SUPERVISOR_ROOT / "supervisor.config.coderabbit.json"
DEFAULT_LOOP_STATUS_WORKSPACE_ROOT = REPO_ROOT


class BackendError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _emit(payload: Dict[str, object], *, stream) -> int:
    stream.write(json.dumps(payload))
    stream.write("\n")
    return 0


def _coerce_value(value: str) -> object:
    if value == "yes":
        return True
    if value == "no":
        return False
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "none":
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _merge_value(target: Dict[str, object], key: str, value: object) -> None:
    current = target.get(key)
    if current is None and key not in target:
        target[key] = value
        return
    if isinstance(current, list):
        current.append(value)
        return
    target[key] = [current, value]


def _parse_line(line: str) -> Tuple[Optional[str], Dict[str, object]]:
    line = line.strip()
    if not line:
        raise BackendError("invalid_supervisor_output", "Supervisor produced an empty diagnostic line.")
    if "=" not in line:
        raise BackendError(
            "invalid_supervisor_output",
            f"Supervisor output line is not parseable: {line}",
            line=line,
        )

    label: Optional[str] = None
    remainder = line
    first_token, separator, rest = line.partition(" ")
    if "=" not in first_token and separator:
        label = first_token
        remainder = rest.strip()

    matches = list(KEY_PATTERN.finditer(remainder))
    if not matches:
        raise BackendError(
            "invalid_supervisor_output",
            f"Supervisor output line is not parseable: {line}",
            line=line,
        )

    fields: Dict[str, object] = {}
    for index, match in enumerate(matches):
        key = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(remainder)
        value = remainder[start:end].strip()
        fields[key] = _coerce_value(value)
    return label, fields


def _parse_supervisor_stdout(stdout: str) -> Dict[str, object]:
    stripped = stdout.strip()
    if not stripped:
        raise BackendError("invalid_supervisor_output", "Supervisor produced empty stdout.")

    parsed: Dict[str, object] = {}
    records: List[Dict[str, object]] = []
    for raw_line in stripped.splitlines():
        label, fields = _parse_line(raw_line)
        record = {"line": raw_line, "fields": fields}
        if label is not None:
            record["label"] = label
        records.append(record)

        if label is None:
            for key, value in fields.items():
                _merge_value(parsed, key, value)
            continue

        existing = parsed.get(label)
        if existing is None and label not in parsed:
            parsed[label] = fields
        elif isinstance(existing, list):
            existing.append(fields)
        else:
            parsed[label] = [existing, fields]

    parsed["records"] = records
    parsed["raw_stdout"] = stripped
    return parsed


def _load_supervisor_cmd() -> List[str]:
    raw = os.environ.get("AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CMD_JSON")
    if not raw:
        return list(DEFAULT_SUPERVISOR_CMD)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BackendError(
            "invalid_backend_config",
            "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CMD_JSON must be valid JSON.",
            env_var="AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CMD_JSON",
        ) from exc

    if not isinstance(data, list) or not data or not all(isinstance(item, str) and item for item in data):
        raise BackendError(
            "invalid_backend_config",
            "AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CMD_JSON must be a non-empty JSON string array.",
            env_var="AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CMD_JSON",
        )
    return data


def _load_supervisor_config() -> Path:
    raw = os.environ.get("AUTOMATIONPLUS_DIAGNOSTICS_SUPERVISOR_CONFIG")
    config_path = Path(raw).expanduser().resolve() if raw else DEFAULT_SUPERVISOR_CONFIG.resolve()
    if not config_path.is_file():
        raise BackendError(
            "backend_config_not_found",
            f"Supervisor config file not found: {config_path}",
            config_path=str(config_path),
        )
    return config_path


def _build_supervisor_argv(
    command: str,
    issue_number: Optional[str],
    dry_run: bool,
) -> Tuple[Dict[str, object], List[str], Path]:
    command_spec = COMMAND_MAP.get(command)
    if command_spec is None:
        raise BackendError("invalid_backend_command", f"Unsupported diagnostics command: {command}")

    if command_spec["needs_issue_number"] and issue_number is None:
        raise BackendError(
            "invalid_backend_arguments",
            f"The {command} backend command requires one issue number.",
            command=command,
        )

    if not command_spec["needs_issue_number"] and issue_number is not None:
        raise BackendError(
            "invalid_backend_arguments",
            f"The {command} backend command does not accept an issue number.",
            command=command,
            issue_number=issue_number,
        )

    if dry_run and not command_spec["supports_dry_run"]:
        raise BackendError(
            "invalid_backend_arguments",
            f"The {command} backend command does not accept --dry-run.",
            command=command,
        )

    supervisor_command = str(command_spec["supervisor_command"])
    supervisor_cmd = _load_supervisor_cmd()
    config_path = _load_supervisor_config()
    argv = [*supervisor_cmd, supervisor_command]
    if issue_number is not None:
        argv.append(issue_number)
    if dry_run:
        argv.append("--dry-run")
    argv.extend(["--config", str(config_path)])
    return command_spec, argv, config_path


def _load_loop_status_supervisor_root() -> Path:
    raw = os.environ.get("AUTOMATIONPLUS_LOOP_STATUS_SUPERVISOR_ROOT")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_SUPERVISOR_ROOT.resolve()


def _load_loop_status_workspace_root() -> Path:
    raw = os.environ.get("AUTOMATIONPLUS_LOOP_STATUS_WORKSPACE_ROOT")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_LOOP_STATUS_WORKSPACE_ROOT.resolve()


def _load_loop_status_session_name() -> str:
    return os.environ.get("AUTOMATIONPLUS_LOOP_STATUS_SESSION_NAME", loop_status.DEFAULT_SESSION_NAME)


def _load_loop_status_capture_lines() -> int:
    raw = os.environ.get("AUTOMATIONPLUS_LOOP_STATUS_CAPTURE_LINES")
    if raw is None:
        return loop_status.DEFAULT_CAPTURE_LINES

    try:
        value = int(raw)
    except ValueError as exc:
        raise BackendError(
            "invalid_backend_config",
            "AUTOMATIONPLUS_LOOP_STATUS_CAPTURE_LINES must be an integer.",
            env_var="AUTOMATIONPLUS_LOOP_STATUS_CAPTURE_LINES",
        ) from exc
    return value


def _run_supervisor(argv: List[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
    except FileNotFoundError as exc:
        raise BackendError(
            "supervisor_cli_not_found",
            f"Supervisor CLI executable not found: {argv[0]}",
            executable=argv[0],
        ) from exc
    except OSError as exc:
        raise BackendError(
            "supervisor_launch_failed",
            f"Failed to launch supervisor CLI: {exc}",
            os_error=str(exc),
        ) from exc


def _raise_supervisor_failure(
    *,
    supervisor_command: str,
    argv: List[str],
    config_path: Path,
    result: subprocess.CompletedProcess,
) -> None:
    details: Dict[str, object] = {
        "supervisor_command": supervisor_command,
        "argv": argv,
        "config_path": str(config_path),
        "exit_code": result.returncode,
    }
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        details["stdout"] = stdout
    if stderr:
        details["stderr"] = stderr
    raise BackendError(
        "supervisor_failed",
        f"Supervisor command exited with status {result.returncode}.",
        **details,
    )


def _parse_json_stdout(
    *,
    stdout: str,
    supervisor_command: str,
    argv: List[str],
    config_path: Path,
) -> Dict[str, object]:
    stripped = stdout.strip()
    if not stripped:
        raise BackendError(
            "invalid_supervisor_output",
            "Supervisor produced empty stdout.",
            supervisor_command=supervisor_command,
            argv=argv,
            config_path=str(config_path),
        )

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise BackendError(
            "invalid_supervisor_output",
            "Supervisor stdout is not valid JSON.",
            supervisor_command=supervisor_command,
            argv=argv,
            config_path=str(config_path),
            stdout=stripped,
        ) from exc

    if not isinstance(parsed, dict):
        raise BackendError(
            "invalid_supervisor_output",
            "Supervisor stdout JSON must be an object.",
            supervisor_command=supervisor_command,
            argv=argv,
            config_path=str(config_path),
            stdout=stripped,
        )
    return parsed


def _parse_summary_stdout(
    *,
    stdout: str,
) -> str:
    stripped = stdout.strip()
    if not stripped:
        raise BackendError("invalid_supervisor_output", "Supervisor produced empty stdout.")
    return stripped


def _with_supervisor_context(
    exc: BackendError,
    *,
    supervisor_command: str,
    argv: List[str],
    config_path: Path,
) -> BackendError:
    details = dict(exc.details)
    details.setdefault("supervisor_command", supervisor_command)
    details.setdefault("argv", argv)
    details.setdefault("config_path", str(config_path))
    return BackendError(
        exc.code,
        exc.message,
        **details,
    )


def main() -> int:
    try:
        argc = len(sys.argv)
        if argc < 2 or argc > 4:
            raise BackendError(
                "invalid_backend_arguments",
                "usage: diagnostics_backend.py <command> [issue-number] [--dry-run]",
            )

        command = sys.argv[1]
        issue_number: Optional[str] = None
        dry_run = False
        for arg in sys.argv[2:]:
            if arg == "--dry-run":
                if dry_run:
                    raise BackendError(
                        "invalid_backend_arguments",
                        "Duplicate --dry-run flag.",
                        command=command,
                    )
                dry_run = True
                continue
            if issue_number is None:
                issue_number = arg
                continue
            raise BackendError(
                "invalid_backend_arguments",
                "Too many backend arguments.",
                command=command,
            )

        command_spec = COMMAND_MAP.get(command)
        if command_spec is None:
            raise BackendError("invalid_backend_command", f"Unsupported diagnostics command: {command}")

        output_mode = str(command_spec["output_mode"])
        if output_mode == "loop_status":
            if issue_number is not None:
                raise BackendError(
                    "invalid_backend_arguments",
                    f"The {command} backend command does not accept an issue number.",
                    command=command,
                    issue_number=issue_number,
                )
            if dry_run:
                raise BackendError(
                    "invalid_backend_arguments",
                    f"The {command} backend command does not accept --dry-run.",
                    command=command,
                )

            payload = loop_status.collect_loop_status(
                supervisor_root=_load_loop_status_supervisor_root(),
                workspace_root=_load_loop_status_workspace_root(),
                session_name=_load_loop_status_session_name(),
                capture_lines=_load_loop_status_capture_lines(),
            )
            payload.update(
                {
                    "backend": "automationplus",
                    "source_command": command,
                    "supervisor_command": None,
                    "issue_number": None,
                    "argv": [command],
                    "config_path": None,
                }
            )
            return _emit(payload, stream=sys.stdout)

        command_spec, argv, config_path = _build_supervisor_argv(command, issue_number, dry_run)
        supervisor_command = str(command_spec["supervisor_command"])
        result = _run_supervisor(argv)
        if result.returncode != 0:
            _raise_supervisor_failure(
                supervisor_command=supervisor_command,
                argv=argv,
                config_path=config_path,
                result=result,
            )

        if output_mode == "parsed_kv":
            try:
                parsed = _parse_supervisor_stdout(result.stdout)
            except BackendError as exc:
                raise _with_supervisor_context(
                    exc,
                    supervisor_command=supervisor_command,
                    argv=argv,
                    config_path=config_path,
                ) from exc
            payload = dict(parsed)
            payload.update(
                {
                "backend": "codex-supervisor",
                "source_command": command,
                "supervisor_command": supervisor_command,
                "issue_number": issue_number,
                "argv": argv,
                "config_path": str(config_path),
                }
            )
        elif output_mode == "json":
            try:
                parsed = _parse_json_stdout(
                    stdout=result.stdout,
                    supervisor_command=supervisor_command,
                    argv=argv,
                    config_path=config_path,
                )
            except BackendError as exc:
                raise _with_supervisor_context(
                    exc,
                    supervisor_command=supervisor_command,
                    argv=argv,
                    config_path=config_path,
                ) from exc
            payload = dict(parsed)
            payload.update(
                {
                "backend": "codex-supervisor",
                "source_command": command,
                "supervisor_command": supervisor_command,
                "issue_number": issue_number,
                "argv": argv,
                "config_path": str(config_path),
                }
            )
        elif output_mode == "summary_text":
            try:
                summary = _parse_summary_stdout(stdout=result.stdout)
            except BackendError as exc:
                raise _with_supervisor_context(
                    exc,
                    supervisor_command=supervisor_command,
                    argv=argv,
                    config_path=config_path,
                ) from exc
            payload = {
                "backend": "codex-supervisor",
                "source_command": command,
                "supervisor_command": supervisor_command,
                "issue_number": issue_number,
                "argv": argv,
                "config_path": str(config_path),
                "dry_run": dry_run,
                "summary": summary,
            }
        else:
            raise BackendError(
                "invalid_backend_config",
                f"Unsupported output mode configured for {command}: {output_mode}",
                command=command,
            )
        return _emit(payload, stream=sys.stdout)
    except BackendError as exc:
        return _emit(
            {
                "code": exc.code,
                "message": exc.message,
                **exc.details,
            },
            stream=sys.stderr,
        ) or 1


if __name__ == "__main__":
    raise SystemExit(main())

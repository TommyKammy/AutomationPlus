#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


COMMAND_MAP = {
    "status-json": {"supervisor_command": "status", "needs_issue_number": False},
    "doctor-json": {"supervisor_command": "doctor", "needs_issue_number": False},
    "explain-json": {"supervisor_command": "explain", "needs_issue_number": True},
    "issue-lint-json": {"supervisor_command": "issue-lint", "needs_issue_number": True},
}
KEY_PATTERN = re.compile(r"(?<!\S)([A-Za-z0-9_:-]+)=")
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUPERVISOR_ROOT = REPO_ROOT.parent.parent / "AutomationPlus-codex-supervisor"
DEFAULT_SUPERVISOR_CMD = ["node", str(DEFAULT_SUPERVISOR_ROOT / "dist" / "index.js")]
DEFAULT_SUPERVISOR_CONFIG = DEFAULT_SUPERVISOR_ROOT / "supervisor.config.coderabbit.json"


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


def _build_supervisor_argv(command: str, issue_number: Optional[str]) -> Tuple[str, List[str], Path]:
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

    supervisor_command = str(command_spec["supervisor_command"])
    supervisor_cmd = _load_supervisor_cmd()
    config_path = _load_supervisor_config()
    argv = [*supervisor_cmd, supervisor_command]
    if issue_number is not None:
        argv.append(issue_number)
    argv.extend(["--config", str(config_path)])
    return supervisor_command, argv, config_path


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


def main() -> int:
    try:
        argc = len(sys.argv)
        if argc not in (2, 3):
            raise BackendError(
                "invalid_backend_arguments",
                "usage: diagnostics_backend.py <command> [issue-number]",
            )

        command = sys.argv[1]
        issue_number = sys.argv[2] if argc == 3 else None
        supervisor_command, argv, config_path = _build_supervisor_argv(command, issue_number)
        result = _run_supervisor(argv)
        if result.returncode != 0:
            _raise_supervisor_failure(
                supervisor_command=supervisor_command,
                argv=argv,
                config_path=config_path,
                result=result,
            )

        try:
            parsed = _parse_supervisor_stdout(result.stdout)
        except BackendError as exc:
            raise BackendError(
                exc.code,
                exc.message,
                supervisor_command=supervisor_command,
                argv=argv,
                config_path=str(config_path),
                **exc.details,
            ) from exc
        payload = {
            "backend": "codex-supervisor",
            "source_command": command,
            "supervisor_command": supervisor_command,
            "issue_number": issue_number,
            "argv": argv,
            "config_path": str(config_path),
            **parsed,
        }
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

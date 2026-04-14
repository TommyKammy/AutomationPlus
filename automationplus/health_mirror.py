import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_CAPTURE_LINES = 20
DEFAULT_SESSION_NAME = "automationplus-loop"
DEFAULT_ARTIFACT_RELATIVE_PATH = Path(".codex-supervisor") / "health" / "loop-health.json"
DEFAULT_TMUX_TIMEOUT_SECONDS = 5.0
FAILURE_POLICY_SCHEMA_VERSION = 1
FAILURE_REGISTRY_SCHEMA_VERSION = 1
RECOVERABLE_FAILURE_TOKENS = (
    "timed out",
    "timeout",
    "econnreset",
    "eai_again",
    "temporarily unavailable",
    "connection reset",
    "rate limit",
    "429",
    "503",
    "connection refused",
)


class HealthMirrorError(Exception):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json_file(path: Path) -> Optional[Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _empty_failure_registry() -> dict:
    return {
        "schemaVersion": FAILURE_REGISTRY_SCHEMA_VERSION,
        "entries": {},
    }


def _read_failure_registry(path: Path) -> dict:
    payload = _read_json_file(path)
    if not isinstance(payload, dict):
        return _empty_failure_registry()

    registry = payload.get("failureRegistry")
    if not isinstance(registry, dict):
        return _empty_failure_registry()
    if registry.get("schemaVersion") != FAILURE_REGISTRY_SCHEMA_VERSION:
        return _empty_failure_registry()

    entries = registry.get("entries")
    if not isinstance(entries, dict):
        return _empty_failure_registry()

    return {
        "schemaVersion": FAILURE_REGISTRY_SCHEMA_VERSION,
        "entries": {
            key: value
            for key, value in entries.items()
            if isinstance(key, str) and key and isinstance(value, dict)
        },
    }


def _normalize_failure_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}(?:\.\d+)?z\b", " ", normalized)
    normalized = re.sub(r"\bpid=\d+\b", "pid=<pid>", normalized)
    normalized = re.sub(r"\bissue=#?\d+\b", "issue=#<id>", normalized)
    normalized = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", normalized)
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _last_tail_line(loop_runtime: dict) -> Optional[str]:
    tail = loop_runtime.get("tail")
    if not isinstance(tail, list):
        return None
    for raw_line in reversed(tail):
        if isinstance(raw_line, str) and raw_line.strip():
            return raw_line.strip()
    return None


def _failure_event(loop_runtime: dict, drift: dict) -> Optional[dict]:
    runtime_state = loop_runtime.get("state")
    pane_dead = loop_runtime.get("paneDead") is True
    mismatch_keys = [
        key
        for key in ("issueNumberMatches", "workspaceMatches", "stateMatches")
        if drift.get(key) is False
    ]

    if runtime_state == "off":
        return None

    if runtime_state != "running":
        summary = f"Loop runtime observation is unreliable: state={runtime_state or 'unknown'}"
        normalized = _normalize_failure_text(summary)
        return {
            "reason": "runtime_unobservable",
            "summary": summary,
            "normalized": normalized,
            "signatureClass": "unknown",
        }

    if pane_dead:
        last_line = _last_tail_line(loop_runtime)
        summary = last_line or "Loop pane terminated without captured tail output"
        normalized = _normalize_failure_text(summary)
        signature_class = (
            "transient"
            if any(token in normalized for token in RECOVERABLE_FAILURE_TOKENS)
            else "unknown"
        )
        return {
            "reason": "pane_dead",
            "summary": summary,
            "normalized": normalized,
            "signatureClass": signature_class,
        }

    if mismatch_keys:
        summary = "Loop runtime drift mismatch: " + ", ".join(mismatch_keys)
        normalized = _normalize_failure_text(summary)
        return {
            "reason": "drift_mismatch",
            "summary": summary,
            "normalized": normalized,
            "signatureClass": "unknown",
        }

    return None


def _signature_id(reason: str, normalized: str) -> str:
    digest = hashlib.sha256(f"{reason}|{normalized}".encode("utf-8")).hexdigest()
    return f"sig-{digest[:16]}"


def _merge_failure_registry(
    registry: dict,
    snapshot: dict,
) -> dict:
    event = _failure_event(snapshot.get("loopRuntime", {}), snapshot.get("drift", {}))
    if event is None:
        return {
            "schemaVersion": FAILURE_REGISTRY_SCHEMA_VERSION,
            "entries": dict(registry.get("entries", {})),
        }

    captured_at = snapshot.get("capturedAt")
    next_entries = dict(registry.get("entries", {}))
    signature_id = _signature_id(event["reason"], event["normalized"])
    previous = next_entries.get(signature_id)

    if isinstance(previous, dict):
        first_seen_at = previous.get("firstSeenAt") or captured_at
        seen_count = int(previous.get("seenCount", 0)) + 1
    else:
        first_seen_at = captured_at
        seen_count = 1

    next_entries[signature_id] = {
        "id": signature_id,
        "reason": event["reason"],
        "signatureClass": event["signatureClass"],
        "summary": event["summary"],
        "normalizedSummary": event["normalized"],
        "firstSeenAt": first_seen_at,
        "lastSeenAt": captured_at,
        "seenCount": seen_count,
    }
    return {
        "schemaVersion": FAILURE_REGISTRY_SCHEMA_VERSION,
        "entries": next_entries,
    }


def _failure_policy(snapshot: dict, registry: dict) -> dict:
    loop_runtime = snapshot.get("loopRuntime", {})
    drift = snapshot.get("drift", {})
    event = _failure_event(loop_runtime, drift)
    runtime_state = loop_runtime.get("state")

    if event is None:
        return {
            "schemaVersion": FAILURE_POLICY_SCHEMA_VERSION,
            "degradedState": "healthy",
            "restartEligible": False,
            "operatorHold": False,
            "summary": "No loop failure classification is active.",
            "signature": None,
        }

    signature_id = _signature_id(event["reason"], event["normalized"])
    entry = registry.get("entries", {}).get(signature_id, {})
    seen_count = entry.get("seenCount", 1) if isinstance(entry, dict) else 1

    if event["signatureClass"] == "transient":
        degraded_state = "transient-failure" if seen_count == 1 else "repeated-failure"
        restart_eligible = seen_count == 1 and runtime_state == "running"
        operator_hold = seen_count > 1
    else:
        degraded_state = "unsafe-unknown"
        restart_eligible = False
        operator_hold = True

    return {
        "schemaVersion": FAILURE_POLICY_SCHEMA_VERSION,
        "degradedState": degraded_state,
        "restartEligible": restart_eligible,
        "operatorHold": operator_hold,
        "summary": event["summary"],
        "signature": {
            "id": signature_id,
            "reason": event["reason"],
            "class": event["signatureClass"],
            "count": seen_count,
            "firstSeenAt": entry.get("firstSeenAt") if isinstance(entry, dict) else snapshot.get("capturedAt"),
            "lastSeenAt": entry.get("lastSeenAt") if isinstance(entry, dict) else snapshot.get("capturedAt"),
            "normalizedSummary": event["normalized"],
        },
    }


def _tmux_capture_lines(stdout: str, limit: int) -> List[str]:
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    if limit <= 0:
        return []
    return lines[-limit:]


def _run_tmux(
    *args: str,
    timeout: float = DEFAULT_TMUX_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["tmux", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise HealthMirrorError("tmux is not available on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        command = " ".join(["tmux", *args])
        raise HealthMirrorError(
            f"tmux command timed out after {timeout:g}s: {command}"
        ) from exc
    except OSError as exc:
        raise HealthMirrorError(f"Failed to invoke tmux: {exc}") from exc


def _read_tmux_runtime(session_name: str, capture_lines: int) -> Dict[str, Any]:
    has_session = _run_tmux("has-session", "-t", session_name)
    if has_session.returncode != 0:
        return {
            "state": "off",
            "hostMode": "tmux",
            "sessionName": session_name,
            "windowName": None,
            "paneId": None,
            "panePid": None,
            "paneCurrentCommand": None,
            "paneCurrentPath": None,
            "paneDead": None,
            "tail": [],
        }

    format_string = (
        "#{window_name}\t#{pane_id}\t#{pane_pid}\t#{pane_current_command}\t"
        "#{pane_current_path}\t#{pane_dead}"
    )
    metadata = _run_tmux("display-message", "-p", "-t", f"{session_name}:0.0", format_string)
    if metadata.returncode != 0:
        raise HealthMirrorError(metadata.stderr.strip() or "Failed to read tmux pane metadata")

    fields = metadata.stdout.rstrip("\n").split("\t")
    if len(fields) != 6:
        raise HealthMirrorError("Unexpected tmux pane metadata format")

    tail_result = _run_tmux("capture-pane", "-p", "-t", f"{session_name}:0.0", "-S", f"-{max(capture_lines, 1)}")
    if tail_result.returncode != 0:
        raise HealthMirrorError(tail_result.stderr.strip() or "Failed to capture tmux pane output")

    window_name, pane_id, pane_pid, pane_current_command, pane_current_path, pane_dead = fields
    return {
        "state": "running",
        "hostMode": "tmux",
        "sessionName": session_name,
        "windowName": window_name or None,
        "paneId": pane_id or None,
        "panePid": int(pane_pid) if pane_pid else None,
        "paneCurrentCommand": pane_current_command or None,
        "paneCurrentPath": pane_current_path or None,
        "paneDead": pane_dead == "1",
        "tail": _tmux_capture_lines(tail_result.stdout, capture_lines),
    }


def _supervisor_record(local_state: Optional[dict], issue_number: Optional[int]) -> Optional[dict]:
    if not isinstance(local_state, dict) or issue_number is None:
        return None
    issues = local_state.get("issues")
    if not isinstance(issues, dict):
        return None
    record = issues.get(str(issue_number))
    return record if isinstance(record, dict) else None


def _drift_flags(
    loop_runtime: dict,
    supervisor_root: Path,
    local_state: Optional[dict],
    turn_in_progress: Optional[dict],
    decision_cycle: Optional[dict],
) -> dict:
    active_issue_number = None
    if isinstance(local_state, dict) and isinstance(local_state.get("activeIssueNumber"), int):
        active_issue_number = local_state["activeIssueNumber"]

    record = _supervisor_record(local_state, active_issue_number)
    turn_issue_number = turn_in_progress.get("issueNumber") if isinstance(turn_in_progress, dict) else None
    decision_issue_number = (
        decision_cycle.get("issue", {}).get("number")
        if isinstance(decision_cycle, dict)
        and isinstance(decision_cycle.get("issue"), dict)
        else None
    )
    decision_state = (
        decision_cycle.get("decision", {}).get("nextState")
        if isinstance(decision_cycle, dict)
        and isinstance(decision_cycle.get("decision"), dict)
        else None
    )

    issue_candidates = [value for value in (active_issue_number, turn_issue_number, decision_issue_number) if value is not None]
    issue_number_matches = len(set(issue_candidates)) <= 1 if issue_candidates else None

    expected_workspace = str(supervisor_root.resolve())
    actual_workspace = loop_runtime.get("paneCurrentPath")
    workspace_matches = (
        Path(actual_workspace).resolve() == Path(expected_workspace).resolve()
        if actual_workspace and expected_workspace
        else None
    )

    record_state = record.get("state") if isinstance(record, dict) else None
    turn_state = turn_in_progress.get("state") if isinstance(turn_in_progress, dict) else None
    state_candidates = [value for value in (record_state, turn_state, decision_state) if value]
    state_matches = len(set(state_candidates)) <= 1 if state_candidates else None

    return {
        "issueNumberMatches": issue_number_matches,
        "workspaceMatches": workspace_matches,
        "stateMatches": state_matches,
    }


def collect_loop_health_snapshot(
    supervisor_root: Path,
    session_name: str = DEFAULT_SESSION_NAME,
    capture_lines: int = DEFAULT_CAPTURE_LINES,
    captured_at: Optional[str] = None,
) -> dict:
    supervisor_root = Path(supervisor_root).resolve()
    local_state = _read_json_file(supervisor_root / ".local" / "state.json")
    turn_in_progress = _read_json_file(supervisor_root / ".codex-supervisor" / "turn-in-progress.json")
    decision_cycle = _read_json_file(
        supervisor_root / ".codex-supervisor" / "replay" / "decision-cycle-snapshot.json"
    )
    loop_runtime = _read_tmux_runtime(session_name, capture_lines)

    snapshot = {
        "schemaVersion": 1,
        "capturedAt": captured_at or _utc_now_iso(),
        "loopRuntime": loop_runtime,
        "supervisor": {
            "root": str(supervisor_root),
            "activeIssueNumber": local_state.get("activeIssueNumber") if isinstance(local_state, dict) else None,
            "activeIssue": _supervisor_record(
                local_state,
                local_state.get("activeIssueNumber") if isinstance(local_state, dict) else None,
            ),
            "turnInProgress": turn_in_progress,
            "decisionCycle": decision_cycle,
        },
    }
    snapshot["drift"] = _drift_flags(
        loop_runtime,
        supervisor_root,
        local_state,
        turn_in_progress,
        decision_cycle,
    )
    snapshot["failureRegistry"] = _merge_failure_registry(_empty_failure_registry(), snapshot)
    snapshot["failurePolicy"] = _failure_policy(snapshot, snapshot["failureRegistry"])
    return snapshot


def write_loop_health_snapshot(
    output_path: Path,
    supervisor_root: Path,
    session_name: str = DEFAULT_SESSION_NAME,
    capture_lines: int = DEFAULT_CAPTURE_LINES,
    captured_at: Optional[str] = None,
) -> dict:
    snapshot = collect_loop_health_snapshot(
        supervisor_root=supervisor_root,
        session_name=session_name,
        capture_lines=capture_lines,
        captured_at=captured_at,
    )
    snapshot["failureRegistry"] = _merge_failure_registry(
        _read_failure_registry(Path(output_path)),
        snapshot,
    )
    snapshot["failurePolicy"] = _failure_policy(snapshot, snapshot["failureRegistry"])

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(output_path.parent),
        delete=False,
    ) as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)

    temp_path.replace(output_path)
    return snapshot


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loop-health-mirror")
    parser.add_argument("--supervisor-root", required=True)
    parser.add_argument("--session-name", default=DEFAULT_SESSION_NAME)
    parser.add_argument("--capture-lines", type=int, default=DEFAULT_CAPTURE_LINES)
    parser.add_argument("--output")
    parser.add_argument("--stdout", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    supervisor_root = Path(args.supervisor_root).expanduser().resolve()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else (Path.cwd() / DEFAULT_ARTIFACT_RELATIVE_PATH).resolve()
    )

    snapshot = write_loop_health_snapshot(
        output_path=output_path,
        supervisor_root=supervisor_root,
        session_name=args.session_name,
        capture_lines=args.capture_lines,
    )

    if args.stdout:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_CAPTURE_LINES = 20
DEFAULT_SESSION_NAME = "automationplus-loop"
DEFAULT_ARTIFACT_RELATIVE_PATH = Path(".codex-supervisor") / "health" / "loop-health.json"


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
    return json.loads(raw)


def _tmux_capture_lines(stdout: str, limit: int) -> List[str]:
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    if limit <= 0:
        return []
    return lines[-limit:]


def _run_tmux(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["tmux", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise HealthMirrorError("tmux is not available on PATH") from exc
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

import json
from pathlib import Path
from typing import Any, Dict, Optional

from automationplus import health_mirror


DEFAULT_SESSION_NAME = health_mirror.DEFAULT_SESSION_NAME
DEFAULT_CAPTURE_LINES = health_mirror.DEFAULT_CAPTURE_LINES
DEFAULT_HEALTH_SNAPSHOT_RELATIVE_PATH = health_mirror.DEFAULT_ARTIFACT_RELATIVE_PATH
DEFAULT_LAUNCHER_STATE_RELATIVE_PATH = (
    Path(".codex-supervisor") / "launcher" / "loop-service.json"
)


def _read_optional_json(path: Path) -> Optional[dict]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    if not raw.strip():
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _runtime_health(loop_runtime: dict, drift: dict) -> str:
    runtime_state = loop_runtime.get("state")
    if runtime_state == "off":
        return "off"

    if runtime_state != "running":
        return "unknown"

    if loop_runtime.get("paneDead"):
        return "degraded"

    mismatch = False
    for key in ("issueNumberMatches", "workspaceMatches", "stateMatches"):
        value = drift.get(key)
        if value is False:
            mismatch = True
            break

    return "degraded" if mismatch else "healthy"


def _top_level_status(runtime_health: str) -> str:
    if runtime_health in {"healthy", "degraded", "off"}:
        return runtime_health
    return "unknown"


def _service_state(runtime: dict, launcher_state: Optional[dict]) -> str:
    if runtime.get("state") == "running":
        return "running"
    if runtime.get("state") == "off":
        return "stopped"

    if isinstance(launcher_state, dict):
        state = launcher_state.get("state")
        if isinstance(state, str) and state:
            return state

    return "unknown"


def _launcher_contract(
    supervisor_root: Path,
    workspace_root: Path,
    health_snapshot_path: Path,
    launcher_state_path: Path,
) -> dict:
    return {
        "version": 1,
        "readOnly": True,
        "serviceName": "automationplus-loop",
        "serviceController": "external-launcher",
        "serviceSignals": {
            "sessionOwner": "launcher",
            "pidOwner": "launcher",
            "supervisorRuntimeObserver": "health-mirror",
        },
        "artifacts": {
            "workspaceRoot": str(workspace_root),
            "supervisorRoot": str(supervisor_root),
            "healthSnapshot": str(health_snapshot_path),
            "launcherState": str(launcher_state_path),
        },
        "trustSignals": [
            "tmux session metadata for the configured session name",
            "loop health snapshot written by automationplus.health_mirror",
            "launcher loop-service.json metadata when present",
            "supervisor local state, turn-in-progress, and decision-cycle snapshots",
        ],
    }


def collect_loop_status(
    *,
    supervisor_root: Path,
    workspace_root: Path,
    session_name: str = DEFAULT_SESSION_NAME,
    capture_lines: int = DEFAULT_CAPTURE_LINES,
) -> Dict[str, Any]:
    supervisor_root = Path(supervisor_root).expanduser().resolve()
    workspace_root = Path(workspace_root).expanduser().resolve()
    health_snapshot_path = (workspace_root / DEFAULT_HEALTH_SNAPSHOT_RELATIVE_PATH).resolve()
    launcher_state_path = (workspace_root / DEFAULT_LAUNCHER_STATE_RELATIVE_PATH).resolve()
    launcher_state = _read_optional_json(launcher_state_path)

    try:
        snapshot = health_mirror.collect_loop_health_snapshot(
            supervisor_root=supervisor_root,
            session_name=session_name,
            capture_lines=capture_lines,
        )
        snapshot = health_mirror.apply_persisted_failure_tracking(
            snapshot,
            health_snapshot_path,
        )
        observation_error = None
    except health_mirror.HealthMirrorError as exc:
        snapshot = {
            "schemaVersion": 1,
            "capturedAt": health_mirror._utc_now_iso(),
            "loopRuntime": {
                "state": "unknown",
                "hostMode": "tmux",
                "sessionName": session_name,
                "windowName": None,
                "paneId": None,
                "panePid": None,
                "paneCurrentCommand": None,
                "paneCurrentPath": None,
                "paneDead": None,
                "tail": [],
            },
            "supervisor": {
                "root": str(supervisor_root),
                "activeIssueNumber": None,
                "activeIssue": None,
                "turnInProgress": None,
                "decisionCycle": None,
            },
            "drift": {
                "issueNumberMatches": None,
                "workspaceMatches": None,
                "stateMatches": None,
            },
        }
        snapshot = health_mirror.apply_persisted_failure_tracking(
            snapshot,
            health_snapshot_path,
        )
        observation_error = {"code": "health_mirror_error", "message": str(exc)}

    loop_runtime = snapshot.get("loopRuntime", {})
    drift = snapshot.get("drift", {})
    runtime_health = _runtime_health(loop_runtime, drift)
    top_level_status = _top_level_status(runtime_health)

    payload: Dict[str, Any] = {
        "schemaVersion": 1,
        "capturedAt": snapshot.get("capturedAt"),
        "status": top_level_status,
        "failurePolicy": snapshot.get("failurePolicy"),
        "runtime": {
            "state": loop_runtime.get("state", "unknown"),
            "health": runtime_health,
            "hostMode": loop_runtime.get("hostMode"),
            "sessionName": loop_runtime.get("sessionName"),
            "windowName": loop_runtime.get("windowName"),
            "paneId": loop_runtime.get("paneId"),
            "pid": loop_runtime.get("panePid"),
            "currentCommand": loop_runtime.get("paneCurrentCommand"),
            "currentPath": loop_runtime.get("paneCurrentPath"),
            "paneDead": loop_runtime.get("paneDead"),
            "tail": loop_runtime.get("tail", []),
        },
        "supervisor": snapshot.get("supervisor"),
        "launcher": {
            "contract": _launcher_contract(
                supervisor_root=supervisor_root,
                workspace_root=workspace_root,
                health_snapshot_path=health_snapshot_path,
                launcher_state_path=launcher_state_path,
            ),
            "discovery": {
                "workspaceRoot": str(workspace_root),
                "supervisorRoot": str(supervisor_root),
                "sessionName": session_name,
                "healthSnapshotPath": str(health_snapshot_path),
                "launcherStatePath": str(launcher_state_path),
            },
            "service": {
                "state": _service_state(loop_runtime, launcher_state),
                "hostMode": loop_runtime.get("hostMode"),
                "sessionName": loop_runtime.get("sessionName", session_name),
                "pid": (
                    loop_runtime.get("panePid")
                    if loop_runtime.get("state") == "running"
                    else launcher_state.get("pid") if isinstance(launcher_state, dict) else None
                ),
                "startedAt": launcher_state.get("startedAt") if isinstance(launcher_state, dict) else None,
                "metadata": launcher_state,
            },
        },
        "drift": drift,
    }
    if observation_error is not None:
        payload["observationError"] = observation_error
    return payload

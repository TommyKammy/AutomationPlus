import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


RESTART_DECISION_SCHEMA_VERSION = 1
RESTART_BUDGET_SCHEMA_VERSION = 1
RESTART_CONTROL_BLOCK_SCHEMA_VERSION = 1
DEFAULT_RESTART_DECISION_RELATIVE_PATH = (
    Path(".codex-supervisor") / "health" / "restart-decision.json"
)
DEFAULT_RESTART_BUDGET_RELATIVE_PATH = (
    Path(".codex-supervisor") / "health" / "restart-budget.json"
)
DEFAULT_RESTART_CONTROL_BLOCK_RELATIVE_PATH = (
    Path(".codex-supervisor") / "health" / "restart-control-block.json"
)
DEFAULT_MAX_RESTARTS = 2
DEFAULT_WINDOW_SECONDS = 900


def _parse_iso8601(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: dict) -> None:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _remove_file_if_present(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return


def _empty_budget_state() -> dict:
    return {
        "schemaVersion": RESTART_BUDGET_SCHEMA_VERSION,
        "history": [],
    }


def _read_budget_state(path: Path) -> dict:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_budget_state()

    if not raw.strip():
        return _empty_budget_state()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _empty_budget_state()

    if not isinstance(payload, dict):
        return _empty_budget_state()
    if payload.get("schemaVersion") != RESTART_BUDGET_SCHEMA_VERSION:
        return _empty_budget_state()

    history = payload.get("history")
    if not isinstance(history, list):
        return _empty_budget_state()

    sanitized_history: List[dict] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        evaluated_at = item.get("evaluatedAt")
        allowed = item.get("allowed")
        if not isinstance(evaluated_at, str) or not isinstance(allowed, bool):
            continue
        try:
            _parse_iso8601(evaluated_at)
        except ValueError:
            continue
        sanitized_history.append(
            {
                "evaluatedAt": evaluated_at,
                "allowed": allowed,
                "reasonCode": item.get("reasonCode") if isinstance(item.get("reasonCode"), str) else None,
                "signatureId": item.get("signatureId") if isinstance(item.get("signatureId"), str) else None,
            }
        )

    return {
        "schemaVersion": RESTART_BUDGET_SCHEMA_VERSION,
        "history": sanitized_history,
    }


def _prune_history(history: List[dict], *, evaluated_at: str, window_seconds: int) -> List[dict]:
    evaluation_time = _parse_iso8601(evaluated_at)
    cutoff = evaluation_time - timedelta(seconds=max(window_seconds, 0))
    kept: List[dict] = []
    for item in history:
        try:
            item_time = _parse_iso8601(item["evaluatedAt"])
        except (KeyError, ValueError):
            continue
        if item_time >= cutoff:
            kept.append(item)
    return kept


def _allowed_restart_count(history: List[dict]) -> int:
    return sum(1 for item in history if item.get("allowed") is True)


def _decision_outcome(loop_status_payload: dict) -> dict:
    failure_policy = loop_status_payload.get("failurePolicy")
    if not isinstance(failure_policy, dict):
        return {
            "reasonCode": "missing_failure_policy",
            "action": "hold",
            "route": "hold",
        }

    degraded_state = failure_policy.get("degradedState")

    if degraded_state == "repeated-failure":
        return {
            "reasonCode": "repeated_failure_auto_stop",
            "action": "stop",
            "route": "quarantine",
        }

    if failure_policy.get("operatorHold") is True:
        return {
            "reasonCode": "unsafe_failure_policy",
            "action": "hold",
            "route": "hold",
        }

    if degraded_state != "transient-failure":
        return {
            "reasonCode": "restart_not_eligible",
            "action": "stop",
            "route": None,
        }

    if failure_policy.get("restartEligible") is not True:
        return {
            "reasonCode": "restart_not_eligible",
            "action": "stop",
            "route": None,
        }

    return {
        "reasonCode": "transient_restart_allowed",
        "action": "restart",
        "route": None,
    }


def _blocking_details(
    *,
    reason_code: str,
    action: str,
    route: Optional[str],
    summary: Optional[str],
    signature: Optional[dict],
    used_before_decision: int,
    max_restarts: int,
    window_seconds: int,
) -> Optional[dict]:
    if action == "restart":
        return None

    signature_count = signature.get("count") if isinstance(signature, dict) else None
    signature_id = signature.get("id") if isinstance(signature, dict) else None
    normalized_summary = (
        signature.get("normalizedSummary") if isinstance(signature, dict) else None
    )

    if reason_code == "repeated_failure_auto_stop":
        blocking_summary = (
            "Automation stopped restarting after the same repeated failure "
            f"signature was observed {signature_count or 0} times."
        )
        resume_hint = (
            "Review the repeated failure signature and clear the quarantine "
            "intentionally before restarting the loop."
        )
    elif reason_code == "restart_budget_exhausted":
        blocking_summary = (
            "Automation stopped unattended restarts after exhausting the "
            f"restart budget of {max_restarts} attempts within {window_seconds} seconds."
        )
        resume_hint = (
            "Review the recent restart history before attempting another restart."
        )
    elif reason_code == "unsafe_failure_policy":
        blocking_summary = (
            "Automation placed the loop on operator hold because the observed "
            "failure state is unsafe or unclassified."
        )
        resume_hint = (
            "Inspect the loop health snapshot and resolve the unsafe condition "
            "before resuming service."
        )
    elif reason_code == "restart_not_eligible":
        blocking_summary = (
            "Automation refused an unattended restart because the current "
            "failure state is not eligible for automatic recovery."
        )
        resume_hint = (
            "Review the current failure classification and restart eligibility "
            "before resuming service."
        )
    else:
        blocking_summary = (
            "Automation refused to continue unattended recovery and is waiting "
            "for explicit operator intervention."
        )
        resume_hint = "Review the blocking details before resuming service."

    return {
        "route": route,
        "action": action,
        "reasonCode": reason_code,
        "summary": blocking_summary,
        "failureSummary": summary,
        "signatureId": signature_id,
        "signatureCount": signature_count,
        "normalizedSummary": normalized_summary,
        "usedRestartsBeforeDecision": used_before_decision,
        "maxRestarts": max_restarts,
        "windowSeconds": window_seconds,
        "requiresOperatorAction": True,
        "resumeHint": resume_hint,
    }


def _restart_control_block_path(output_path: Path) -> Path:
    output_path = Path(output_path).resolve()
    return output_path.with_name(DEFAULT_RESTART_CONTROL_BLOCK_RELATIVE_PATH.name)


def build_restart_decision_artifact(
    *,
    loop_status_payload: dict,
    budget_state: Optional[dict] = None,
    evaluated_at: Optional[str] = None,
    max_restarts: int = DEFAULT_MAX_RESTARTS,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> tuple[dict, dict]:
    if evaluated_at is None:
        evaluated_at = _utc_now_iso()

    max_restarts = max(int(max_restarts), 0)
    window_seconds = max(int(window_seconds), 0)
    budget_state = budget_state if isinstance(budget_state, dict) else _empty_budget_state()
    history = budget_state.get("history", [])
    if not isinstance(history, list):
        history = []

    history = _prune_history(history, evaluated_at=evaluated_at, window_seconds=window_seconds)
    used_before_decision = _allowed_restart_count(history)
    decision_outcome = _decision_outcome(loop_status_payload)
    reason_code = decision_outcome["reasonCode"]
    action = decision_outcome["action"]
    route = decision_outcome["route"]

    if reason_code == "transient_restart_allowed" and used_before_decision >= max_restarts:
        reason_code = "restart_budget_exhausted"
        action = "stop"
        route = "quarantine"

    allowed = reason_code == "transient_restart_allowed"
    failure_policy = loop_status_payload.get("failurePolicy")
    signature = failure_policy.get("signature") if isinstance(failure_policy, dict) else None
    signature_id = signature.get("id") if isinstance(signature, dict) else None
    degraded_state = failure_policy.get("degradedState") if isinstance(failure_policy, dict) else None
    summary = failure_policy.get("summary") if isinstance(failure_policy, dict) else None
    blocking = _blocking_details(
        reason_code=reason_code,
        action=action,
        route=route,
        summary=summary,
        signature=signature if isinstance(signature, dict) else None,
        used_before_decision=used_before_decision,
        max_restarts=max_restarts,
        window_seconds=window_seconds,
    )

    decision_entry = {
        "evaluatedAt": evaluated_at,
        "allowed": allowed,
        "reasonCode": reason_code,
        "signatureId": signature_id,
    }
    next_history = list(history)
    if allowed:
        next_history.append(decision_entry)

    used_after_decision = _allowed_restart_count(next_history)
    remaining = max(max_restarts - used_after_decision, 0)
    budget = {
        "maxRestarts": max_restarts,
        "windowSeconds": window_seconds,
        "used": used_after_decision,
        "remaining": remaining,
    }

    artifact = {
        "schemaVersion": RESTART_DECISION_SCHEMA_VERSION,
        "artifactType": "restart_decision",
        "evaluatedAt": evaluated_at,
        "decision": {
            "allowed": allowed,
            "reasonCode": reason_code,
            "action": action,
        },
        "failurePolicy": {
            "degradedState": degraded_state,
            "restartEligible": (
                failure_policy.get("restartEligible")
                if isinstance(failure_policy, dict)
                else None
            ),
            "operatorHold": (
                failure_policy.get("operatorHold")
                if isinstance(failure_policy, dict)
                else None
            ),
            "summary": summary,
            "signature": signature,
        },
        "budget": budget,
        "blocking": blocking,
        "sourceLoopStatus": {
            "capturedAt": loop_status_payload.get("capturedAt"),
            "status": loop_status_payload.get("status"),
            "runtimeState": (
                loop_status_payload.get("runtime", {}).get("state")
                if isinstance(loop_status_payload.get("runtime"), dict)
                else None
            ),
        },
    }
    next_budget_state = {
        "schemaVersion": RESTART_BUDGET_SCHEMA_VERSION,
        "history": next_history,
    }
    return artifact, next_budget_state


def write_restart_decision_artifact(
    *,
    output_path: Path,
    budget_path: Path,
    loop_status_payload: dict,
    evaluated_at: Optional[str] = None,
    max_restarts: int = DEFAULT_MAX_RESTARTS,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> dict:
    budget_path = Path(budget_path).resolve()
    output_path = Path(output_path).resolve()
    budget_state = _read_budget_state(budget_path)
    artifact, next_budget_state = build_restart_decision_artifact(
        loop_status_payload=loop_status_payload,
        budget_state=budget_state,
        evaluated_at=evaluated_at,
        max_restarts=max_restarts,
        window_seconds=window_seconds,
    )
    _write_json_atomic(budget_path, next_budget_state)
    block_artifact_path = _restart_control_block_path(output_path)
    blocking = artifact.get("blocking")
    routed_blocking = (
        blocking if isinstance(blocking, dict) and blocking.get("route") is not None else None
    )
    if isinstance(routed_blocking, dict):
        artifact["blockArtifactPath"] = str(block_artifact_path)
        block_artifact = {
            "schemaVersion": RESTART_CONTROL_BLOCK_SCHEMA_VERSION,
            "artifactType": "restart_control_block",
            "evaluatedAt": artifact.get("evaluatedAt"),
            "route": routed_blocking.get("route"),
            "decision": artifact.get("decision"),
            "blocking": routed_blocking,
            "failurePolicy": artifact.get("failurePolicy"),
            "sourceDecisionArtifactPath": str(output_path),
        }
        _write_json_atomic(block_artifact_path, block_artifact)
    _write_json_atomic(output_path, artifact)
    if routed_blocking is None:
        _remove_file_if_present(block_artifact_path)
    return artifact

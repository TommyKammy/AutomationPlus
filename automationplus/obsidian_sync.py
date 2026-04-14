import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Optional


GENERATED_NOTE_ALLOWED_ROOTS = (
    Path(".codex-supervisor") / "generated" / "obsidian" / "notes",
    Path("obsidian") / "generated",
)
DEFAULT_QUARANTINE_RELATIVE_PATH = (
    Path(".codex-supervisor") / "generated" / "obsidian" / "quarantine.json"
)


def _write_text_atomic(path: Path, content: str) -> None:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _relative_to_root(path: Path, root: Path) -> Optional[Path]:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return None


def _service_is_safe(loop_status_payload: dict[str, Any]) -> bool:
    status = loop_status_payload.get("status")
    failure_policy = loop_status_payload.get("failurePolicy")
    if status != "healthy":
        return False
    if not isinstance(failure_policy, dict):
        return False
    if failure_policy.get("operatorHold") is True:
        return False
    return failure_policy.get("degradedState") == "healthy"


def _requested_path_string(output_path: Path, vault_root: Path) -> str:
    relative = _relative_to_root(output_path, vault_root)
    if relative is not None:
        return relative.as_posix()
    return str(output_path.resolve())


def _path_allowed(output_path: Path, vault_root: Path) -> bool:
    resolved_output = output_path.resolve()
    resolved_vault_root = vault_root.resolve()
    if _relative_to_root(resolved_output, resolved_vault_root) is None:
        return False

    for allowed_root in GENERATED_NOTE_ALLOWED_ROOTS:
        resolved_allowed_root = (resolved_vault_root / allowed_root).resolve()
        if _relative_to_root(resolved_output, resolved_allowed_root) is not None:
            return True
    return False


def _base_artifact(
    *,
    workspace_root: Path,
    vault_root: Path,
    output_path: Path,
    content: str,
    loop_status_payload: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "artifactType": "obsidian_generated_sync",
        "generatedAt": generated_at,
        "requestedPath": _requested_path_string(output_path, vault_root),
        "policy": {
            "mode": "generated_note_path_policy",
            "allowedRoots": [path.as_posix() for path in GENERATED_NOTE_ALLOWED_ROOTS],
        },
        "serviceState": {
            "status": loop_status_payload.get("status"),
            "failurePolicy": loop_status_payload.get("failurePolicy"),
        },
        "content": {
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "bytes": len(content.encode("utf-8")),
        },
        "paths": {
            "workspaceRoot": str(workspace_root.resolve()),
            "vaultRoot": str(vault_root.resolve()),
            "outputPath": str(output_path.resolve()),
            "quarantinePath": str((workspace_root / DEFAULT_QUARANTINE_RELATIVE_PATH).resolve()),
        },
    }


def write_generated_note_sync(
    *,
    workspace_root: Path,
    vault_root: Path,
    output_path: Path,
    content: str,
    loop_status_payload: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    workspace_root = Path(workspace_root).resolve()
    vault_root = Path(vault_root).resolve()
    output_path = Path(output_path).resolve()
    quarantine_path = (workspace_root / DEFAULT_QUARANTINE_RELATIVE_PATH).resolve()

    artifact = _base_artifact(
        workspace_root=workspace_root,
        vault_root=vault_root,
        output_path=output_path,
        content=content,
        loop_status_payload=loop_status_payload,
        generated_at=generated_at,
    )

    if not _service_is_safe(loop_status_payload):
        artifact["decision"] = {
            "status": "skipped",
            "reasonCode": "service_not_safe_for_generated_sync",
        }
        _write_json_atomic(quarantine_path, artifact)
        return artifact

    if not _path_allowed(output_path, vault_root):
        artifact["decision"] = {
            "status": "blocked",
            "reasonCode": "generated_path_not_allowed",
        }
        _write_json_atomic(quarantine_path, artifact)
        return artifact

    _write_text_atomic(output_path, content)
    artifact["decision"] = {
        "status": "written",
        "reasonCode": "allowed_generated_path",
    }
    return artifact

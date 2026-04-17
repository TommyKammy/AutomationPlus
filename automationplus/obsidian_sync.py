import errno
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional


GENERATED_NOTE_ALLOWED_ROOTS = (
    Path(".codex-supervisor") / "generated" / "obsidian" / "notes",
    Path("obsidian") / "generated",
)
CURATED_NOTE_PATCH_ALLOWED_ROOTS = (Path("obsidian") / "roadmap",)
CURATED_NOTE_PATCH_ALLOWED_OPERATIONS = ("replace_text",)
DEFAULT_QUARANTINE_RELATIVE_PATH = (
    Path(".codex-supervisor") / "generated" / "obsidian" / "quarantine.json"
)


class UnsafeGeneratedPathError(RuntimeError):
    pass


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _unsafe_path_error(exc: OSError) -> Optional[UnsafeGeneratedPathError]:
    if exc.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
        return UnsafeGeneratedPathError("generated sync path is not safely reachable")
    return None


def _path_relative_to_root(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise UnsafeGeneratedPathError("generated sync path escaped its trusted root") from exc


def _open_directory_no_symlinks(root: Path, relative_dir: Path) -> int:
    if relative_dir.is_absolute():
        raise UnsafeGeneratedPathError("relative directory must not be absolute")

    current_fd = os.open(str(root), _directory_open_flags())
    try:
        for part in relative_dir.parts:
            if part in ("", "."):
                continue
            if part == "..":
                raise UnsafeGeneratedPathError("generated sync path escaped its trusted root")
            try:
                os.mkdir(part, dir_fd=current_fd)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(part, _directory_open_flags(), dir_fd=current_fd)
            except OSError as exc:
                unsafe = _unsafe_path_error(exc)
                if unsafe is not None:
                    raise unsafe from exc
                raise
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _write_text_atomic(root: Path, path: Path, content: str) -> None:
    relative_path = _path_relative_to_root(path, root)
    parent_fd = _open_directory_no_symlinks(root, relative_path.parent)
    temp_name = f".automationplus-{uuid.uuid4().hex}.tmp"
    temp_fd: Optional[int] = None
    temp_created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        temp_fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
        temp_created = True
        with os.fdopen(temp_fd, "wb") as handle:
            temp_fd = None
            handle.write(content.encode("utf-8"))
        os.replace(temp_name, relative_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temp_created = False
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_created:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def _write_json_atomic(root: Path, path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomic(root, path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_text_no_symlinks(root: Path, path: Path) -> str:
    relative_path = _path_relative_to_root(path, root)
    parent_fd = _open_directory_no_symlinks(root, relative_path.parent)
    file_fd: Optional[int] = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            file_fd = os.open(relative_path.name, flags, dir_fd=parent_fd)
        except OSError as exc:
            unsafe = _unsafe_path_error(exc)
            if unsafe is not None:
                raise unsafe from exc
            raise
        with os.fdopen(file_fd, "rb") as handle:
            file_fd = None
            return handle.read().decode("utf-8")
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


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
    if failure_policy.get("operatorHold") is not False:
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


def _path_allowed_in_roots(output_path: Path, vault_root: Path, allowed_roots: tuple[Path, ...]) -> bool:
    resolved_output = output_path.resolve()
    resolved_vault_root = vault_root.resolve()
    if _relative_to_root(resolved_output, resolved_vault_root) is None:
        return False

    for allowed_root in allowed_roots:
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
    output_path = Path(output_path)
    if not output_path.is_absolute():
        output_path = vault_root / output_path
    output_path = output_path.resolve()
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
        _write_json_atomic(workspace_root, quarantine_path, artifact)
        return artifact

    if not _path_allowed(output_path, vault_root):
        artifact["decision"] = {
            "status": "blocked",
            "reasonCode": "generated_path_not_allowed",
        }
        _write_json_atomic(workspace_root, quarantine_path, artifact)
        return artifact

    try:
        _write_text_atomic(vault_root, output_path, content)
    except UnsafeGeneratedPathError:
        artifact["decision"] = {
            "status": "blocked",
            "reasonCode": "generated_path_not_allowed",
        }
        _write_json_atomic(workspace_root, quarantine_path, artifact)
        return artifact
    artifact["decision"] = {
        "status": "written",
        "reasonCode": "allowed_generated_path",
    }
    return artifact


def _curated_note_patch_artifact(
    *,
    workspace_root: Path,
    vault_root: Path,
    patch_artifact: dict[str, Any],
    loop_status_payload: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "artifactType": "obsidian_curated_note_patch_result",
        "generatedAt": generated_at,
        "policy": {
            "mode": "curated_note_patch_policy",
            "allowedRoots": [path.as_posix() for path in CURATED_NOTE_PATCH_ALLOWED_ROOTS],
            "allowedOperations": list(CURATED_NOTE_PATCH_ALLOWED_OPERATIONS),
            "requiresApprovedArtifact": True,
            "requiresExistingNote": True,
            "matchMode": "exact_single_replace",
        },
        "sourceArtifact": {
            "artifactType": patch_artifact.get("artifactType"),
            "approvalStatus": patch_artifact.get("approval", {}).get("status")
            if isinstance(patch_artifact.get("approval"), dict)
            else None,
        },
        "serviceState": {
            "status": loop_status_payload.get("status"),
            "failurePolicy": loop_status_payload.get("failurePolicy"),
        },
        "patches": [],
        "paths": {
            "workspaceRoot": str(workspace_root.resolve()),
            "vaultRoot": str(vault_root.resolve()),
            "quarantinePath": str((workspace_root / DEFAULT_QUARANTINE_RELATIVE_PATH).resolve()),
        },
    }


def _blocked_patch_result(
    *,
    target_path: str,
    operation: Any,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "targetPath": target_path,
        "operation": operation,
        "decision": {
            "status": "blocked",
            "reasonCode": reason_code,
        },
    }


def _set_curated_patch_write_state(
    artifact: dict[str, Any],
    *,
    content_changed: bool,
    content_changed_before_failure: bool,
    rollback_status: str,
) -> None:
    artifact["writeState"] = {
        "contentChanged": content_changed,
        "contentChangedBeforeFailure": content_changed_before_failure,
        "rollbackStatus": rollback_status,
    }


def apply_curated_note_patch_artifact(
    *,
    workspace_root: Path,
    vault_root: Path,
    patch_artifact: dict[str, Any],
    loop_status_payload: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    workspace_root = Path(workspace_root).resolve()
    vault_root = Path(vault_root).resolve()
    quarantine_path = (workspace_root / DEFAULT_QUARANTINE_RELATIVE_PATH).resolve()

    artifact = _curated_note_patch_artifact(
        workspace_root=workspace_root,
        vault_root=vault_root,
        patch_artifact=patch_artifact,
        loop_status_payload=loop_status_payload,
        generated_at=generated_at,
    )
    _set_curated_patch_write_state(
        artifact,
        content_changed=False,
        content_changed_before_failure=False,
        rollback_status="not_needed",
    )

    if not _service_is_safe(loop_status_payload):
        artifact["decision"] = {
            "status": "skipped",
            "reasonCode": "service_not_safe_for_curated_note_patch",
        }
        _write_json_atomic(workspace_root, quarantine_path, artifact)
        return artifact

    if patch_artifact.get("artifactType") != "roadmap_continuity_note_patch_plan":
        artifact["decision"] = {
            "status": "blocked",
            "reasonCode": "curated_note_patch_policy_violation",
        }
        artifact["patches"] = [
            _blocked_patch_result(
                target_path="",
                operation=None,
                reason_code="unexpected_patch_artifact_type",
            )
        ]
        _write_json_atomic(workspace_root, quarantine_path, artifact)
        return artifact

    approval = patch_artifact.get("approval")
    if not isinstance(approval, dict) or approval.get("status") != "approved":
        artifact["decision"] = {
            "status": "blocked",
            "reasonCode": "curated_note_patch_policy_violation",
        }
        artifact["patches"] = [
            _blocked_patch_result(
                target_path="",
                operation=None,
                reason_code="artifact_not_approved",
            )
        ]
        _write_json_atomic(workspace_root, quarantine_path, artifact)
        return artifact

    patches = patch_artifact.get("patches")
    if not isinstance(patches, list) or not patches:
        artifact["decision"] = {
            "status": "blocked",
            "reasonCode": "curated_note_patch_policy_violation",
        }
        artifact["patches"] = [
            _blocked_patch_result(
                target_path="",
                operation=None,
                reason_code="missing_patch_operations",
            )
        ]
        _write_json_atomic(workspace_root, quarantine_path, artifact)
        return artifact

    pending_writes_by_target: dict[str, dict[str, Any]] = {}
    approved_patch_results: list[dict[str, Any]] = []
    for patch in patches:
        target_path = patch.get("targetPath")
        operation = patch.get("operation")
        if not isinstance(target_path, str) or not target_path:
            artifact["patches"].append(
                _blocked_patch_result(
                    target_path="",
                    operation=operation,
                    reason_code="missing_target_path",
                )
            )
            continue
        if operation not in CURATED_NOTE_PATCH_ALLOWED_OPERATIONS:
            artifact["patches"].append(
                _blocked_patch_result(
                    target_path=target_path,
                    operation=operation,
                    reason_code="operation_not_allowed",
                )
            )
            continue

        resolved_target_path = (vault_root / Path(target_path)).resolve()
        if not _path_allowed_in_roots(
            resolved_target_path, vault_root, CURATED_NOTE_PATCH_ALLOWED_ROOTS
        ):
            artifact["patches"].append(
                _blocked_patch_result(
                    target_path=target_path,
                    operation=operation,
                    reason_code="target_path_not_allowed",
                )
            )
            continue

        match_text = patch.get("matchText")
        replacement_text = patch.get("replacementText")
        if not isinstance(match_text, str) or not isinstance(replacement_text, str):
            artifact["patches"].append(
                _blocked_patch_result(
                    target_path=target_path,
                    operation=operation,
                    reason_code="invalid_patch_payload",
                )
            )
            continue

        target_write_key = str(resolved_target_path)
        pending_write = pending_writes_by_target.get(target_write_key)
        if pending_write is None:
            if not resolved_target_path.exists():
                artifact["patches"].append(
                    _blocked_patch_result(
                        target_path=target_path,
                        operation=operation,
                        reason_code="target_note_missing",
                    )
                )
                continue

            try:
                original_content = _read_text_no_symlinks(vault_root, resolved_target_path)
            except FileNotFoundError:
                artifact["patches"].append(
                    _blocked_patch_result(
                        target_path=target_path,
                        operation=operation,
                        reason_code="target_note_missing",
                    )
                )
                continue
            except UnsafeGeneratedPathError:
                artifact["patches"].append(
                    _blocked_patch_result(
                        target_path=target_path,
                        operation=operation,
                        reason_code="target_path_not_safely_reachable",
                    )
                )
                continue
            except OSError:
                artifact["patches"].append(
                    _blocked_patch_result(
                        target_path=target_path,
                        operation=operation,
                        reason_code="target_note_unreadable",
                    )
                )
                continue

            pending_write = {
                "resolvedTargetPath": resolved_target_path,
                "originalContent": original_content,
                "updatedContent": original_content,
                "patchResults": [],
            }
            pending_writes_by_target[target_write_key] = pending_write

        current_content = pending_write["updatedContent"]
        if current_content.count(match_text) != 1:
            artifact["patches"].append(
                _blocked_patch_result(
                    target_path=target_path,
                    operation=operation,
                    reason_code="match_text_not_unique",
                )
            )
            continue

        updated_content = current_content.replace(match_text, replacement_text, 1)
        patch_result = {
            "targetPath": target_path,
            "operation": operation,
            "decision": {
                "status": "approved",
                "reasonCode": "patch_within_policy",
            },
        }
        pending_write["updatedContent"] = updated_content
        pending_write["patchResults"].append(patch_result)
        approved_patch_results.append(patch_result)

    if len(approved_patch_results) != len(patches):
        artifact["decision"] = {
            "status": "blocked",
            "reasonCode": "curated_note_patch_policy_violation",
        }
        _write_json_atomic(workspace_root, quarantine_path, artifact)
        return artifact

    pending_writes = list(pending_writes_by_target.values())
    artifact["patches"] = approved_patch_results
    changed_writes = [
        entry
        for entry in pending_writes
        if entry["updatedContent"] != entry["originalContent"]
    ]
    applied_entries: list[dict[str, Any]] = []
    current_entry: Optional[dict[str, Any]] = None
    try:
        for entry in changed_writes:
            current_entry = entry
            _write_text_atomic(vault_root, entry["resolvedTargetPath"], entry["updatedContent"])
            applied_entries.append(entry)
            current_entry = None
    except (UnsafeGeneratedPathError, OSError) as exc:
        restored_paths: set[str] = set()
        rollback_failed = False
        rollback_entries = list(applied_entries)
        if current_entry is not None:
            rollback_entries.append(current_entry)

        for entry in reversed(rollback_entries):
            try:
                _write_text_atomic(vault_root, entry["resolvedTargetPath"], entry["originalContent"])
                restored_paths.add(str(entry["resolvedTargetPath"]))
            except (UnsafeGeneratedPathError, OSError):
                rollback_failed = True

        for entry in applied_entries:
            decision = (
                {
                    "status": "rolled_back",
                    "reasonCode": "patch_reverted_after_batch_failure",
                }
                if str(entry["resolvedTargetPath"]) in restored_paths
                else {
                    "status": "rollback_failed",
                    "reasonCode": "patch_revert_failed_after_batch_failure",
                }
            )
            for patch_result in entry["patchResults"]:
                patch_result["decision"] = dict(decision)

        failed_index = len(applied_entries)
        failure_reason_code = (
            "target_path_not_safely_reachable"
            if isinstance(exc, UnsafeGeneratedPathError)
            else "target_write_failed"
        )
        failed_entry = (
            current_entry
            if current_entry is not None
            else changed_writes[failed_index]
            if failed_index < len(changed_writes)
            else None
        )
        if failed_entry is not None:
            for patch_result in failed_entry["patchResults"]:
                patch_result["decision"] = {
                    "status": "blocked",
                    "reasonCode": failure_reason_code,
                }
        for entry in pending_writes:
            if entry in applied_entries or entry is failed_entry:
                continue
            for patch_result in entry["patchResults"]:
                patch_result["decision"] = {
                    "status": "blocked",
                    "reasonCode": "not_applied_due_to_batch_failure",
                }

        artifact["decision"] = {
            "status": "blocked",
            "reasonCode": "curated_note_patch_policy_violation",
        }
        _set_curated_patch_write_state(
            artifact,
            content_changed=rollback_failed,
            content_changed_before_failure=bool(applied_entries or current_entry is not None),
            rollback_status="failed" if rollback_failed else "restored",
        )
        _write_json_atomic(workspace_root, quarantine_path, artifact)
        return artifact

    for entry in pending_writes:
        for patch_result in entry["patchResults"]:
            patch_result["decision"] = {
                "status": "applied",
                "reasonCode": "patch_applied",
            }

    artifact["decision"] = {
        "status": "applied",
        "reasonCode": "curated_note_patch_applied",
    }
    _set_curated_patch_write_state(
        artifact,
        content_changed=bool(changed_writes),
        content_changed_before_failure=False,
        rollback_status="not_needed",
    )
    return artifact

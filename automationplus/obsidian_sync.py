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

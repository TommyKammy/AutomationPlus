import fcntl
import json
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Optional

from automationplus.github_ingest import GitHubDeliveryRecord


@dataclass(frozen=True)
class RegistryRecord:
    workflow_kind: str
    routing_key: str
    idempotency_key: str
    repository_full_name: str
    issue_number: Optional[int]
    installation_id: Optional[int]
    first_seen_delivery_id: str
    last_seen_delivery_id: str
    seen_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistryWriteResult:
    status: str
    record: RegistryRecord


class RegistryStateError(RuntimeError):
    pass


class AutomationRegistry:
    _thread_locks: ClassVar[dict[Path, threading.Lock]] = {}
    _thread_locks_guard: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, *, state_path: Optional[Path] = None) -> None:
        self._state_path = Path(state_path).resolve() if state_path is not None else None
        self._records = self._load_records()

    def record(self, candidate: GitHubDeliveryRecord) -> RegistryWriteResult:
        if self._state_path is None:
            self._records, result = _record_records(self._records, candidate)
            return result

        allow_missing = not self._state_path.parent.exists()
        with self._locked_state():
            records = self._load_records(allow_missing=allow_missing)
            updated_records, result = _record_records(records, candidate)
            self._persist_records(updated_records)
            self._records = updated_records
            return result

    def _load_records(self, *, allow_missing: bool = False) -> dict[str, RegistryRecord]:
        if self._state_path is None:
            return {}
        if not self._state_path.exists():
            if allow_missing:
                return {}
            if self._state_path.parent.exists():
                raise RegistryStateError(
                    f"Registry state file is missing: {self._state_path}"
                )
            return {}

        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryStateError(
                f"Registry state file is not valid JSON: {self._state_path}"
            ) from exc

        if not isinstance(payload, dict):
            raise RegistryStateError("Registry state root must be a JSON object")
        if payload.get("version") != 1:
            raise RegistryStateError("Registry state version must be 1")

        raw_records = payload.get("records")
        if not isinstance(raw_records, dict):
            raise RegistryStateError("Registry state records must be a JSON object")

        records: dict[str, RegistryRecord] = {}
        for key, raw_record in raw_records.items():
            if not isinstance(key, str) or not key:
                raise RegistryStateError("Registry state record keys must be non-empty strings")
            if not isinstance(raw_record, dict):
                raise RegistryStateError("Registry state records must contain JSON objects")
            records[key] = _parse_record(key, raw_record)
        return records

    def _persist_records(self, records: dict[str, RegistryRecord]) -> None:
        if self._state_path is None:
            return

        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "records": {
                key: asdict(record)
                for key, record in sorted(records.items())
            },
        }
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self._state_path.parent),
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = Path(handle.name)

        temp_path.replace(self._state_path)

    @classmethod
    def _thread_lock_for(cls, state_path: Path) -> threading.Lock:
        with cls._thread_locks_guard:
            thread_lock = cls._thread_locks.get(state_path)
            if thread_lock is None:
                thread_lock = threading.Lock()
                cls._thread_locks[state_path] = thread_lock
            return thread_lock

    @contextmanager
    def _locked_state(self) -> Any:
        if self._state_path is None:
            yield
            return

        state_dir = self._state_path.parent
        lock_path = state_dir / f"{self._state_path.name}.lock"
        thread_lock = self._thread_lock_for(self._state_path)

        state_dir.mkdir(parents=True, exist_ok=True)
        with thread_lock:
            with lock_path.open("a+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _delivery_id(candidate: GitHubDeliveryRecord) -> str:
    delivery_id = candidate.metadata.get("delivery_id")
    if not isinstance(delivery_id, str) or not delivery_id:
        raise ValueError("GitHub delivery metadata must include a non-empty delivery_id")
    return delivery_id


def _record_records(
    records: dict[str, RegistryRecord],
    candidate: GitHubDeliveryRecord,
) -> tuple[dict[str, RegistryRecord], RegistryWriteResult]:
    delivery_id = _delivery_id(candidate)
    next_records = dict(records)
    existing = next_records.get(candidate.idempotency_key)
    if existing is None:
        record = RegistryRecord(
            workflow_kind=candidate.workflow_kind,
            routing_key=candidate.routing_key,
            idempotency_key=candidate.idempotency_key,
            repository_full_name=candidate.repository_full_name,
            issue_number=candidate.issue_number,
            installation_id=candidate.installation_id,
            first_seen_delivery_id=delivery_id,
            last_seen_delivery_id=delivery_id,
            seen_count=1,
            metadata=_stable_metadata(candidate.metadata),
        )
        next_records[candidate.idempotency_key] = record
        return next_records, RegistryWriteResult(status="recorded", record=record)

    _assert_same_identity(existing, candidate)
    record = RegistryRecord(
        workflow_kind=existing.workflow_kind,
        routing_key=existing.routing_key,
        idempotency_key=existing.idempotency_key,
        repository_full_name=existing.repository_full_name,
        issue_number=existing.issue_number,
        installation_id=existing.installation_id,
        first_seen_delivery_id=existing.first_seen_delivery_id,
        last_seen_delivery_id=delivery_id,
        seen_count=existing.seen_count + 1,
        metadata=existing.metadata,
    )
    next_records[candidate.idempotency_key] = record
    return next_records, RegistryWriteResult(status="duplicate", record=record)


def _stable_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key != "delivery_id"
    }


def _assert_same_identity(existing: RegistryRecord, candidate: GitHubDeliveryRecord) -> None:
    if existing.workflow_kind != candidate.workflow_kind:
        raise ValueError("Registry idempotency key collision across workflow kinds")
    if existing.routing_key != candidate.routing_key:
        raise ValueError("Registry idempotency key collision across routing keys")
    if existing.repository_full_name != candidate.repository_full_name:
        raise ValueError("Registry idempotency key collision across repositories")
    if existing.issue_number != candidate.issue_number:
        raise ValueError("Registry idempotency key collision across issue numbers")
    if existing.installation_id != candidate.installation_id:
        raise ValueError("Registry idempotency key collision across installations")


def _parse_record(idempotency_key: str, raw_record: dict[str, Any]) -> RegistryRecord:
    metadata = raw_record.get("metadata")
    if not isinstance(metadata, dict):
        raise RegistryStateError("Registry state record metadata must be a JSON object")

    record = RegistryRecord(
        workflow_kind=_require_string(raw_record, "workflow_kind"),
        routing_key=_require_string(raw_record, "routing_key"),
        idempotency_key=_require_string(raw_record, "idempotency_key"),
        repository_full_name=_require_string(raw_record, "repository_full_name"),
        issue_number=_optional_int(raw_record, "issue_number"),
        installation_id=_optional_int(raw_record, "installation_id"),
        first_seen_delivery_id=_require_string(raw_record, "first_seen_delivery_id"),
        last_seen_delivery_id=_require_string(raw_record, "last_seen_delivery_id"),
        seen_count=_require_positive_int(raw_record, "seen_count"),
        metadata=metadata,
    )
    if record.idempotency_key != idempotency_key:
        raise RegistryStateError("Registry state record key does not match idempotency_key")
    return record


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RegistryStateError(f"Registry state field '{key}' must be a non-empty string")
    return value


def _optional_int(payload: dict[str, Any], key: str) -> Optional[int]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise RegistryStateError(f"Registry state field '{key}' must be an integer when present")
    return value


def _require_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RegistryStateError(f"Registry state field '{key}' must be a positive integer")
    return value

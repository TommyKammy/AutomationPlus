from dataclasses import dataclass, field
from typing import Any, Optional

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


class AutomationRegistry:
    def __init__(self) -> None:
        self._records: dict[str, RegistryRecord] = {}

    def record(self, candidate: GitHubDeliveryRecord) -> RegistryWriteResult:
        delivery_id = _delivery_id(candidate)
        existing = self._records.get(candidate.idempotency_key)
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
            self._records[candidate.idempotency_key] = record
            return RegistryWriteResult(status="recorded", record=record)

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
        self._records[candidate.idempotency_key] = record
        return RegistryWriteResult(status="duplicate", record=record)


def _delivery_id(candidate: GitHubDeliveryRecord) -> str:
    delivery_id = candidate.metadata.get("delivery_id")
    if not isinstance(delivery_id, str) or not delivery_id:
        raise ValueError("GitHub delivery metadata must include a non-empty delivery_id")
    return delivery_id


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

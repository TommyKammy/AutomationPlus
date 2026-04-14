from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class GitHubDeliveryRecord:
    workflow_kind: str
    routing_key: str
    idempotency_key: str
    repository_full_name: str
    issue_number: Optional[int]
    installation_id: Optional[int]
    metadata: dict[str, Any]


_ISSUE_ACTION_TO_WORKFLOW_KIND = {
    "opened": "github.issue.opened",
    "reopened": "github.issue.reopened",
    "labeled": "github.issue.labeled",
    "unlabeled": "github.issue.unlabeled",
}


def classify_github_delivery(
    *, event_name: str, delivery_id: str, payload: Mapping[str, Any]
) -> Optional[GitHubDeliveryRecord]:
    if event_name != "issues":
        return None

    action = payload.get("action")
    workflow_kind = _ISSUE_ACTION_TO_WORKFLOW_KIND.get(action)
    if workflow_kind is None:
        return None

    repository = _require_mapping(payload, "repository")
    issue = _require_mapping(payload, "issue")
    sender = _optional_mapping(payload, "sender")
    installation = _optional_mapping(payload, "installation")

    repository_full_name = _require_string(repository, "full_name")
    issue_number = _require_int(issue, "number")
    issue_node_id = _require_string(issue, "node_id")
    issue_html_url = _optional_string(issue, "html_url")
    installation_id = _optional_int(installation, "id") if installation else None
    sender_login = _optional_string(sender, "login") if sender else None

    metadata = {
        "action": action,
        "delivery_id": delivery_id,
        "event_name": event_name,
        "issue_node_id": issue_node_id,
    }
    if issue_html_url is not None:
        metadata["issue_html_url"] = issue_html_url
    if sender_login is not None:
        metadata["sender_login"] = sender_login

    return GitHubDeliveryRecord(
        workflow_kind=workflow_kind,
        routing_key=f"{workflow_kind}:{repository_full_name}:{issue_number}",
        idempotency_key=f"github:{event_name}:{issue_node_id}:{action}",
        repository_full_name=repository_full_name,
        issue_number=issue_number,
        installation_id=installation_id,
        metadata=metadata,
    )


def _require_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"GitHub payload field '{key}' must be an object")
    return value


def _optional_mapping(payload: Mapping[str, Any], key: str) -> Optional[Mapping[str, Any]]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"GitHub payload field '{key}' must be an object when present")
    return value


def _require_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"GitHub payload field '{key}' must be a non-empty string")
    return value


def _optional_string(payload: Mapping[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"GitHub payload field '{key}' must be a non-empty string when present")
    return value


def _require_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"GitHub payload field '{key}' must be an integer")
    return value


def _optional_int(payload: Mapping[str, Any], key: str) -> Optional[int]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"GitHub payload field '{key}' must be an integer when present")
    return value

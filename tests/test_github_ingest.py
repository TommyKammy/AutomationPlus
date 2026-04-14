import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automationplus.github_ingest import GitHubDeliveryRecord, classify_github_delivery
from automationplus.registry import AutomationRegistry, RegistryStateError


class GitHubIngestTests(unittest.TestCase):
    def test_classifies_issue_opened_delivery_into_phase1_candidate(self) -> None:
        candidate = classify_github_delivery(
            event_name="issues",
            delivery_id="delivery-001",
            payload={
                "action": "opened",
                "repository": {
                    "full_name": "TommyKammy/AutomationPlus",
                },
                "installation": {
                    "id": 42,
                },
                "issue": {
                    "number": 4,
                    "node_id": "I_kwDOExample",
                    "title": "Build GitHub event ingest and automation registry foundation",
                    "html_url": "https://github.com/TommyKammy/AutomationPlus/issues/4",
                },
                "sender": {
                    "login": "octocat",
                },
            },
        )

        self.assertEqual(candidate.workflow_kind, "github.issue.opened")
        self.assertEqual(candidate.routing_key, "github.issue.opened:TommyKammy/AutomationPlus:4")
        self.assertEqual(candidate.idempotency_key, "github:issues:I_kwDOExample:opened")
        self.assertEqual(candidate.repository_full_name, "TommyKammy/AutomationPlus")
        self.assertEqual(candidate.issue_number, 4)
        self.assertEqual(candidate.installation_id, 42)
        self.assertEqual(
            candidate.metadata,
            {
                "action": "opened",
                "delivery_id": "delivery-001",
                "event_name": "issues",
                "issue_html_url": "https://github.com/TommyKammy/AutomationPlus/issues/4",
                "issue_node_id": "I_kwDOExample",
                "sender_login": "octocat",
            },
        )

    def test_ignores_non_phase1_issue_actions(self) -> None:
        candidate = classify_github_delivery(
            event_name="issues",
            delivery_id="delivery-002",
            payload={
                "action": "deleted",
                "repository": {
                    "full_name": "TommyKammy/AutomationPlus",
                },
                "issue": {
                    "number": 4,
                    "node_id": "I_kwDOExample",
                },
            },
        )

        self.assertIsNone(candidate)


class AutomationRegistryTests(unittest.TestCase):
    def test_registry_marks_replayed_delivery_without_rewriting_identity(self) -> None:
        registry = AutomationRegistry()
        candidate = GitHubDeliveryRecord(
            workflow_kind="github.issue.opened",
            routing_key="github.issue.opened:TommyKammy/AutomationPlus:4",
            idempotency_key="github:issues:I_kwDOExample:opened",
            repository_full_name="TommyKammy/AutomationPlus",
            issue_number=4,
            installation_id=42,
            metadata={
                "action": "opened",
                "delivery_id": "delivery-001",
                "event_name": "issues",
                "issue_node_id": "I_kwDOExample",
            },
        )

        first = registry.record(candidate)
        replay = registry.record(
            GitHubDeliveryRecord(
                workflow_kind="github.issue.opened",
                routing_key="github.issue.opened:TommyKammy/AutomationPlus:4",
                idempotency_key="github:issues:I_kwDOExample:opened",
                repository_full_name="TommyKammy/AutomationPlus",
                issue_number=4,
                installation_id=42,
                metadata={
                    "action": "opened",
                    "delivery_id": "delivery-002",
                    "event_name": "issues",
                    "issue_node_id": "I_kwDOExample",
                },
            )
        )

        self.assertEqual(first.status, "recorded")
        self.assertEqual(first.record.first_seen_delivery_id, "delivery-001")
        self.assertEqual(first.record.last_seen_delivery_id, "delivery-001")
        self.assertEqual(first.record.seen_count, 1)

        self.assertEqual(replay.status, "duplicate")
        self.assertEqual(replay.record.first_seen_delivery_id, "delivery-001")
        self.assertEqual(replay.record.last_seen_delivery_id, "delivery-002")
        self.assertEqual(replay.record.seen_count, 2)
        self.assertEqual(replay.record.routing_key, candidate.routing_key)
        self.assertEqual(replay.record.workflow_kind, candidate.workflow_kind)
        self.assertEqual(
            replay.record.metadata,
            {
                "action": "opened",
                "event_name": "issues",
                "issue_node_id": "I_kwDOExample",
            },
        )

    def test_registry_persists_duplicates_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / ".automationplus" / "registry.json"
            candidate = GitHubDeliveryRecord(
                workflow_kind="github.issue.opened",
                routing_key="github.issue.opened:TommyKammy/AutomationPlus:4",
                idempotency_key="github:issues:I_kwDOExample:opened",
                repository_full_name="TommyKammy/AutomationPlus",
                issue_number=4,
                installation_id=42,
                metadata={
                    "action": "opened",
                    "delivery_id": "delivery-001",
                    "event_name": "issues",
                    "issue_node_id": "I_kwDOExample",
                },
            )

            first = AutomationRegistry(state_path=state_path).record(candidate)
            replay = AutomationRegistry(state_path=state_path).record(
                GitHubDeliveryRecord(
                    workflow_kind="github.issue.opened",
                    routing_key="github.issue.opened:TommyKammy/AutomationPlus:4",
                    idempotency_key="github:issues:I_kwDOExample:opened",
                    repository_full_name="TommyKammy/AutomationPlus",
                    issue_number=4,
                    installation_id=42,
                    metadata={
                        "action": "opened",
                        "delivery_id": "delivery-002",
                        "event_name": "issues",
                        "issue_node_id": "I_kwDOExample",
                    },
                )
            )

        self.assertEqual(first.status, "recorded")
        self.assertEqual(replay.status, "duplicate")
        self.assertEqual(replay.record.first_seen_delivery_id, "delivery-001")
        self.assertEqual(replay.record.last_seen_delivery_id, "delivery-002")
        self.assertEqual(replay.record.seen_count, 2)

    def test_registry_merges_interleaved_writes_from_live_registries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / ".automationplus" / "registry.json"
            first_candidate = GitHubDeliveryRecord(
                workflow_kind="github.issue.opened",
                routing_key="github.issue.opened:TommyKammy/AutomationPlus:4",
                idempotency_key="github:issues:I_kwDOExampleOne:opened",
                repository_full_name="TommyKammy/AutomationPlus",
                issue_number=4,
                installation_id=42,
                metadata={
                    "action": "opened",
                    "delivery_id": "delivery-001",
                    "event_name": "issues",
                    "issue_node_id": "I_kwDOExampleOne",
                },
            )
            second_candidate = GitHubDeliveryRecord(
                workflow_kind="github.issue.opened",
                routing_key="github.issue.opened:TommyKammy/AutomationPlus:5",
                idempotency_key="github:issues:I_kwDOExampleTwo:opened",
                repository_full_name="TommyKammy/AutomationPlus",
                issue_number=5,
                installation_id=42,
                metadata={
                    "action": "opened",
                    "delivery_id": "delivery-002",
                    "event_name": "issues",
                    "issue_node_id": "I_kwDOExampleTwo",
                },
            )

            first_registry = AutomationRegistry(state_path=state_path)
            second_registry = AutomationRegistry(state_path=state_path)

            first_write = first_registry.record(first_candidate)
            second_write = second_registry.record(second_candidate)
            replay = AutomationRegistry(state_path=state_path).record(
                GitHubDeliveryRecord(
                    workflow_kind="github.issue.opened",
                    routing_key="github.issue.opened:TommyKammy/AutomationPlus:4",
                    idempotency_key="github:issues:I_kwDOExampleOne:opened",
                    repository_full_name="TommyKammy/AutomationPlus",
                    issue_number=4,
                    installation_id=42,
                    metadata={
                        "action": "opened",
                        "delivery_id": "delivery-003",
                        "event_name": "issues",
                        "issue_node_id": "I_kwDOExampleOne",
                    },
                )
            )
            on_disk = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(first_write.status, "recorded")
        self.assertEqual(second_write.status, "recorded")
        self.assertEqual(replay.status, "duplicate")
        self.assertEqual(replay.record.seen_count, 2)
        self.assertCountEqual(
            on_disk["records"].keys(),
            [
                "github:issues:I_kwDOExampleOne:opened",
                "github:issues:I_kwDOExampleTwo:opened",
            ],
        )

    def test_registry_does_not_keep_in_memory_updates_when_persist_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / ".automationplus" / "registry.json"
            registry = AutomationRegistry(state_path=state_path)
            candidate = GitHubDeliveryRecord(
                workflow_kind="github.issue.opened",
                routing_key="github.issue.opened:TommyKammy/AutomationPlus:4",
                idempotency_key="github:issues:I_kwDOExample:opened",
                repository_full_name="TommyKammy/AutomationPlus",
                issue_number=4,
                installation_id=42,
                metadata={
                    "action": "opened",
                    "delivery_id": "delivery-001",
                    "event_name": "issues",
                    "issue_node_id": "I_kwDOExample",
                },
            )
            replay = GitHubDeliveryRecord(
                workflow_kind="github.issue.opened",
                routing_key="github.issue.opened:TommyKammy/AutomationPlus:4",
                idempotency_key="github:issues:I_kwDOExample:opened",
                repository_full_name="TommyKammy/AutomationPlus",
                issue_number=4,
                installation_id=42,
                metadata={
                    "action": "opened",
                    "delivery_id": "delivery-002",
                    "event_name": "issues",
                    "issue_node_id": "I_kwDOExample",
                },
            )

            first_write = registry.record(candidate)

            with mock.patch.object(
                registry,
                "_persist_records",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaisesRegex(OSError, "disk full"):
                    registry.record(replay)

            self.assertEqual(
                registry._records[candidate.idempotency_key].last_seen_delivery_id,
                "delivery-001",
            )
            self.assertEqual(registry._records[candidate.idempotency_key].seen_count, 1)
            successful_retry = registry.record(replay)

        self.assertEqual(first_write.status, "recorded")
        self.assertEqual(successful_retry.status, "duplicate")
        self.assertEqual(successful_retry.record.seen_count, 2)

    def test_registry_raises_when_existing_state_file_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / ".automationplus" / "registry.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text('{"version": ', encoding="utf-8")

            with self.assertRaisesRegex(
                RegistryStateError,
                "Registry state file is not valid JSON",
            ):
                AutomationRegistry(state_path=state_path)

    def test_registry_raises_when_expected_state_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / ".automationplus" / "registry.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)

            with self.assertRaisesRegex(
                RegistryStateError,
                "Registry state file is missing",
            ):
                AutomationRegistry(state_path=state_path)

    def test_registry_writes_repo_local_state_on_first_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / ".automationplus" / "registry.json"
            record = AutomationRegistry(state_path=state_path).record(
                GitHubDeliveryRecord(
                    workflow_kind="github.issue.opened",
                    routing_key="github.issue.opened:TommyKammy/AutomationPlus:4",
                    idempotency_key="github:issues:I_kwDOExample:opened",
                    repository_full_name="TommyKammy/AutomationPlus",
                    issue_number=4,
                    installation_id=42,
                    metadata={
                        "action": "opened",
                        "delivery_id": "delivery-001",
                        "event_name": "issues",
                        "issue_node_id": "I_kwDOExample",
                    },
                )
            )

            on_disk = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(record.status, "recorded")
        self.assertEqual(on_disk["version"], 1)
        self.assertEqual(
            on_disk["records"]["github:issues:I_kwDOExample:opened"]["first_seen_delivery_id"],
            "delivery-001",
        )
        self.assertEqual(
            on_disk["records"]["github:issues:I_kwDOExample:opened"]["last_seen_delivery_id"],
            "delivery-001",
        )
        self.assertEqual(
            on_disk["records"]["github:issues:I_kwDOExample:opened"]["seen_count"],
            1,
        )

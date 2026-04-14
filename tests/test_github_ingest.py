import unittest

from automationplus.github_ingest import GitHubDeliveryRecord, classify_github_delivery
from automationplus.registry import AutomationRegistry


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

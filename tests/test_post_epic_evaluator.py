import json
import tempfile
import unittest
from pathlib import Path

from automationplus.post_epic_evaluator import (
    EpicChildIssueState,
    PostEpicEvaluationJob,
    PullRequestFact,
    build_post_epic_findings_pack,
    evaluate_completed_epic,
    write_post_epic_findings_pack,
    write_post_epic_evaluation_artifact,
)


class PostEpicEvaluatorTests(unittest.TestCase):
    def test_completed_epic_evaluation_persists_meta_artifact_with_target_sha_and_source_context(
        self,
    ) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="abc123def4567890abc123def4567890abc123de",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=5,
                    title="Build supervisor health mirror for loop observation",
                    state="closed",
                    conclusion="completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/5",
                ),
                EpicChildIssueState(
                    issue_number=6,
                    title="Build post-Epic completion evaluator",
                    state="closed",
                    conclusion="completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/6",
                ),
            ],
            related_pull_requests=[
                PullRequestFact(
                    number=12,
                    title="Build supervisor health mirror snapshot",
                    state="closed",
                    merged=True,
                    target_branch="main",
                    merge_commit_sha="5793a58c5439527f1315af6e66b1ff8ef2e1f746",
                    pull_request_url="https://github.com/TommyKammy/AutomationPlus/pull/12",
                    source_issue_numbers=[5],
                ),
            ],
            generated_at="2026-04-14T10:00:00Z",
        )

        artifact = evaluate_completed_epic(job)

        self.assertEqual(artifact["schemaVersion"], 1)
        self.assertEqual(artifact["artifactType"], "post_epic_evaluation")
        self.assertEqual(artifact["routing"]["lane"], "meta")
        self.assertFalse(artifact["routing"]["interferesWithActivePrExecution"])
        self.assertEqual(
            artifact["target"],
            {
                "ref": "refs/heads/main",
                "sha": "abc123def4567890abc123def4567890abc123de",
            },
        )
        self.assertEqual(artifact["sourceContext"]["childIssues"][1]["issueNumber"], 6)
        self.assertEqual(
            artifact["sourceContext"]["childIssues"][1]["conclusion"],
            "completed",
        )
        self.assertEqual(
            artifact["sourceContext"]["relatedPullRequests"][0]["mergeCommitSha"],
            "5793a58c5439527f1315af6e66b1ff8ef2e1f746",
        )
        self.assertEqual(
            artifact["summary"],
            {
                "childIssueCount": 2,
                "completedChildIssueCount": 2,
                "relatedPullRequestCount": 1,
                "mergedPullRequestCount": 1,
            },
        )

        with tempfile.TemporaryDirectory() as tempdir:
            output_path = Path(tempdir) / "post-epic-evaluation.json"
            persisted = write_post_epic_evaluation_artifact(output_path, job)
            on_disk = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted, artifact)
        self.assertEqual(on_disk, artifact)

    def test_completed_epic_evaluation_derives_structured_findings_pack(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="def456abc1237890def456abc1237890def456ab",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=7,
                    title="Build post-Epic findings pack generation",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/7",
                ),
                EpicChildIssueState(
                    issue_number=7,
                    title="Build post-Epic findings pack generation",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/7",
                ),
                EpicChildIssueState(
                    issue_number=5,
                    title="Build supervisor health mirror for loop observation",
                    state="closed",
                    conclusion="completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/5",
                ),
            ],
            related_pull_requests=[
                PullRequestFact(
                    number=14,
                    title="Prototype findings pack output",
                    state="open",
                    merged=False,
                    target_branch="main",
                    merge_commit_sha=None,
                    pull_request_url="https://github.com/TommyKammy/AutomationPlus/pull/14",
                    source_issue_numbers=[7],
                ),
            ],
            generated_at="2026-04-14T11:00:00Z",
        )

        evaluation_artifact = evaluate_completed_epic(job)
        findings_pack = build_post_epic_findings_pack(evaluation_artifact)

        self.assertEqual(findings_pack["schemaVersion"], 1)
        self.assertEqual(findings_pack["artifactType"], "post_epic_findings_pack")
        self.assertEqual(
            findings_pack["sourceArtifact"],
            {
                "artifactType": "post_epic_evaluation",
                "generatedAt": "2026-04-14T11:00:00Z",
                "target": {
                    "ref": "refs/heads/main",
                    "sha": "def456abc1237890def456abc1237890def456ab",
                },
            },
        )
        self.assertEqual(findings_pack["routing"]["lane"], "meta")
        self.assertEqual(findings_pack["routing"]["sourceClassification"], "meta_only")
        self.assertTrue(findings_pack["routing"]["excludeCurrentPrResiduals"])
        self.assertEqual(len(findings_pack["actionableFindings"]), 2)
        self.assertEqual(
            findings_pack["actionableFindings"][0]["dedupeKey"],
            "child-issue:7:not_completed",
        )
        self.assertEqual(findings_pack["actionableFindings"][0]["severity"], "medium")
        self.assertEqual(findings_pack["actionableFindings"][0]["confidence"], "high")
        self.assertEqual(
            findings_pack["actionableFindings"][0]["sourceClassification"],
            "meta_only",
        )
        self.assertEqual(
            findings_pack["actionableFindings"][1]["dedupeKey"],
            "pull-request:14:unmerged",
        )
        self.assertEqual(len(findings_pack["suppressedFindings"]["duplicates"]), 1)
        self.assertEqual(
            findings_pack["suppressedFindings"]["duplicates"][0]["duplicateOf"],
            "child-issue:7:not_completed",
        )
        self.assertEqual(len(findings_pack["suppressedFindings"]["lowValue"]), 1)
        self.assertEqual(
            findings_pack["suppressedFindings"]["lowValue"][0]["dedupeKey"],
            "child-issue:5:completed",
        )
        self.assertEqual(
            findings_pack["summary"],
            {
                "actionableFindingCount": 2,
                "suppressedDuplicateCount": 1,
                "suppressedLowValueCount": 1,
            },
        )

        with tempfile.TemporaryDirectory() as tempdir:
            output_path = Path(tempdir) / "post-epic-findings-pack.json"
            persisted = write_post_epic_findings_pack(output_path, evaluation_artifact)
            on_disk = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted, findings_pack)
        self.assertEqual(on_disk, findings_pack)

        findings_pack["sourceArtifact"]["target"]["sha"] = "mutated"
        self.assertEqual(
            evaluation_artifact["target"]["sha"],
            "def456abc1237890def456abc1237890def456ab",
        )


if __name__ == "__main__":
    unittest.main()

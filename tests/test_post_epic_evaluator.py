import json
import tempfile
import unittest
from pathlib import Path

from automationplus.post_epic_evaluator import (
    EpicChildIssueState,
    PostEpicEvaluationJob,
    PullRequestFact,
    evaluate_completed_epic,
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


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from automationplus.post_epic_evaluator import (
    EpicChildIssueState,
    PostEpicEvaluationJob,
    PullRequestFact,
    build_post_epic_findings_pack,
    build_post_epic_follow_up_issue_publish_plan,
    evaluate_completed_epic,
    write_post_epic_findings_pack,
    write_post_epic_evaluation_artifact,
    write_post_epic_follow_up_issue_publish_plan,
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
                "evaluation": {
                    "trigger": "epic.completed",
                    "epic": {
                        "issueNumber": 1,
                        "title": "Epic: Phase 1 foundations for AutomationPlus loop automation",
                        "issueUrl": "https://github.com/TommyKammy/AutomationPlus/issues/1",
                    },
                },
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

    def test_findings_pack_renders_template_clean_follow_up_issue_and_promotes_when_lint_clean(
        self,
    ) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="1111111111111111111111111111111111111111",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=8,
                    title="Build post-Epic follow-up issue publisher",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/8",
                ),
            ],
            generated_at="2026-04-14T12:00:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        publish_plan = build_post_epic_follow_up_issue_publish_plan(
            findings_pack,
            issue_lint_result={
                "executionReady": True,
                "missingRequired": [],
                "metadataErrors": [],
                "highRiskBlockingAmbiguity": None,
            },
        )

        self.assertEqual(publish_plan["schemaVersion"], 1)
        self.assertEqual(
            publish_plan["artifactType"],
            "post_epic_follow_up_issue_publish_plan",
        )
        self.assertEqual(
            publish_plan["routing"],
            {
                "lane": "meta",
                "sourceClassification": "meta_only",
                "excludeCurrentPrResiduals": True,
                "publishTarget": "post_epic_follow_up",
            },
        )
        self.assertEqual(
            publish_plan["draftIssue"]["title"],
            "Post-epic follow-up for #1 Epic: Phase 1 foundations for AutomationPlus loop automation",
        )
        self.assertEqual(
            publish_plan["draftIssue"]["labels"],
            ["codex", "post-epic-follow-up"],
        )
        self.assertEqual(publish_plan["draftIssue"]["state"], "draft")
        self.assertEqual(
            publish_plan["draftIssue"]["body"],
            """## Summary
Follow up on remaining post-epic work for #1 Epic: Phase 1 foundations for AutomationPlus loop automation.

## Scope
- convert the post-epic findings pack into one execution-ready follow-up issue
- keep current-PR local-review residual routing excluded from this publish path
- carry forward actionable meta-only follow-up findings without silent promotion

## Acceptance criteria
- the generated follow-up issue remains template-clean for codex-supervisor issue-lint
- the publish plan promotes only when issue-lint is execution-ready and free of blocking ambiguity
- unsafe or duplicate drafts are quarantined instead of being promoted

## Verification
- python3 -m unittest tests.test_post_epic_evaluator
- review the generated issue body for required sections and scheduling metadata

Part of: #1
Depends on: none
Parallelizable: No

## Execution order
1 of 1""",
        )
        self.assertEqual(
            publish_plan["promotion"],
            {
                "decision": "promote",
                "reason": "issue_lint_clean",
            },
        )
        self.assertEqual(
            publish_plan["sourceFindings"],
            [
                {
                    "dedupeKey": "child-issue:8:not_completed",
                    "findingType": "child_issue_follow_up_candidate",
                    "title": "Child issue requires follow-up after epic close: #8 Build post-Epic follow-up issue publisher",
                    "severity": "medium",
                    "confidence": "high",
                    "novelty": "candidate",
                    "sourceClassification": "meta_only",
                    "evidence": {
                        "issueNumber": 8,
                        "issueUrl": "https://github.com/TommyKammy/AutomationPlus/issues/8",
                        "state": "open",
                        "conclusion": "not_completed",
                    },
                }
            ],
        )

        with tempfile.TemporaryDirectory() as tempdir:
            output_path = Path(tempdir) / "post-epic-follow-up-publish-plan.json"
            persisted = write_post_epic_follow_up_issue_publish_plan(
                output_path,
                findings_pack,
                issue_lint_result={
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
            )
            on_disk = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted, publish_plan)
        self.assertEqual(on_disk, publish_plan)

    def test_follow_up_issue_publish_plan_quarantines_duplicate_or_unsafe_drafts(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="2222222222222222222222222222222222222222",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=8,
                    title="Build post-Epic follow-up issue publisher",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/8",
                ),
            ],
            generated_at="2026-04-14T12:30:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        quarantined = build_post_epic_follow_up_issue_publish_plan(
            findings_pack,
            issue_lint_result={
                "executionReady": False,
                "missingRequired": ["verification"],
                "metadataErrors": [],
                "highRiskBlockingAmbiguity": None,
            },
            existing_draft_keys=["epic-follow-up:1"],
        )

        self.assertEqual(
            quarantined["promotion"],
            {
                "decision": "quarantine",
                "reason": "duplicate_draft",
            },
        )
        self.assertEqual(
            quarantined["quarantine"],
            {
                "reason": "duplicate_draft",
                "blockingDetails": ["draft already exists for dedupe key epic-follow-up:1"],
            },
        )

    def test_follow_up_issue_publish_plan_requires_present_clean_issue_lint_result(
        self,
    ) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="3333333333333333333333333333333333333333",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=8,
                    title="Build post-Epic follow-up issue publisher",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/8",
                ),
            ],
            generated_at="2026-04-14T13:00:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))

        missing_lint = build_post_epic_follow_up_issue_publish_plan(findings_pack)
        self.assertEqual(
            missing_lint["promotion"],
            {
                "decision": "quarantine",
                "reason": "issue_lint_missing",
            },
        )
        self.assertEqual(
            missing_lint["quarantine"],
            {
                "reason": "issue_lint_missing",
                "blockingDetails": [
                    "publish plan requires an issue-lint result before promotion"
                ],
            },
        )

        ambiguous_lint = build_post_epic_follow_up_issue_publish_plan(
            findings_pack,
            issue_lint_result={
                "executionReady": True,
                "missingRequired": [],
                "metadataErrors": [],
                "highRiskBlockingAmbiguity": "assignee resolution is ambiguous",
            },
        )
        self.assertEqual(
            ambiguous_lint["promotion"],
            {
                "decision": "quarantine",
                "reason": "issue_lint_blocked",
            },
        )
        self.assertEqual(
            ambiguous_lint["quarantine"],
            {
                "reason": "issue_lint_blocked",
                "blockingDetails": [
                    "issue-lint blocking ambiguity: assignee resolution is ambiguous"
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()

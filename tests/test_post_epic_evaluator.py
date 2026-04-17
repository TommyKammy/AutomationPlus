import json
import tempfile
import unittest
from pathlib import Path

from automationplus.post_epic_evaluator import (
    EpicChildIssueState,
    PostEpicEvaluationJob,
    PullRequestFact,
    build_post_epic_findings_pack,
    build_roadmap_continuity_note_patch_plan,
    build_post_epic_follow_up_issue_publish_plan,
    build_planning_pack,
    build_roadmap_continuity_issue_set_publish_plan,
    build_roadmap_proposal_pack,
    evaluate_completed_epic,
    write_post_epic_findings_pack,
    write_post_epic_evaluation_artifact,
    write_post_epic_follow_up_issue_publish_plan,
    write_planning_pack,
    write_roadmap_proposal_pack,
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

## Findings to carry forward
- Child issue requires follow-up after epic close: #8 Build post-Epic follow-up issue publisher (issue #8)

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

    def test_follow_up_issue_body_renders_actionable_findings_context(self) -> None:
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
            related_pull_requests=[
                PullRequestFact(
                    number=21,
                    title="Stabilize post-epic publish plan gating",
                    state="open",
                    merged=False,
                    target_branch="main",
                    merge_commit_sha=None,
                    pull_request_url="https://github.com/TommyKammy/AutomationPlus/pull/21",
                    source_issue_numbers=[],
                ),
            ],
            generated_at="2026-04-14T12:15:00Z",
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

        self.assertIn("## Findings to carry forward", publish_plan["draftIssue"]["body"])
        self.assertIn(
            "- Child issue requires follow-up after epic close: #8 Build post-Epic follow-up issue publisher",
            publish_plan["draftIssue"]["body"],
        )
        self.assertIn(
            "- Related pull request still requires follow-up after epic close: #21 Stabilize post-epic publish plan gating",
            publish_plan["draftIssue"]["body"],
        )
        self.assertIn("## Acceptance criteria", publish_plan["draftIssue"]["body"])
        self.assertIn("## Verification", publish_plan["draftIssue"]["body"])

    def test_follow_up_issue_body_tolerates_malformed_actionable_findings(self) -> None:
        findings_pack = {
            "schemaVersion": 1,
            "artifactType": "post_epic_findings_pack",
            "generatedAt": "2026-04-15T00:00:00Z",
            "sourceArtifact": {
                "artifactType": "post_epic_evaluation",
                "generatedAt": "2026-04-15T00:00:00Z",
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
                    "sha": "4444444444444444444444444444444444444444",
                },
            },
            "routing": {
                "lane": "meta",
                "sourceClassification": "meta_only",
                "excludeCurrentPrResiduals": True,
            },
            "actionableFindings": [
                {
                    "title": "Child issue requires follow-up after epic close: #8 Build post-Epic follow-up issue publisher",
                    "evidence": {
                        "issueNumber": 8,
                        "issueUrl": "https://github.com/TommyKammy/AutomationPlus/issues/8",
                    },
                },
                None,
                "unexpected",
                {
                    "title": "  ",
                    "evidence": {
                        "pullRequestNumber": 21,
                        "pullRequestUrl": "https://github.com/TommyKammy/AutomationPlus/pull/21",
                    },
                },
            ],
        }

        publish_plan = build_post_epic_follow_up_issue_publish_plan(
            findings_pack,
            issue_lint_result={
                "executionReady": True,
                "missingRequired": [],
                "metadataErrors": [],
                "highRiskBlockingAmbiguity": None,
            },
        )

        self.assertIn(
            "- Child issue requires follow-up after epic close: #8 Build post-Epic follow-up issue publisher (issue #8)",
            publish_plan["draftIssue"]["body"],
        )
        self.assertIn(
            "- Follow-up finding (malformed entry)",
            publish_plan["draftIssue"]["body"],
        )
        self.assertIn(
            "- Follow-up finding (PR #21)",
            publish_plan["draftIssue"]["body"],
        )
        self.assertEqual(
            publish_plan["promotion"],
            {
                "decision": "promote",
                "reason": "issue_lint_clean",
            },
        )

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

    def test_follow_up_issue_publish_plan_handles_missing_evaluation_payload_without_crashing(
        self,
    ) -> None:
        findings_pack = {
            "schemaVersion": 1,
            "artifactType": "post_epic_findings_pack",
            "generatedAt": "2026-04-14T14:00:00Z",
            "sourceArtifact": {
                "artifactType": "post_epic_evaluation",
                "generatedAt": "2026-04-14T14:00:00Z",
                "evaluation": None,
                "target": {
                    "ref": "refs/heads/main",
                    "sha": "4444444444444444444444444444444444444444",
                },
            },
            "routing": {
                "lane": "meta",
                "sourceClassification": "meta_only",
                "excludeCurrentPrResiduals": True,
            },
            "actionableFindings": [
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
            "suppressedFindings": {
                "duplicates": [],
                "lowValue": [],
            },
            "summary": {
                "actionableFindingCount": 1,
                "suppressedDuplicateCount": 0,
                "suppressedLowValueCount": 0,
            },
        }

        publish_plan = build_post_epic_follow_up_issue_publish_plan(
            findings_pack,
            issue_lint_result={
                "executionReady": False,
                "missingRequired": ["summary"],
                "metadataErrors": [],
                "highRiskBlockingAmbiguity": None,
            },
        )

        self.assertEqual(
            publish_plan["draftIssue"]["dedupeKey"],
            "epic-follow-up:target:4444444444444444444444444444444444444444",
        )
        self.assertEqual(
            publish_plan["sourceArtifact"]["evaluation"],
            {},
        )
        self.assertEqual(
            publish_plan["promotion"],
            {
                "decision": "quarantine",
                "reason": "issue_lint_blocked",
            },
        )
        self.assertEqual(
            publish_plan["quarantine"],
            {
                "reason": "issue_lint_blocked",
                "blockingDetails": ["issue-lint missing required fields: summary"],
            },
        )

    def test_follow_up_issue_publish_plan_does_not_block_on_falsy_ambiguity_markers(
        self,
    ) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="5555555555555555555555555555555555555555",
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
            generated_at="2026-04-14T14:30:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        publish_plan = build_post_epic_follow_up_issue_publish_plan(
            findings_pack,
            issue_lint_result={
                "executionReady": True,
                "missingRequired": [],
                "metadataErrors": [],
                "highRiskBlockingAmbiguity": {},
            },
        )

        self.assertEqual(
            publish_plan["promotion"],
            {
                "decision": "promote",
                "reason": "issue_lint_clean",
            },
        )
        self.assertNotIn("quarantine", publish_plan)

    def test_findings_pack_can_render_versioned_roadmap_proposal_pack(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="6666666666666666666666666666666666666666",
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
            related_pull_requests=[
                PullRequestFact(
                    number=21,
                    title="Stabilize post-epic publish plan gating",
                    state="open",
                    merged=False,
                    target_branch="main",
                    merge_commit_sha=None,
                    pull_request_url="https://github.com/TommyKammy/AutomationPlus/pull/21",
                    source_issue_numbers=[8],
                ),
            ],
            generated_at="2026-04-15T02:00:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        proposal_pack = build_roadmap_proposal_pack(
            findings_pack,
            proposals=[
                {
                    "proposalKey": "phase-2-execution",
                    "title": "Phase 2 execution hardening and publish automation",
                    "summary": "Turn the post-epic continuity findings into a bounded next-phase plan.",
                    "goals": [
                        "Define planning-pack generation inputs from the proposal artifact.",
                        "Keep publication intent explicit for later issue and roadmap steps.",
                    ],
                    "constraints": [
                        "Do not publish issues directly from roadmap completion.",
                        "Do not validate planning DAGs in this phase proposal.",
                    ],
                    "candidateIssueTypes": ["planning_pack", "publish_plan"],
                    "publicationIntent": "planning_input",
                }
            ],
        )

        self.assertEqual(proposal_pack["schemaVersion"], 1)
        self.assertEqual(proposal_pack["artifactType"], "roadmap_proposal_pack")
        self.assertEqual(proposal_pack["sourceArtifact"]["artifactType"], "post_epic_findings_pack")
        self.assertEqual(proposal_pack["continuityContext"]["epic"]["issueNumber"], 1)
        self.assertEqual(
            proposal_pack["continuityContext"]["target"],
            {
                "ref": "refs/heads/main",
                "sha": "6666666666666666666666666666666666666666",
            },
        )
        self.assertEqual(proposal_pack["summary"]["proposalCount"], 1)
        self.assertEqual(
            proposal_pack["proposals"][0],
            {
                "proposalKey": "phase-2-execution",
                "title": "Phase 2 execution hardening and publish automation",
                "summary": "Turn the post-epic continuity findings into a bounded next-phase plan.",
                "goals": [
                    "Define planning-pack generation inputs from the proposal artifact.",
                    "Keep publication intent explicit for later issue and roadmap steps.",
                ],
                "constraints": [
                    "Do not publish issues directly from roadmap completion.",
                    "Do not validate planning DAGs in this phase proposal.",
                ],
                "candidateIssueTypes": ["planning_pack", "publish_plan"],
                "publicationIntent": "planning_input",
            },
        )

        with tempfile.TemporaryDirectory() as tempdir:
            output_path = Path(tempdir) / "roadmap-proposal-pack.json"
            persisted = write_roadmap_proposal_pack(
                output_path,
                findings_pack,
                proposals=[
                    {
                        "proposalKey": "phase-2-execution",
                        "title": "Phase 2 execution hardening and publish automation",
                        "summary": "Turn the post-epic continuity findings into a bounded next-phase plan.",
                        "goals": [
                            "Define planning-pack generation inputs from the proposal artifact.",
                            "Keep publication intent explicit for later issue and roadmap steps.",
                        ],
                        "constraints": [
                            "Do not publish issues directly from roadmap completion.",
                            "Do not validate planning DAGs in this phase proposal.",
                        ],
                        "candidateIssueTypes": ["planning_pack", "publish_plan"],
                        "publicationIntent": "planning_input",
                    }
                ],
            )
            on_disk = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted, proposal_pack)
        self.assertEqual(on_disk, proposal_pack)

    def test_roadmap_proposal_pack_rejects_schema_invalid_proposal_payload(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="7777777777777777777777777777777777777777",
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
            generated_at="2026-04-15T02:15:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))

        with self.assertRaisesRegex(
            ValueError,
            "proposal\\[0\\] missing required fields: goals",
        ):
            build_roadmap_proposal_pack(
                findings_pack,
                proposals=[
                    {
                        "proposalKey": "phase-2-execution",
                        "title": "Phase 2 execution hardening and publish automation",
                        "summary": "Turn the post-epic continuity findings into a bounded next-phase plan.",
                        "constraints": [
                            "Do not publish issues directly from roadmap completion."
                        ],
                        "candidateIssueTypes": ["planning_pack"],
                        "publicationIntent": "planning_input",
                    }
                ],
            )

    def test_roadmap_proposal_pack_rejects_non_list_proposals_payload(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="8888888888888888888888888888888888888888",
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
            generated_at="2026-04-15T02:30:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))

        with self.assertRaisesRegex(
            ValueError,
            "proposals must be a list of proposal objects",
        ):
            build_roadmap_proposal_pack(  # type: ignore[arg-type]
                findings_pack,
                proposals=None,
            )

    def test_roadmap_proposal_pack_emits_publishable_continuity_envelope(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="1212121212121212121212121212121212121212",
            target_ref="refs/heads/main",
            child_issues=[],
            generated_at="2026-04-15T02:45:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))

        proposal_pack = build_roadmap_proposal_pack(
            findings_pack,
            proposals=[
                {
                    "proposalKey": "phase-3-roadmap-continuity",
                    "title": "Phase 3 roadmap continuity envelope",
                    "summary": "Wrap proposal artifacts with explicit publish eligibility for roadmap continuity.",
                    "goals": [
                        "Carry explicit promotion state forward with the proposal output.",
                    ],
                    "constraints": [
                        "Only mark continuity output publishable when no operator review or drift gate blocks promotion.",
                    ],
                    "candidateIssueTypes": ["planning_pack"],
                    "publicationIntent": "planning_input",
                }
            ],
        )

        self.assertEqual(
            proposal_pack["continuityEnvelope"],
            {
                "promotionState": "publishable",
                "publishEligibility": {
                    "eligible": True,
                    "decision": "publishable",
                    "reasons": [
                        "proposal_pack_ready",
                        "no_actionable_findings",
                        "no_strategy_drift",
                        "operator_review_not_required",
                    ],
                },
                "strategyDrift": {
                    "status": "aligned",
                    "requiresReview": False,
                    "reasons": [],
                },
                "operatorReview": {
                    "status": "not_required",
                    "required": False,
                    "reasons": [],
                },
                "confidence": {
                    "level": "high",
                    "reasons": [
                        "findings_pack_contains_no_actionable_findings",
                        "proposal_pack_contains_validated_proposals",
                    ],
                },
            },
        )

    def test_planning_pack_emits_quarantined_continuity_envelope_when_strategy_drift_requires_review(
        self,
    ) -> None:
        proposal_pack = {
            "schemaVersion": 1,
            "artifactType": "roadmap_proposal_pack",
            "generatedAt": "2026-04-15T03:45:00Z",
            "sourceArtifact": {
                "artifactType": "post_epic_findings_pack",
                "generatedAt": "2026-04-15T03:30:00Z",
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
                    "sha": "3434343434343434343434343434343434343434",
                },
            },
            "continuityContext": {
                "epic": {
                    "issueNumber": 1,
                    "title": "Epic: Phase 1 foundations for AutomationPlus loop automation",
                    "issueUrl": "https://github.com/TommyKammy/AutomationPlus/issues/1",
                },
                "evaluationTrigger": "epic.completed",
                "target": {
                    "ref": "refs/heads/release-candidate",
                    "sha": "5656565656565656565656565656565656565656",
                },
                "actionableFindings": [
                    {
                        "dedupeKey": "target-mismatch",
                        "findingType": "strategy_drift_candidate",
                        "title": "Continuity target diverged from the evaluated epic target.",
                        "severity": "high",
                        "confidence": "high",
                        "novelty": "candidate",
                        "sourceClassification": "meta_only",
                        "evidence": {
                            "expectedTargetRef": "refs/heads/main",
                            "observedTargetRef": "refs/heads/release-candidate",
                        },
                    }
                ],
            },
            "proposals": [
                {
                    "proposalKey": "phase-3-roadmap-continuity",
                }
            ],
        }

        planning_pack = build_planning_pack(
            proposal_pack,
            plan_items=[
                {
                    "itemKey": "implement-envelope",
                    "proposalKey": "phase-3-roadmap-continuity",
                    "phase": "implementation",
                    "title": "Implement continuity envelope",
                    "summary": "Wrap continuity output with explicit publish gating metadata.",
                    "dependsOn": [],
                }
            ],
        )

        self.assertEqual(
            planning_pack["continuityEnvelope"],
            {
                "promotionState": "quarantined",
                "publishEligibility": {
                    "eligible": False,
                    "decision": "quarantined",
                    "reasons": [
                        "strategy_drift_detected",
                        "operator_review_required",
                    ],
                },
                "strategyDrift": {
                    "status": "drift_detected",
                    "requiresReview": True,
                    "reasons": [
                        "continuity_target_mismatch",
                    ],
                },
                "operatorReview": {
                    "status": "required",
                    "required": True,
                    "reasons": [
                        "strategy_drift_detected",
                    ],
                },
                "confidence": {
                    "level": "medium",
                    "reasons": [
                        "planning_pack_contains_validated_items",
                        "strategy_drift_signal_present",
                    ],
                },
            },
        )

    def test_roadmap_proposal_pack_can_render_planning_pack_with_execution_order(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="9999999999999999999999999999999999999999",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=42,
                    title="Build planning pack generation with DAG checker",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/42",
                ),
            ],
            generated_at="2026-04-15T03:00:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        proposal_pack = build_roadmap_proposal_pack(
            findings_pack,
            proposals=[
                {
                    "proposalKey": "phase-2-planning",
                    "title": "Phase 2 planning pack generation",
                    "summary": "Turn approved roadmap proposals into execution-ready planning artifacts.",
                    "goals": [
                        "Represent proposed work as explicit planning phases.",
                        "Preserve dependency metadata for downstream publish logic.",
                    ],
                    "constraints": [
                        "Reject malformed dependency graphs before publish.",
                        "Do not publish any GitHub artifacts from this planning step.",
                    ],
                    "candidateIssueTypes": ["planning_pack"],
                    "publicationIntent": "planning_input",
                }
            ],
        )

        planning_pack = build_planning_pack(
            proposal_pack,
            plan_items=[
                {
                    "itemKey": "capture-pack-shape",
                    "proposalKey": "phase-2-planning",
                    "phase": "design",
                    "title": "Capture planning-pack shape",
                    "summary": "Define the machine-readable planning artifact and source metadata.",
                    "dependsOn": [],
                },
                {
                    "itemKey": "validate-dag",
                    "proposalKey": "phase-2-planning",
                    "phase": "validation",
                    "title": "Validate planning DAGs",
                    "summary": "Reject cycles, missing parents, and malformed planning graphs.",
                    "dependsOn": ["capture-pack-shape"],
                },
                {
                    "itemKey": "persist-artifact",
                    "proposalKey": "phase-2-planning",
                    "phase": "delivery",
                    "title": "Persist planning pack artifact",
                    "summary": "Write the stable artifact only after the graph passes validation.",
                    "dependsOn": ["validate-dag"],
                },
            ],
        )

        self.assertEqual(planning_pack["schemaVersion"], 1)
        self.assertEqual(planning_pack["artifactType"], "planning_pack")
        self.assertEqual(
            planning_pack["sourceArtifact"],
            {
                "artifactType": "roadmap_proposal_pack",
                "generatedAt": "2026-04-15T03:00:00Z",
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
                    "sha": "9999999999999999999999999999999999999999",
                },
            },
        )
        self.assertEqual(
            planning_pack["graph"],
            {
                "roots": ["capture-pack-shape"],
                "executionOrder": [
                    "capture-pack-shape",
                    "validate-dag",
                    "persist-artifact",
                ],
            },
        )
        self.assertEqual(
            planning_pack["phases"],
            [
                {
                    "phase": "design",
                    "itemKeys": ["capture-pack-shape"],
                    "dependsOnPhases": [],
                },
                {
                    "phase": "validation",
                    "itemKeys": ["validate-dag"],
                    "dependsOnPhases": ["design"],
                },
                {
                    "phase": "delivery",
                    "itemKeys": ["persist-artifact"],
                    "dependsOnPhases": ["validation"],
                },
            ],
        )
        self.assertEqual(planning_pack["summary"]["itemCount"], 3)
        self.assertEqual(planning_pack["summary"]["phaseCount"], 3)
        self.assertEqual(planning_pack["summary"]["rootCount"], 1)

        with tempfile.TemporaryDirectory() as tempdir:
            output_path = Path(tempdir) / "planning-pack.json"
            persisted = write_planning_pack(
                output_path,
                proposal_pack,
                plan_items=[
                    {
                        "itemKey": "capture-pack-shape",
                        "proposalKey": "phase-2-planning",
                        "phase": "design",
                        "title": "Capture planning-pack shape",
                        "summary": "Define the machine-readable planning artifact and source metadata.",
                        "dependsOn": [],
                    },
                    {
                        "itemKey": "validate-dag",
                        "proposalKey": "phase-2-planning",
                        "phase": "validation",
                        "title": "Validate planning DAGs",
                        "summary": "Reject cycles, missing parents, and malformed planning graphs.",
                        "dependsOn": ["capture-pack-shape"],
                    },
                    {
                        "itemKey": "persist-artifact",
                        "proposalKey": "phase-2-planning",
                        "phase": "delivery",
                        "title": "Persist planning pack artifact",
                        "summary": "Write the stable artifact only after the graph passes validation.",
                        "dependsOn": ["validate-dag"],
                    },
                ],
            )
            on_disk = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted, planning_pack)
        self.assertEqual(on_disk, planning_pack)

    def test_planning_pack_merges_dependency_phases_across_multi_item_phase(self) -> None:
        proposal_pack = {
            "generatedAt": "2026-04-15T03:00:00Z",
            "proposals": [
                {
                    "proposalKey": "phase-2-planning",
                }
            ],
        }

        planning_pack = build_planning_pack(
            proposal_pack,
            plan_items=[
                {
                    "itemKey": "capture-pack-shape",
                    "proposalKey": "phase-2-planning",
                    "phase": "design",
                    "title": "Capture planning-pack shape",
                    "summary": "Define the machine-readable planning artifact and source metadata.",
                    "dependsOn": [],
                },
                {
                    "itemKey": "validate-dag",
                    "proposalKey": "phase-2-planning",
                    "phase": "validation",
                    "title": "Validate planning DAGs",
                    "summary": "Reject cycles and malformed graphs.",
                    "dependsOn": ["capture-pack-shape"],
                },
                {
                    "itemKey": "draft-implementation-plan",
                    "proposalKey": "phase-2-planning",
                    "phase": "execution",
                    "title": "Draft implementation plan",
                    "summary": "Lay out execution after the initial design is ready.",
                    "dependsOn": ["capture-pack-shape"],
                },
                {
                    "itemKey": "persist-artifact",
                    "proposalKey": "phase-2-planning",
                    "phase": "execution",
                    "title": "Persist planning pack artifact",
                    "summary": "Persist the plan after validation passes.",
                    "dependsOn": ["validate-dag"],
                },
            ],
        )

        self.assertEqual(
            planning_pack["phases"],
            [
                {
                    "phase": "design",
                    "itemKeys": ["capture-pack-shape"],
                    "dependsOnPhases": [],
                },
                {
                    "phase": "validation",
                    "itemKeys": ["validate-dag"],
                    "dependsOnPhases": ["design"],
                },
                {
                    "phase": "execution",
                    "itemKeys": [
                        "draft-implementation-plan",
                        "persist-artifact",
                    ],
                    "dependsOnPhases": ["design", "validation"],
                },
            ],
        )

    def test_planning_pack_rejects_missing_dependency_parent(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=42,
                    title="Build planning pack generation with DAG checker",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/42",
                ),
            ],
            generated_at="2026-04-15T03:15:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        proposal_pack = build_roadmap_proposal_pack(
            findings_pack,
            proposals=[
                {
                    "proposalKey": "phase-2-planning",
                    "title": "Phase 2 planning pack generation",
                    "summary": "Turn approved roadmap proposals into execution-ready planning artifacts.",
                    "goals": [
                        "Represent proposed work as explicit planning phases.",
                    ],
                    "constraints": [
                        "Reject malformed dependency graphs before publish.",
                    ],
                    "candidateIssueTypes": ["planning_pack"],
                    "publicationIntent": "planning_input",
                }
            ],
        )

        with self.assertRaisesRegex(
            ValueError,
            "plan_items\\[1\\] dependsOn references unknown itemKey: missing-parent",
        ):
            build_planning_pack(
                proposal_pack,
                plan_items=[
                    {
                        "itemKey": "capture-pack-shape",
                        "proposalKey": "phase-2-planning",
                        "phase": "design",
                        "title": "Capture planning-pack shape",
                        "summary": "Define the machine-readable planning artifact and source metadata.",
                        "dependsOn": [],
                    },
                    {
                        "itemKey": "validate-dag",
                        "proposalKey": "phase-2-planning",
                        "phase": "validation",
                        "title": "Validate planning DAGs",
                        "summary": "Reject cycles, missing parents, and malformed planning graphs.",
                        "dependsOn": ["missing-parent"],
                    },
                ],
            )

    def test_planning_pack_rejects_cycles(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=42,
                    title="Build planning pack generation with DAG checker",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/42",
                ),
            ],
            generated_at="2026-04-15T03:30:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        proposal_pack = build_roadmap_proposal_pack(
            findings_pack,
            proposals=[
                {
                    "proposalKey": "phase-2-planning",
                    "title": "Phase 2 planning pack generation",
                    "summary": "Turn approved roadmap proposals into execution-ready planning artifacts.",
                    "goals": [
                        "Represent proposed work as explicit planning phases.",
                    ],
                    "constraints": [
                        "Reject malformed dependency graphs before publish.",
                    ],
                    "candidateIssueTypes": ["planning_pack"],
                    "publicationIntent": "planning_input",
                }
            ],
        )

        with self.assertRaisesRegex(
            ValueError,
            "planning DAG contains a cycle",
        ):
            build_planning_pack(
                proposal_pack,
                plan_items=[
                    {
                        "itemKey": "capture-pack-shape",
                        "proposalKey": "phase-2-planning",
                        "phase": "design",
                        "title": "Capture planning-pack shape",
                        "summary": "Define the machine-readable planning artifact and source metadata.",
                        "dependsOn": ["validate-dag"],
                    },
                    {
                        "itemKey": "validate-dag",
                        "proposalKey": "phase-2-planning",
                        "phase": "validation",
                        "title": "Validate planning DAGs",
                        "summary": "Reject cycles, missing parents, and malformed planning graphs.",
                        "dependsOn": ["capture-pack-shape"],
                    },
                ],
            )

    def test_planning_pack_can_publish_bounded_roadmap_continuity_issue_set(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="cccccccccccccccccccccccccccccccccccccccc",
            target_ref="refs/heads/main",
            child_issues=[],
            generated_at="2026-04-15T04:00:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        proposal_pack = build_roadmap_proposal_pack(
            findings_pack,
            proposals=[
                {
                    "proposalKey": "phase-2-planning",
                    "title": "Phase 2 planning pack generation",
                    "summary": "Turn approved roadmap proposals into execution-ready planning artifacts.",
                    "goals": [
                        "Represent proposed work as explicit planning phases.",
                    ],
                    "constraints": [
                        "Reject malformed dependency graphs before publish.",
                    ],
                    "candidateIssueTypes": ["epic", "child"],
                    "publicationIntent": "issue_set_publish",
                }
            ],
        )
        planning_pack = build_planning_pack(
            proposal_pack,
            plan_items=[
                {
                    "itemKey": "capture-pack-shape",
                    "proposalKey": "phase-2-planning",
                    "phase": "design",
                    "title": "Capture planning-pack shape",
                    "summary": "Define the machine-readable planning artifact and source metadata.",
                    "dependsOn": [],
                },
                {
                    "itemKey": "validate-dag",
                    "proposalKey": "phase-2-planning",
                    "phase": "validation",
                    "title": "Validate planning DAGs",
                    "summary": "Reject cycles, missing parents, and malformed planning graphs.",
                    "dependsOn": ["capture-pack-shape"],
                },
            ],
        )

        publish_plan = build_roadmap_continuity_issue_set_publish_plan(
            planning_pack,
            publish_decisions={
                "roadmap": "publish",
                "epic:phase-2-planning": "publish",
                "child:capture-pack-shape": "publish",
                "child:validate-dag": "draft",
            },
            issue_lint_results={
                "roadmap": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
                "epic:phase-2-planning": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
                "child:capture-pack-shape": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
            },
        )

        self.assertEqual(
            publish_plan["artifactType"],
            "roadmap_continuity_issue_set_publish_plan",
        )
        self.assertEqual(
            publish_plan["summary"],
            {
                "issueCount": 4,
                "promotedCount": 3,
                "draftCount": 1,
                "quarantinedCount": 0,
            },
        )
        self.assertEqual(
            [item["publishKey"] for item in publish_plan["issueSet"]],
            [
                "roadmap",
                "epic:phase-2-planning",
                "child:capture-pack-shape",
                "child:validate-dag",
            ],
        )
        self.assertEqual(
            [item["promotion"]["decision"] for item in publish_plan["issueSet"]],
            ["promote", "promote", "promote", "draft"],
        )
        self.assertEqual(
            publish_plan["issueSet"][0]["draftIssue"]["labels"],
            ["codex", "roadmap-continuity", "roadmap"],
        )
        self.assertEqual(
            publish_plan["issueSet"][1]["draftIssue"]["canonicalMetadata"],
            {
                "issueType": "epic",
                "proposalKey": "phase-2-planning",
                "partOf": "roadmap",
                "dependsOn": [],
                "parallelizable": False,
                "executionOrder": "2 of 4",
            },
        )
        self.assertEqual(
            publish_plan["issueSet"][2]["draftIssue"]["canonicalMetadata"],
            {
                "issueType": "child",
                "proposalKey": "phase-2-planning",
                "itemKey": "capture-pack-shape",
                "partOf": "epic:phase-2-planning",
                "dependsOn": [],
                "parallelizable": False,
                "executionOrder": "3 of 4",
            },
        )
        self.assertIn("Part of: #1", publish_plan["issueSet"][0]["draftIssue"]["body"])
        self.assertIn(
            "## Execution order\n4 of 4",
            publish_plan["issueSet"][3]["draftIssue"]["body"],
        )

    def test_planning_pack_withholds_roadmap_continuity_issue_set_when_envelope_is_draft_only(
        self,
    ) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="dddddddddddddddddddddddddddddddddddddddd",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=42,
                    title="Build planning pack generation with DAG checker",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/42",
                ),
            ],
            generated_at="2026-04-15T04:15:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        proposal_pack = build_roadmap_proposal_pack(
            findings_pack,
            proposals=[
                {
                    "proposalKey": "phase-2-planning",
                    "title": "Phase 2 planning pack generation",
                    "summary": "Turn approved roadmap proposals into execution-ready planning artifacts.",
                    "goals": [
                        "Represent proposed work as explicit planning phases.",
                    ],
                    "constraints": [
                        "Reject malformed dependency graphs before publish.",
                    ],
                    "candidateIssueTypes": ["epic", "child"],
                    "publicationIntent": "issue_set_publish",
                }
            ],
        )
        planning_pack = build_planning_pack(
            proposal_pack,
            plan_items=[
                {
                    "itemKey": "capture-pack-shape",
                    "proposalKey": "phase-2-planning",
                    "phase": "design",
                    "title": "Capture planning-pack shape",
                    "summary": "Define the machine-readable planning artifact and source metadata.",
                    "dependsOn": [],
                }
            ],
        )

        publish_plan = build_roadmap_continuity_issue_set_publish_plan(
            planning_pack,
            publish_decisions={
                "roadmap": "publish",
                "epic:phase-2-planning": "publish",
                "child:capture-pack-shape": "publish",
            },
            issue_lint_results={
                "roadmap": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
                "epic:phase-2-planning": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
                "child:capture-pack-shape": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
            },
        )

        self.assertEqual(planning_pack["continuityEnvelope"]["promotionState"], "draft_only")
        self.assertEqual(
            [item["promotion"] for item in publish_plan["issueSet"]],
            [
                {"decision": "draft", "reason": "continuity_envelope_draft_only"},
                {"decision": "draft", "reason": "continuity_envelope_draft_only"},
                {"decision": "draft", "reason": "continuity_envelope_draft_only"},
            ],
        )
        self.assertEqual(
            publish_plan["summary"],
            {
                "issueCount": 3,
                "promotedCount": 0,
                "draftCount": 3,
                "quarantinedCount": 0,
            },
        )

    def test_continuity_pipeline_can_emit_approved_curated_note_patch_plan(self) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            target_ref="refs/heads/main",
            child_issues=[],
            generated_at="2026-04-15T04:30:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        proposal_pack = build_roadmap_proposal_pack(
            findings_pack,
            proposals=[
                {
                    "proposalKey": "phase-3-roadmap-continuity",
                    "title": "Phase 3 roadmap continuity envelope",
                    "summary": "Carry approved roadmap continuity outputs into bounded roadmap note updates.",
                    "goals": [
                        "Emit a first-class curated note patch plan from continuity artifacts.",
                    ],
                    "constraints": [
                        "Keep note updates bounded to curated roadmap note paths.",
                    ],
                    "candidateIssueTypes": ["epic", "child"],
                    "publicationIntent": "issue_set_publish",
                    "curatedNotePatches": [
                        {
                            "targetPath": "obsidian/roadmap/quarterly-plan.md",
                            "operation": "replace_text",
                            "matchText": "Status: Draft",
                            "replacementText": "Status: Confirmed",
                        }
                    ],
                }
            ],
        )
        planning_pack = build_planning_pack(
            proposal_pack,
            plan_items=[
                {
                    "itemKey": "capture-pack-shape",
                    "proposalKey": "phase-3-roadmap-continuity",
                    "phase": "design",
                    "title": "Capture planning-pack shape",
                    "summary": "Define the machine-readable planning artifact and source metadata.",
                    "dependsOn": [],
                }
            ],
        )
        publish_plan = build_roadmap_continuity_issue_set_publish_plan(
            planning_pack,
            publish_decisions={
                "roadmap": "publish",
                "epic:phase-3-roadmap-continuity": "publish",
                "child:capture-pack-shape": "publish",
            },
            issue_lint_results={
                "roadmap": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
                "epic:phase-3-roadmap-continuity": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
                "child:capture-pack-shape": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
            },
        )

        note_patch_plan = build_roadmap_continuity_note_patch_plan(
            planning_pack,
            issue_set_publish_plan=publish_plan,
        )

        self.assertEqual(
            note_patch_plan["artifactType"],
            "roadmap_continuity_note_patch_plan",
        )
        self.assertEqual(note_patch_plan["approval"]["status"], "approved")
        self.assertEqual(
            note_patch_plan["approval"]["reason"],
            "approved_continuity_artifacts_allow_note_updates",
        )
        self.assertEqual(
            note_patch_plan["patches"],
            [
                {
                    "targetPath": "obsidian/roadmap/quarterly-plan.md",
                    "operation": "replace_text",
                    "matchText": "Status: Draft",
                    "replacementText": "Status: Confirmed",
                }
            ],
        )
        self.assertEqual(
            note_patch_plan["summary"],
            {
                "proposalCount": 1,
                "proposedPatchCount": 1,
                "approvedPatchCount": 1,
                "withheldPatchCount": 0,
            },
        )

    def test_continuity_pipeline_withholds_note_patch_plan_when_continuity_or_publish_state_disallows_updates(
        self,
    ) -> None:
        job = PostEpicEvaluationJob(
            repository_full_name="TommyKammy/AutomationPlus",
            epic_issue_number=1,
            epic_issue_title="Epic: Phase 1 foundations for AutomationPlus loop automation",
            epic_issue_url="https://github.com/TommyKammy/AutomationPlus/issues/1",
            evaluation_trigger="epic.completed",
            target_sha="ffffffffffffffffffffffffffffffffffffffff",
            target_ref="refs/heads/main",
            child_issues=[
                EpicChildIssueState(
                    issue_number=42,
                    title="Build planning pack generation with DAG checker",
                    state="open",
                    conclusion="not_completed",
                    issue_url="https://github.com/TommyKammy/AutomationPlus/issues/42",
                ),
            ],
            generated_at="2026-04-15T04:45:00Z",
        )

        findings_pack = build_post_epic_findings_pack(evaluate_completed_epic(job))
        proposal_pack = build_roadmap_proposal_pack(
            findings_pack,
            proposals=[
                {
                    "proposalKey": "phase-3-roadmap-continuity",
                    "title": "Phase 3 roadmap continuity envelope",
                    "summary": "Carry approved roadmap continuity outputs into bounded roadmap note updates.",
                    "goals": [
                        "Emit a first-class curated note patch plan from continuity artifacts.",
                    ],
                    "constraints": [
                        "Keep note updates bounded to curated roadmap note paths.",
                    ],
                    "candidateIssueTypes": ["epic", "child"],
                    "publicationIntent": "issue_set_publish",
                    "curatedNotePatches": [
                        {
                            "targetPath": "obsidian/roadmap/quarterly-plan.md",
                            "operation": "replace_text",
                            "matchText": "Status: Draft",
                            "replacementText": "Status: Confirmed",
                        }
                    ],
                }
            ],
        )
        planning_pack = build_planning_pack(
            proposal_pack,
            plan_items=[
                {
                    "itemKey": "capture-pack-shape",
                    "proposalKey": "phase-3-roadmap-continuity",
                    "phase": "design",
                    "title": "Capture planning-pack shape",
                    "summary": "Define the machine-readable planning artifact and source metadata.",
                    "dependsOn": [],
                }
            ],
        )
        publish_plan = build_roadmap_continuity_issue_set_publish_plan(
            planning_pack,
            publish_decisions={
                "roadmap": "publish",
                "epic:phase-3-roadmap-continuity": "publish",
                "child:capture-pack-shape": "publish",
            },
            issue_lint_results={
                "roadmap": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
                "epic:phase-3-roadmap-continuity": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
                "child:capture-pack-shape": {
                    "executionReady": True,
                    "missingRequired": [],
                    "metadataErrors": [],
                    "highRiskBlockingAmbiguity": None,
                },
            },
        )

        note_patch_plan = build_roadmap_continuity_note_patch_plan(
            planning_pack,
            issue_set_publish_plan=publish_plan,
        )

        self.assertEqual(planning_pack["continuityEnvelope"]["promotionState"], "draft_only")
        self.assertEqual(note_patch_plan["approval"]["status"], "withheld")
        self.assertEqual(
            note_patch_plan["approval"]["reason"],
            "continuity_promotion_state_not_publishable",
        )
        self.assertEqual(note_patch_plan["patches"], [])
        self.assertEqual(
            note_patch_plan["summary"],
            {
                "proposalCount": 1,
                "proposedPatchCount": 1,
                "approvedPatchCount": 0,
                "withheldPatchCount": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()

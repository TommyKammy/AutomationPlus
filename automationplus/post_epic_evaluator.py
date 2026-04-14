import copy
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class EpicChildIssueState:
    issue_number: int
    title: str
    state: str
    conclusion: str
    issue_url: str


@dataclass(frozen=True)
class PullRequestFact:
    number: int
    title: str
    state: str
    merged: bool
    target_branch: str
    merge_commit_sha: Optional[str]
    pull_request_url: str
    source_issue_numbers: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class PostEpicEvaluationJob:
    repository_full_name: str
    epic_issue_number: int
    epic_issue_title: str
    epic_issue_url: str
    evaluation_trigger: str
    target_sha: str
    target_ref: str
    child_issues: list[EpicChildIssueState] = field(default_factory=list)
    related_pull_requests: list[PullRequestFact] = field(default_factory=list)
    generated_at: Optional[str] = None


def evaluate_completed_epic(job: PostEpicEvaluationJob) -> dict[str, Any]:
    completed_child_issue_count = sum(
        1 for issue in job.child_issues if issue.conclusion == "completed"
    )
    merged_pull_request_count = sum(1 for pr in job.related_pull_requests if pr.merged)

    return {
        "schemaVersion": 1,
        "artifactType": "post_epic_evaluation",
        "generatedAt": job.generated_at,
        "evaluation": {
            "trigger": job.evaluation_trigger,
            "epic": {
                "issueNumber": job.epic_issue_number,
                "title": job.epic_issue_title,
                "issueUrl": job.epic_issue_url,
            },
        },
        "routing": {
            "lane": "meta",
            "interferesWithActivePrExecution": False,
        },
        "repository": {
            "fullName": job.repository_full_name,
        },
        "target": {
            "ref": job.target_ref,
            "sha": job.target_sha,
        },
        "sourceContext": {
            "childIssues": [
                {
                    "issueNumber": issue.issue_number,
                    "title": issue.title,
                    "state": issue.state,
                    "conclusion": issue.conclusion,
                    "issueUrl": issue.issue_url,
                }
                for issue in job.child_issues
            ],
            "relatedPullRequests": [
                {
                    "number": pr.number,
                    "title": pr.title,
                    "state": pr.state,
                    "merged": pr.merged,
                    "targetBranch": pr.target_branch,
                    "mergeCommitSha": pr.merge_commit_sha,
                    "pullRequestUrl": pr.pull_request_url,
                    "sourceIssueNumbers": list(pr.source_issue_numbers),
                }
                for pr in job.related_pull_requests
            ],
        },
        "summary": {
            "childIssueCount": len(job.child_issues),
            "completedChildIssueCount": completed_child_issue_count,
            "relatedPullRequestCount": len(job.related_pull_requests),
            "mergedPullRequestCount": merged_pull_request_count,
        },
    }


def write_post_epic_evaluation_artifact(
    output_path: Path,
    job: PostEpicEvaluationJob,
) -> dict[str, Any]:
    artifact = evaluate_completed_epic(job)
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(output_path.parent),
        delete=False,
    ) as handle:
        json.dump(artifact, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)

    temp_path.replace(output_path)
    return artifact


def _base_finding(
    *,
    dedupe_key: str,
    finding_type: str,
    title: str,
    severity: str,
    confidence: str,
    novelty: str,
    source_classification: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "dedupeKey": dedupe_key,
        "findingType": finding_type,
        "title": title,
        "severity": severity,
        "confidence": confidence,
        "novelty": novelty,
        "sourceClassification": source_classification,
        "evidence": evidence,
    }


def build_post_epic_findings_pack(evaluation_artifact: dict[str, Any]) -> dict[str, Any]:
    generated_at = evaluation_artifact.get("generatedAt")
    target = copy.deepcopy(evaluation_artifact.get("target"))
    evaluation = copy.deepcopy(evaluation_artifact.get("evaluation"))
    source_context = copy.deepcopy(evaluation_artifact.get("sourceContext", {}))
    child_issues = source_context.get("childIssues", [])
    related_pull_requests = source_context.get("relatedPullRequests", [])

    actionable_findings: list[dict[str, Any]] = []
    duplicate_findings: list[dict[str, Any]] = []
    low_value_findings: list[dict[str, Any]] = []
    actionable_dedupe_keys: set[str] = set()
    low_value_dedupe_keys: set[str] = set()

    for issue in child_issues:
        issue_number = issue.get("issueNumber")
        title = issue.get("title")
        conclusion = issue.get("conclusion")
        state = issue.get("state")
        issue_url = issue.get("issueUrl")

        if conclusion == "completed":
            dedupe_key = f"child-issue:{issue_number}:completed"
            finding = _base_finding(
                dedupe_key=dedupe_key,
                finding_type="completed_child_issue",
                title=f"Completed child issue remains meta-only context: #{issue_number} {title}",
                severity="low",
                confidence="high",
                novelty="routine",
                source_classification="meta_only",
                evidence={
                    "issueNumber": issue_number,
                    "issueUrl": issue_url,
                    "state": state,
                    "conclusion": conclusion,
                },
            )
            if dedupe_key in low_value_dedupe_keys:
                duplicate_findings.append({**finding, "duplicateOf": dedupe_key})
                continue
            low_value_dedupe_keys.add(dedupe_key)
            low_value_findings.append(finding)
            continue

        dedupe_key = f"child-issue:{issue_number}:{conclusion or 'unknown'}"
        finding = _base_finding(
            dedupe_key=dedupe_key,
            finding_type="child_issue_follow_up_candidate",
            title=f"Child issue requires follow-up after epic close: #{issue_number} {title}",
            severity="medium",
            confidence="high",
            novelty="candidate",
            source_classification="meta_only",
            evidence={
                "issueNumber": issue_number,
                "issueUrl": issue_url,
                "state": state,
                "conclusion": conclusion,
            },
        )
        if dedupe_key in actionable_dedupe_keys:
            duplicate_findings.append({**finding, "duplicateOf": dedupe_key})
            continue
        actionable_dedupe_keys.add(dedupe_key)
        actionable_findings.append(finding)

    for pull_request in related_pull_requests:
        pr_number = pull_request.get("number")
        pr_title = pull_request.get("title")
        pr_state = pull_request.get("state")
        pr_merged = bool(pull_request.get("merged"))
        pr_url = pull_request.get("pullRequestUrl")

        if pr_merged:
            dedupe_key = f"pull-request:{pr_number}:merged"
            finding = _base_finding(
                dedupe_key=dedupe_key,
                finding_type="merged_pull_request_context",
                title=f"Merged pull request remains low-value meta context: #{pr_number} {pr_title}",
                severity="low",
                confidence="high",
                novelty="routine",
                source_classification="meta_only",
                evidence={
                    "pullRequestNumber": pr_number,
                    "pullRequestUrl": pr_url,
                    "state": pr_state,
                    "merged": pr_merged,
                },
            )
            if dedupe_key in low_value_dedupe_keys:
                duplicate_findings.append({**finding, "duplicateOf": dedupe_key})
                continue
            low_value_dedupe_keys.add(dedupe_key)
            low_value_findings.append(finding)
            continue

        dedupe_key = f"pull-request:{pr_number}:unmerged"
        finding = _base_finding(
            dedupe_key=dedupe_key,
            finding_type="pull_request_follow_up_candidate",
            title=f"Related pull request still requires follow-up after epic close: #{pr_number} {pr_title}",
            severity="medium",
            confidence="medium",
            novelty="candidate",
            source_classification="meta_only",
            evidence={
                "pullRequestNumber": pr_number,
                "pullRequestUrl": pr_url,
                "state": pr_state,
                "merged": pr_merged,
            },
        )
        if dedupe_key in actionable_dedupe_keys:
            duplicate_findings.append({**finding, "duplicateOf": dedupe_key})
            continue
        actionable_dedupe_keys.add(dedupe_key)
        actionable_findings.append(finding)

    return {
        "schemaVersion": 1,
        "artifactType": "post_epic_findings_pack",
        "generatedAt": generated_at,
        "sourceArtifact": {
            "artifactType": evaluation_artifact.get("artifactType"),
            "generatedAt": generated_at,
            "evaluation": evaluation,
            "target": target,
        },
        "routing": {
            "lane": "meta",
            "sourceClassification": "meta_only",
            "excludeCurrentPrResiduals": True,
        },
        "actionableFindings": actionable_findings,
        "suppressedFindings": {
            "duplicates": duplicate_findings,
            "lowValue": low_value_findings,
        },
        "summary": {
            "actionableFindingCount": len(actionable_findings),
            "suppressedDuplicateCount": len(duplicate_findings),
            "suppressedLowValueCount": len(low_value_findings),
        },
    }


def _follow_up_issue_dedupe_key(findings_pack: dict[str, Any]) -> str:
    epic = findings_pack.get("sourceArtifact", {}).get("evaluation", {}).get("epic")
    if isinstance(epic, dict) and epic.get("issueNumber") is not None:
        return f"epic-follow-up:{epic['issueNumber']}"

    target = findings_pack.get("sourceArtifact", {}).get("target", {})
    target_sha = target.get("sha", "unknown")
    return f"epic-follow-up:target:{target_sha}"


def _render_post_epic_follow_up_issue_body(
    *,
    epic_issue_number: Any,
    epic_title: str,
) -> str:
    return "\n".join(
        [
            "## Summary",
            f"Follow up on remaining post-epic work for #{epic_issue_number} {epic_title}.",
            "",
            "## Scope",
            "- convert the post-epic findings pack into one execution-ready follow-up issue",
            "- keep current-PR local-review residual routing excluded from this publish path",
            "- carry forward actionable meta-only follow-up findings without silent promotion",
            "",
            "## Acceptance criteria",
            "- the generated follow-up issue remains template-clean for codex-supervisor issue-lint",
            "- the publish plan promotes only when issue-lint is execution-ready and free of blocking ambiguity",
            "- unsafe or duplicate drafts are quarantined instead of being promoted",
            "",
            "## Verification",
            "- python3 -m unittest tests.test_post_epic_evaluator",
            "- review the generated issue body for required sections and scheduling metadata",
            "",
            f"Part of: #{epic_issue_number}",
            "Depends on: none",
            "Parallelizable: No",
            "",
            "## Execution order",
            "1 of 1",
        ]
    )


def _build_follow_up_draft_issue(
    findings_pack: dict[str, Any],
    source_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    evaluation = findings_pack.get("sourceArtifact", {}).get("evaluation", {})
    epic = evaluation.get("epic", {})
    epic_issue_number = epic.get("issueNumber")
    epic_title = epic.get("title", "Post-epic follow-up")

    return {
        "dedupeKey": _follow_up_issue_dedupe_key(findings_pack),
        "title": f"Post-epic follow-up for #{epic_issue_number} {epic_title}",
        "body": _render_post_epic_follow_up_issue_body(
            epic_issue_number=epic_issue_number,
            epic_title=epic_title,
        ),
        "labels": ["codex", "post-epic-follow-up"],
        "state": "draft",
        "findingCount": len(source_findings),
    }


def _issue_lint_blocking_details(issue_lint_result: dict[str, Any]) -> list[str]:
    details: list[str] = []
    missing_required = issue_lint_result.get("missingRequired", [])
    metadata_errors = issue_lint_result.get("metadataErrors", [])
    ambiguity = issue_lint_result.get("highRiskBlockingAmbiguity")

    if missing_required:
        details.append(
            "issue-lint missing required fields: " + ", ".join(str(item) for item in missing_required)
        )
    if metadata_errors:
        details.append(
            "issue-lint metadata errors: " + "; ".join(str(item) for item in metadata_errors)
        )
    if ambiguity:
        details.append(f"issue-lint blocking ambiguity: {ambiguity}")

    return details


def build_post_epic_follow_up_issue_publish_plan(
    findings_pack: dict[str, Any],
    *,
    issue_lint_result: Optional[dict[str, Any]] = None,
    existing_draft_keys: Optional[list[str]] = None,
) -> dict[str, Any]:
    source_findings = copy.deepcopy(findings_pack.get("actionableFindings", []))
    routing = copy.deepcopy(findings_pack.get("routing", {}))
    evaluation = copy.deepcopy(findings_pack.get("sourceArtifact", {}).get("evaluation", {}))
    epic = evaluation.get("epic", {})
    existing_draft_keys = existing_draft_keys or []
    draft_issue = _build_follow_up_draft_issue(findings_pack, source_findings)
    dedupe_key = draft_issue["dedupeKey"]

    quarantine_reason: Optional[str] = None
    blocking_details: list[str] = []

    if not source_findings:
        quarantine_reason = "no_actionable_findings"
        blocking_details.append("findings pack did not contain actionable follow-up findings")
    elif routing.get("excludeCurrentPrResiduals") is not True:
        quarantine_reason = "unsafe_routing"
        blocking_details.append("publish path must exclude current-PR residual routing")
    elif routing.get("sourceClassification") != "meta_only":
        quarantine_reason = "unsafe_routing"
        blocking_details.append("publish path only supports meta-only findings packs")
    elif dedupe_key in existing_draft_keys:
        quarantine_reason = "duplicate_draft"
        blocking_details.append(f"draft already exists for dedupe key {dedupe_key}")
    elif issue_lint_result is None:
        quarantine_reason = "issue_lint_missing"
        blocking_details.append("publish plan requires an issue-lint result before promotion")
    else:
        lint_missing_required = bool(issue_lint_result.get("missingRequired"))
        lint_metadata_errors = bool(issue_lint_result.get("metadataErrors"))
        lint_has_blocking_ambiguity = issue_lint_result.get("highRiskBlockingAmbiguity") not in (
            None,
            False,
        )

        if (
            not issue_lint_result.get("executionReady", False)
            or lint_has_blocking_ambiguity
            or lint_missing_required
            or lint_metadata_errors
        ):
            quarantine_reason = "issue_lint_blocked"
            blocking_details.extend(
                _issue_lint_blocking_details(issue_lint_result)
                or ["issue-lint did not mark this draft safe to promote"]
            )

    promotion = (
        {"decision": "quarantine", "reason": quarantine_reason}
        if quarantine_reason is not None
        else {"decision": "promote", "reason": "issue_lint_clean"}
    )

    publish_plan = {
        "schemaVersion": 1,
        "artifactType": "post_epic_follow_up_issue_publish_plan",
        "generatedAt": findings_pack.get("generatedAt"),
        "sourceArtifact": {
            "artifactType": findings_pack.get("artifactType"),
            "generatedAt": findings_pack.get("generatedAt"),
            "evaluation": evaluation,
            "target": copy.deepcopy(findings_pack.get("sourceArtifact", {}).get("target")),
        },
        "routing": {
            "lane": routing.get("lane", "meta"),
            "sourceClassification": routing.get("sourceClassification", "meta_only"),
            "excludeCurrentPrResiduals": routing.get("excludeCurrentPrResiduals", True),
            "publishTarget": "post_epic_follow_up",
        },
        "draftIssue": draft_issue,
        "sourceFindings": source_findings,
        "promotion": promotion,
    }

    if quarantine_reason is not None:
        publish_plan["quarantine"] = {
            "reason": quarantine_reason,
            "blockingDetails": blocking_details,
        }

    return publish_plan


def write_post_epic_follow_up_issue_publish_plan(
    output_path: Path,
    findings_pack: dict[str, Any],
    *,
    issue_lint_result: Optional[dict[str, Any]] = None,
    existing_draft_keys: Optional[list[str]] = None,
) -> dict[str, Any]:
    publish_plan = build_post_epic_follow_up_issue_publish_plan(
        findings_pack,
        issue_lint_result=issue_lint_result,
        existing_draft_keys=existing_draft_keys,
    )
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(output_path.parent),
        delete=False,
    ) as handle:
        json.dump(publish_plan, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)

    temp_path.replace(output_path)
    return publish_plan


def write_post_epic_findings_pack(
    output_path: Path,
    evaluation_artifact: dict[str, Any],
) -> dict[str, Any]:
    findings_pack = build_post_epic_findings_pack(evaluation_artifact)
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(output_path.parent),
        delete=False,
    ) as handle:
        json.dump(findings_pack, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)

    temp_path.replace(output_path)
    return findings_pack

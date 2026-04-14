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

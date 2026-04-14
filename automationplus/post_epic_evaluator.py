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

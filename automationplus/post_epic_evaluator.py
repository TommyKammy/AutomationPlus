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
    source_artifact = findings_pack.get("sourceArtifact")
    if not isinstance(source_artifact, dict):
        source_artifact = {}

    evaluation = source_artifact.get("evaluation")
    if not isinstance(evaluation, dict):
        evaluation = {}

    epic = evaluation.get("epic")
    if isinstance(epic, dict) and epic.get("issueNumber") is not None:
        return f"epic-follow-up:{epic['issueNumber']}"

    target = source_artifact.get("target")
    if not isinstance(target, dict):
        target = {}

    target_sha = target.get("sha", "unknown")
    return f"epic-follow-up:target:{target_sha}"


def _render_post_epic_follow_up_issue_body(
    *,
    epic_issue_number: Any,
    epic_title: str,
    source_findings: list[Any],
) -> str:
    findings_lines = ["## Findings to carry forward"]
    for finding in source_findings:
        if not isinstance(finding, dict):
            findings_lines.append("- Follow-up finding (malformed entry)")
            continue

        raw_title = finding.get("title")
        title = str(raw_title).strip() if raw_title is not None else ""
        if not title:
            title = "Follow-up finding"
        evidence = finding.get("evidence")
        reference = None
        if isinstance(evidence, dict):
            if evidence.get("issueNumber") is not None:
                reference = f"issue #{evidence['issueNumber']}"
            elif evidence.get("pullRequestNumber") is not None:
                reference = f"PR #{evidence['pullRequestNumber']}"

        findings_lines.append(
            f"- {title}" if reference is None else f"- {title} ({reference})"
        )

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
            *findings_lines,
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
    source_artifact = findings_pack.get("sourceArtifact")
    if not isinstance(source_artifact, dict):
        source_artifact = {}

    evaluation = source_artifact.get("evaluation")
    if not isinstance(evaluation, dict):
        evaluation = {}

    epic = evaluation.get("epic", {})
    epic_issue_number = epic.get("issueNumber")
    epic_title = epic.get("title", "Post-epic follow-up")

    return {
        "dedupeKey": _follow_up_issue_dedupe_key(findings_pack),
        "title": f"Post-epic follow-up for #{epic_issue_number} {epic_title}",
        "body": _render_post_epic_follow_up_issue_body(
            epic_issue_number=epic_issue_number,
            epic_title=epic_title,
            source_findings=source_findings,
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
    source_artifact = findings_pack.get("sourceArtifact")
    if not isinstance(source_artifact, dict):
        source_artifact = {}

    evaluation = source_artifact.get("evaluation")
    if not isinstance(evaluation, dict):
        evaluation = {}
    evaluation = copy.deepcopy(evaluation)

    target = source_artifact.get("target")
    if isinstance(target, dict):
        target = copy.deepcopy(target)
    else:
        target = None

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
        lint_has_blocking_ambiguity = bool(issue_lint_result.get("highRiskBlockingAmbiguity"))

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
            "target": target,
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


def _normalized_string_list(value: Any) -> Optional[list[str]]:
    if not isinstance(value, list):
        return None

    normalized: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            return None
        stripped = entry.strip()
        if not stripped:
            return None
        normalized.append(stripped)

    return normalized


def _validate_roadmap_proposal(index: int, proposal: Any) -> dict[str, Any]:
    if not isinstance(proposal, dict):
        raise ValueError(f"proposal[{index}] must be an object")

    required_fields = [
        "proposalKey",
        "title",
        "summary",
        "goals",
        "constraints",
        "candidateIssueTypes",
        "publicationIntent",
    ]
    missing_fields = [field for field in required_fields if field not in proposal]
    if missing_fields:
        raise ValueError(
            f"proposal[{index}] missing required fields: {', '.join(missing_fields)}"
        )

    normalized: dict[str, Any] = {}
    for field_name in ("proposalKey", "title", "summary", "publicationIntent"):
        raw_value = proposal.get(field_name)
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"proposal[{index}].{field_name} must be a non-empty string")
        normalized[field_name] = raw_value.strip()

    for field_name in ("goals", "constraints", "candidateIssueTypes"):
        normalized_list = _normalized_string_list(proposal.get(field_name))
        if normalized_list is None or not normalized_list:
            raise ValueError(
                f"proposal[{index}].{field_name} must be a non-empty list of non-empty strings"
            )
        normalized[field_name] = normalized_list

    return normalized


def build_roadmap_proposal_pack(
    findings_pack: dict[str, Any],
    *,
    proposals: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(proposals, list):
        raise ValueError("proposals must be a list of proposal objects")

    source_artifact = findings_pack.get("sourceArtifact")
    if not isinstance(source_artifact, dict):
        source_artifact = {}

    evaluation = source_artifact.get("evaluation")
    if not isinstance(evaluation, dict):
        evaluation = {}
    evaluation = copy.deepcopy(evaluation)

    epic = evaluation.get("epic")
    if not isinstance(epic, dict):
        epic = {}
    epic = copy.deepcopy(epic)

    target = source_artifact.get("target")
    if not isinstance(target, dict):
        target = {}
    target = copy.deepcopy(target)

    actionable_findings = findings_pack.get("actionableFindings")
    if not isinstance(actionable_findings, list):
        actionable_findings = []
    actionable_findings = copy.deepcopy(actionable_findings)

    validated_proposals = [
        _validate_roadmap_proposal(index, proposal)
        for index, proposal in enumerate(proposals)
    ]

    return {
        "schemaVersion": 1,
        "artifactType": "roadmap_proposal_pack",
        "generatedAt": findings_pack.get("generatedAt"),
        "sourceArtifact": {
            "artifactType": findings_pack.get("artifactType"),
            "generatedAt": findings_pack.get("generatedAt"),
            "evaluation": evaluation,
            "target": target,
        },
        "continuityContext": {
            "epic": epic,
            "evaluationTrigger": evaluation.get("trigger"),
            "target": target,
            "actionableFindings": actionable_findings,
        },
        "proposals": validated_proposals,
        "summary": {
            "proposalCount": len(validated_proposals),
            "actionableFindingCount": len(actionable_findings),
        },
    }


def write_roadmap_proposal_pack(
    output_path: Path,
    findings_pack: dict[str, Any],
    *,
    proposals: list[dict[str, Any]],
) -> dict[str, Any]:
    proposal_pack = build_roadmap_proposal_pack(
        findings_pack,
        proposals=proposals,
    )
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(output_path.parent),
        delete=False,
    ) as handle:
        json.dump(proposal_pack, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)

    temp_path.replace(output_path)
    return proposal_pack


def _validate_planning_item(
    index: int,
    plan_item: Any,
    *,
    proposal_keys: set[str],
) -> dict[str, Any]:
    if not isinstance(plan_item, dict):
        raise ValueError(f"plan_items[{index}] must be an object")

    required_fields = [
        "itemKey",
        "proposalKey",
        "phase",
        "title",
        "summary",
        "dependsOn",
    ]
    missing_fields = [field for field in required_fields if field not in plan_item]
    if missing_fields:
        raise ValueError(
            f"plan_items[{index}] missing required fields: {', '.join(missing_fields)}"
        )

    normalized: dict[str, Any] = {}
    for field_name in ("itemKey", "proposalKey", "phase", "title", "summary"):
        raw_value = plan_item.get(field_name)
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(
                f"plan_items[{index}].{field_name} must be a non-empty string"
            )
        normalized[field_name] = raw_value.strip()

    if normalized["proposalKey"] not in proposal_keys:
        raise ValueError(
            f"plan_items[{index}].proposalKey references unknown proposalKey: {normalized['proposalKey']}"
        )

    normalized_depends_on = _normalized_string_list(plan_item.get("dependsOn"))
    if normalized_depends_on is None:
        raise ValueError(
            f"plan_items[{index}].dependsOn must be a list of non-empty strings"
        )
    normalized["dependsOn"] = normalized_depends_on

    return normalized


def _topological_execution_order(plan_items: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    item_keys = [item["itemKey"] for item in plan_items]
    known_keys = set(item_keys)
    dependency_counts: dict[str, int] = {}
    dependents: dict[str, list[str]] = {item_key: [] for item_key in item_keys}
    roots: list[str] = []

    for index, item in enumerate(plan_items):
        item_key = item["itemKey"]
        dependencies = item["dependsOn"]
        dependency_counts[item_key] = len(dependencies)

        for dependency_key in dependencies:
            if dependency_key not in known_keys:
                raise ValueError(
                    f"plan_items[{index}] dependsOn references unknown itemKey: {dependency_key}"
                )
            if dependency_key == item_key:
                raise ValueError(
                    f"plan_items[{index}] dependsOn cannot reference its own itemKey: {item_key}"
                )
            dependents[dependency_key].append(item_key)

        if not dependencies:
            roots.append(item_key)

    ready = list(roots)
    execution_order: list[str] = []

    while ready:
        current = ready.pop(0)
        execution_order.append(current)

        for dependent_key in dependents[current]:
            dependency_counts[dependent_key] -= 1
            if dependency_counts[dependent_key] == 0:
                ready.append(dependent_key)

    if len(execution_order) != len(plan_items):
        raise ValueError("planning DAG contains a cycle")

    return roots, execution_order


def build_planning_pack(
    proposal_pack: dict[str, Any],
    *,
    plan_items: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(plan_items, list):
        raise ValueError("plan_items must be a list of plan item objects")

    proposals = proposal_pack.get("proposals")
    if not isinstance(proposals, list):
        proposals = []

    proposal_keys = {
        proposal.get("proposalKey")
        for proposal in proposals
        if isinstance(proposal, dict) and isinstance(proposal.get("proposalKey"), str)
    }
    validated_plan_items = [
        _validate_planning_item(index, plan_item, proposal_keys=proposal_keys)
        for index, plan_item in enumerate(plan_items)
    ]

    seen_item_keys: set[str] = set()
    for index, item in enumerate(validated_plan_items):
        item_key = item["itemKey"]
        if item_key in seen_item_keys:
            raise ValueError(f"plan_items[{index}].itemKey must be unique: {item_key}")
        seen_item_keys.add(item_key)

    roots, execution_order = _topological_execution_order(validated_plan_items)
    execution_index_by_key = {
        item_key: execution_index for execution_index, item_key in enumerate(execution_order)
    }
    item_by_key = {item["itemKey"]: item for item in validated_plan_items}

    ordered_plan_items = []
    for item_key in execution_order:
        item = copy.deepcopy(item_by_key[item_key])
        item["executionIndex"] = execution_index_by_key[item_key]
        ordered_plan_items.append(item)

    phases: list[dict[str, Any]] = []
    for item_key in execution_order:
        item = item_by_key[item_key]
        phase_name = item["phase"]
        if phases and phases[-1]["phase"] == phase_name:
            phases[-1]["itemKeys"].append(item_key)
            continue

        depends_on_phases: list[str] = []
        for dependency_key in item["dependsOn"]:
            dependency_phase = item_by_key[dependency_key]["phase"]
            if dependency_phase != phase_name and dependency_phase not in depends_on_phases:
                depends_on_phases.append(dependency_phase)

        phases.append(
            {
                "phase": phase_name,
                "itemKeys": [item_key],
                "dependsOnPhases": depends_on_phases,
            }
        )

    source_artifact = proposal_pack.get("sourceArtifact")
    if not isinstance(source_artifact, dict):
        source_artifact = {}

    source_evaluation = source_artifact.get("evaluation")
    if not isinstance(source_evaluation, dict):
        source_evaluation = {}

    source_target = source_artifact.get("target")
    if not isinstance(source_target, dict):
        source_target = {}

    return {
        "schemaVersion": 1,
        "artifactType": "planning_pack",
        "generatedAt": proposal_pack.get("generatedAt"),
        "sourceArtifact": {
            "artifactType": proposal_pack.get("artifactType"),
            "generatedAt": proposal_pack.get("generatedAt"),
            "evaluation": copy.deepcopy(source_evaluation),
            "target": copy.deepcopy(source_target),
        },
        "continuityContext": copy.deepcopy(proposal_pack.get("continuityContext", {})),
        "proposals": copy.deepcopy(proposals),
        "planItems": ordered_plan_items,
        "graph": {
            "roots": roots,
            "executionOrder": execution_order,
        },
        "phases": phases,
        "summary": {
            "proposalCount": len(proposals),
            "itemCount": len(ordered_plan_items),
            "phaseCount": len(phases),
            "rootCount": len(roots),
        },
    }


def write_planning_pack(
    output_path: Path,
    proposal_pack: dict[str, Any],
    *,
    plan_items: list[dict[str, Any]],
) -> dict[str, Any]:
    planning_pack = build_planning_pack(
        proposal_pack,
        plan_items=plan_items,
    )
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(output_path.parent),
        delete=False,
    ) as handle:
        json.dump(planning_pack, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)

    temp_path.replace(output_path)
    return planning_pack

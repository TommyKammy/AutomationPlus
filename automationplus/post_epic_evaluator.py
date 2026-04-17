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


ROADMAP_CONTINUITY_CURATED_NOTE_PATCH_ROOT = "obsidian/roadmap/"
ROADMAP_CONTINUITY_CURATED_NOTE_PATCH_OPERATION = "replace_text"


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

    curated_note_patches = proposal.get("curatedNotePatches")
    if curated_note_patches is not None:
        if not isinstance(curated_note_patches, list) or not curated_note_patches:
            raise ValueError(
                f"proposal[{index}].curatedNotePatches must be a non-empty list of patch objects"
            )
        normalized["curatedNotePatches"] = [
            _validate_curated_note_patch(index, patch_index, patch)
            for patch_index, patch in enumerate(curated_note_patches)
        ]

    return normalized


def _validate_curated_note_patch(
    proposal_index: int,
    patch_index: int,
    patch: Any,
) -> dict[str, str]:
    if not isinstance(patch, dict):
        raise ValueError(
            f"proposal[{proposal_index}].curatedNotePatches[{patch_index}] must be an object"
        )

    required_fields = [
        "targetPath",
        "operation",
        "matchText",
        "replacementText",
    ]
    missing_fields = [field for field in required_fields if field not in patch]
    if missing_fields:
        raise ValueError(
            "proposal"
            f"[{proposal_index}].curatedNotePatches[{patch_index}] missing required fields: "
            + ", ".join(missing_fields)
        )

    normalized: dict[str, str] = {}
    for field_name in required_fields:
        raw_value = patch.get(field_name)
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(
                "proposal"
                f"[{proposal_index}].curatedNotePatches[{patch_index}].{field_name} "
                "must be a non-empty string"
            )
        normalized[field_name] = raw_value.strip()

    if normalized["operation"] != ROADMAP_CONTINUITY_CURATED_NOTE_PATCH_OPERATION:
        raise ValueError(
            "proposal"
            f"[{proposal_index}].curatedNotePatches[{patch_index}].operation must be "
            f"{ROADMAP_CONTINUITY_CURATED_NOTE_PATCH_OPERATION}"
        )

    target_path = normalized["targetPath"]
    if Path(target_path).is_absolute() or ".." in Path(target_path).parts:
        raise ValueError(
            "proposal"
            f"[{proposal_index}].curatedNotePatches[{patch_index}].targetPath must remain "
            "relative to the curated roadmap root"
        )
    if not target_path.startswith(ROADMAP_CONTINUITY_CURATED_NOTE_PATCH_ROOT):
        raise ValueError(
            "proposal"
            f"[{proposal_index}].curatedNotePatches[{patch_index}].targetPath must stay under "
            f"{ROADMAP_CONTINUITY_CURATED_NOTE_PATCH_ROOT}"
        )

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
    continuity_context = {
        "epic": epic,
        "evaluationTrigger": evaluation.get("trigger"),
        "target": target,
        "actionableFindings": actionable_findings,
    }
    continuity_envelope = _build_continuity_envelope(
        source_artifact={
            "artifactType": findings_pack.get("artifactType"),
            "generatedAt": findings_pack.get("generatedAt"),
            "target": target,
        },
        continuity_context=continuity_context,
        artifact_ready_reason="proposal_pack_ready",
        validated_signal_reason="proposal_pack_contains_validated_proposals",
        has_validated_signal=bool(validated_proposals),
    )

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
        "continuityContext": continuity_context,
        "continuityEnvelope": continuity_envelope,
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
    phase_by_name: dict[str, dict[str, Any]] = {}
    for item_key in execution_order:
        item = item_by_key[item_key]
        phase_name = item["phase"]
        phase_entry = phase_by_name.get(phase_name)
        if phase_entry is None:
            phase_entry = {
                "phase": phase_name,
                "itemKeys": [],
                "dependsOnPhases": [],
            }
            phase_by_name[phase_name] = phase_entry
            phases.append(phase_entry)

        phase_entry["itemKeys"].append(item_key)

        for dependency_key in item["dependsOn"]:
            dependency_phase = item_by_key[dependency_key]["phase"]
            if (
                dependency_phase != phase_name
                and dependency_phase not in phase_entry["dependsOnPhases"]
            ):
                phase_entry["dependsOnPhases"].append(dependency_phase)

    source_artifact = proposal_pack.get("sourceArtifact")
    if not isinstance(source_artifact, dict):
        source_artifact = {}

    source_evaluation = source_artifact.get("evaluation")
    if not isinstance(source_evaluation, dict):
        source_evaluation = {}

    source_target = source_artifact.get("target")
    if not isinstance(source_target, dict):
        source_target = {}
    continuity_context = copy.deepcopy(proposal_pack.get("continuityContext", {}))
    continuity_envelope = _build_continuity_envelope(
        source_artifact={
            "artifactType": proposal_pack.get("artifactType"),
            "generatedAt": proposal_pack.get("generatedAt"),
            "target": copy.deepcopy(source_target),
        },
        continuity_context=continuity_context,
        artifact_ready_reason="planning_pack_ready",
        validated_signal_reason="planning_pack_contains_validated_items",
        has_validated_signal=bool(ordered_plan_items),
    )

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
        "continuityContext": continuity_context,
        "continuityEnvelope": continuity_envelope,
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


def _roadmap_issue_set_blocking_details(issue_lint_result: dict[str, Any]) -> list[str]:
    return _issue_lint_blocking_details(issue_lint_result)


def _render_issue_set_issue_body(
    *,
    issue_type: str,
    title: str,
    summary: str,
    scope: list[str],
    acceptance_criteria: list[str],
    verification: list[str],
    part_of: str,
    depends_on: list[str],
    execution_order: str,
) -> str:
    depends_on_value = ", ".join(depends_on) if depends_on else "none"
    return "\n".join(
        [
            "## Summary",
            summary,
            "",
            "## Scope",
            *[f"- {item}" for item in scope],
            "",
            "## Acceptance criteria",
            *[f"- {item}" for item in acceptance_criteria],
            "",
            "## Verification",
            *[f"- {item}" for item in verification],
            "",
            f"Part of: {part_of}",
            f"Depends on: {depends_on_value}",
            "Parallelizable: No",
            "",
            "## Execution order",
            execution_order,
        ]
    )


def _build_roadmap_continuity_roadmap_issue(
    *,
    epic_issue_number: Any,
    epic_title: str,
    proposals: list[dict[str, Any]],
    issue_count: int,
) -> dict[str, Any]:
    title = f"Roadmap continuity for #{epic_issue_number} {epic_title}"
    return {
        "dedupeKey": f"roadmap-continuity:roadmap:{epic_issue_number}",
        "title": title,
        "body": _render_issue_set_issue_body(
            issue_type="roadmap",
            title=title,
            summary=(
                f"Publish a bounded roadmap continuity issue set for #{epic_issue_number} "
                f"{epic_title}."
            ),
            scope=[
                "promote only the explicitly approved roadmap continuity issues",
                "keep roadmap continuity publication bounded to one roadmap issue, one Epic per proposal, and one child issue per planning item",
                *[
                    f"include proposal {proposal['proposalKey']}: {proposal['title']}"
                    for proposal in proposals
                ],
            ],
            acceptance_criteria=[
                "the roadmap continuity issue set remains bounded and label-aware",
                "all promoted issues preserve canonical codex metadata for later issue-lint and loop execution",
                "issues without explicit publish approval stop at draft or quarantine instead of promoting",
            ],
            verification=[
                "python3 -m unittest tests.test_post_epic_evaluator -v",
                "review the generated roadmap, Epic, and child issue metadata for canonical scheduling fields",
            ],
            part_of=f"#{epic_issue_number}",
            depends_on=[],
            execution_order=f"1 of {issue_count}",
        ),
        "labels": ["codex", "roadmap-continuity", "roadmap"],
        "state": "draft",
        "canonicalMetadata": {
            "issueType": "roadmap",
            "partOf": f"#{epic_issue_number}",
            "dependsOn": [],
            "parallelizable": False,
            "executionOrder": f"1 of {issue_count}",
        },
    }


def _build_roadmap_continuity_epic_issue(
    *,
    epic_issue_number: Any,
    proposal: dict[str, Any],
    execution_order: str,
) -> dict[str, Any]:
    title = f"Epic: {proposal['title']}"
    return {
        "dedupeKey": (
            f"roadmap-continuity:epic:{epic_issue_number}:{proposal['proposalKey']}"
        ),
        "title": title,
        "body": _render_issue_set_issue_body(
            issue_type="epic",
            title=title,
            summary=proposal["summary"],
            scope=[
                *proposal["goals"],
                *proposal["constraints"],
            ],
            acceptance_criteria=[
                "the Epic retains roadmap continuity labels and canonical metadata",
                "the Epic only promotes when the continuity envelope and issue-lint both allow publication",
                "the Epic remains bounded to child issues derived from the planning pack",
            ],
            verification=[
                "python3 -m unittest tests.test_post_epic_evaluator -v",
                "review the generated Epic issue body and canonical metadata",
            ],
            part_of="roadmap",
            depends_on=[],
            execution_order=execution_order,
        ),
        "labels": ["codex", "roadmap-continuity", "epic"],
        "state": "draft",
        "canonicalMetadata": {
            "issueType": "epic",
            "proposalKey": proposal["proposalKey"],
            "partOf": "roadmap",
            "dependsOn": [],
            "parallelizable": False,
            "executionOrder": execution_order,
        },
    }


def _build_roadmap_continuity_child_issue(
    *,
    item: dict[str, Any],
    execution_order: str,
) -> dict[str, Any]:
    depends_on = [f"child:{dependency_key}" for dependency_key in item["dependsOn"]]
    return {
        "dedupeKey": f"roadmap-continuity:child:{item['itemKey']}",
        "title": item["title"],
        "body": _render_issue_set_issue_body(
            issue_type="child",
            title=item["title"],
            summary=item["summary"],
            scope=[
                f"deliver planning phase {item['phase']} for proposal {item['proposalKey']}",
                "preserve canonical metadata for downstream issue-lint and loop execution",
                "respect explicit publish gates from the continuity envelope and publish decisions",
            ],
            acceptance_criteria=[
                "the child issue remains label-aware and bounded to the planning pack item",
                "the child issue records canonical dependency metadata for loop-safe execution",
                "the child issue only promotes when publish gates are explicitly satisfied",
            ],
            verification=[
                "python3 -m unittest tests.test_post_epic_evaluator -v",
                "review the generated child issue metadata and dependency chain",
            ],
            part_of=f"epic:{item['proposalKey']}",
            depends_on=depends_on,
            execution_order=execution_order,
        ),
        "labels": ["codex", "roadmap-continuity", "child"],
        "state": "draft",
        "canonicalMetadata": {
            "issueType": "child",
            "proposalKey": item["proposalKey"],
            "itemKey": item["itemKey"],
            "partOf": f"epic:{item['proposalKey']}",
            "dependsOn": depends_on,
            "parallelizable": False,
            "executionOrder": execution_order,
        },
    }


def build_roadmap_continuity_issue_set_publish_plan(
    planning_pack: dict[str, Any],
    *,
    publish_decisions: dict[str, str],
    issue_lint_results: Optional[dict[str, dict[str, Any]]] = None,
    existing_draft_keys: Optional[list[str]] = None,
) -> dict[str, Any]:
    issue_lint_results = issue_lint_results or {}
    existing_draft_keys = existing_draft_keys or []

    source_artifact = planning_pack.get("sourceArtifact")
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

    continuity_envelope = planning_pack.get("continuityEnvelope")
    if not isinstance(continuity_envelope, dict):
        continuity_envelope = {}

    promotion_state = continuity_envelope.get("promotionState")

    proposals = planning_pack.get("proposals")
    if not isinstance(proposals, list):
        proposals = []
    proposals = [copy.deepcopy(proposal) for proposal in proposals if isinstance(proposal, dict)]

    plan_items = planning_pack.get("planItems")
    if not isinstance(plan_items, list):
        plan_items = []
    plan_items = [copy.deepcopy(item) for item in plan_items if isinstance(item, dict)]

    total_issue_count = 1 + len(proposals) + len(plan_items)

    issue_set: list[dict[str, Any]] = []
    issue_set.append(
        {
            "publishKey": "roadmap",
            "issueType": "roadmap",
            "draftIssue": _build_roadmap_continuity_roadmap_issue(
                epic_issue_number=epic.get("issueNumber"),
                epic_title=epic.get("title", "Roadmap continuity"),
                proposals=proposals,
                issue_count=total_issue_count,
            ),
        }
    )

    for proposal_index, proposal in enumerate(proposals, start=2):
        issue_set.append(
            {
                "publishKey": f"epic:{proposal['proposalKey']}",
                "issueType": "epic",
                "draftIssue": _build_roadmap_continuity_epic_issue(
                    epic_issue_number=epic.get("issueNumber"),
                    proposal=proposal,
                    execution_order=f"{proposal_index} of {total_issue_count}",
                ),
            }
        )

    child_start_index = 1 + len(proposals)
    for item_index, item in enumerate(plan_items, start=child_start_index + 1):
        issue_set.append(
            {
                "publishKey": f"child:{item['itemKey']}",
                "issueType": "child",
                "draftIssue": _build_roadmap_continuity_child_issue(
                    item=item,
                    execution_order=f"{item_index} of {total_issue_count}",
                ),
            }
        )

    valid_decisions = {"publish", "draft", "quarantine"}
    promoted_count = 0
    draft_count = 0
    quarantined_count = 0

    for entry in issue_set:
        publish_key = entry["publishKey"]
        draft_issue = entry["draftIssue"]
        decision = publish_decisions.get(publish_key, "draft")
        if decision not in valid_decisions:
            raise ValueError(
                f"publish_decisions[{publish_key}] must be one of: draft, publish, quarantine"
            )

        dedupe_key = draft_issue["dedupeKey"]
        if dedupe_key in existing_draft_keys:
            entry["promotion"] = {"decision": "quarantine", "reason": "duplicate_draft"}
            entry["quarantine"] = {
                "reason": "duplicate_draft",
                "blockingDetails": [
                    f"draft already exists for dedupe key {dedupe_key}"
                ],
            }
            quarantined_count += 1
            continue

        if promotion_state == "quarantined":
            entry["promotion"] = {
                "decision": "quarantine",
                "reason": "continuity_envelope_quarantined",
            }
            entry["quarantine"] = {
                "reason": "continuity_envelope_quarantined",
                "blockingDetails": list(
                    continuity_envelope.get("publishEligibility", {}).get("reasons", [])
                )
                or ["continuity envelope quarantined issue publication"],
            }
            quarantined_count += 1
            continue

        if promotion_state == "draft_only":
            entry["promotion"] = {
                "decision": "draft",
                "reason": "continuity_envelope_draft_only",
            }
            draft_count += 1
            continue

        if decision == "quarantine":
            entry["promotion"] = {"decision": "quarantine", "reason": "publish_withheld"}
            entry["quarantine"] = {
                "reason": "publish_withheld",
                "blockingDetails": ["publish decision explicitly withheld promotion"],
            }
            quarantined_count += 1
            continue

        if decision == "draft":
            entry["promotion"] = {"decision": "draft", "reason": "explicit_draft"}
            draft_count += 1
            continue

        issue_lint_result = issue_lint_results.get(publish_key)
        if issue_lint_result is None:
            entry["promotion"] = {"decision": "quarantine", "reason": "issue_lint_missing"}
            entry["quarantine"] = {
                "reason": "issue_lint_missing",
                "blockingDetails": [
                    "publish plan requires an issue-lint result before promotion"
                ],
            }
            quarantined_count += 1
            continue

        lint_missing_required = bool(issue_lint_result.get("missingRequired"))
        lint_metadata_errors = bool(issue_lint_result.get("metadataErrors"))
        lint_has_blocking_ambiguity = bool(
            issue_lint_result.get("highRiskBlockingAmbiguity")
        )
        if (
            not issue_lint_result.get("executionReady", False)
            or lint_missing_required
            or lint_metadata_errors
            or lint_has_blocking_ambiguity
        ):
            entry["promotion"] = {"decision": "quarantine", "reason": "issue_lint_blocked"}
            entry["quarantine"] = {
                "reason": "issue_lint_blocked",
                "blockingDetails": _roadmap_issue_set_blocking_details(issue_lint_result)
                or ["issue-lint did not mark this draft safe to promote"],
            }
            quarantined_count += 1
            continue

        entry["promotion"] = {"decision": "promote", "reason": "issue_lint_clean"}
        promoted_count += 1

    return {
        "schemaVersion": 1,
        "artifactType": "roadmap_continuity_issue_set_publish_plan",
        "generatedAt": planning_pack.get("generatedAt"),
        "sourceArtifact": {
            "artifactType": planning_pack.get("artifactType"),
            "generatedAt": planning_pack.get("generatedAt"),
            "evaluation": evaluation,
            "target": copy.deepcopy(source_artifact.get("target")),
        },
        "routing": {
            "lane": "meta",
            "publishTarget": "roadmap_continuity_issue_set",
        },
        "continuityEnvelope": copy.deepcopy(continuity_envelope),
        "issueSet": issue_set,
        "summary": {
            "issueCount": len(issue_set),
            "promotedCount": promoted_count,
            "draftCount": draft_count,
            "quarantinedCount": quarantined_count,
        },
    }


def build_roadmap_continuity_note_patch_plan(
    planning_pack: dict[str, Any],
    *,
    issue_set_publish_plan: dict[str, Any],
) -> dict[str, Any]:
    continuity_envelope = planning_pack.get("continuityEnvelope")
    if not isinstance(continuity_envelope, dict):
        continuity_envelope = {}

    proposals = planning_pack.get("proposals")
    if not isinstance(proposals, list):
        proposals = []
    proposals = [copy.deepcopy(proposal) for proposal in proposals if isinstance(proposal, dict)]

    plan_items = planning_pack.get("planItems")
    if not isinstance(plan_items, list):
        plan_items = []
    plan_items = [copy.deepcopy(item) for item in plan_items if isinstance(item, dict)]

    issue_set = issue_set_publish_plan.get("issueSet")
    if not isinstance(issue_set, list):
        issue_set = []

    promotion_by_publish_key: dict[str, str] = {}
    child_promotions_by_proposal_key: dict[str, list[str]] = {}
    for entry in issue_set:
        if not isinstance(entry, dict):
            continue
        publish_key = entry.get("publishKey")
        promotion = entry.get("promotion")
        if not isinstance(publish_key, str) or not isinstance(promotion, dict):
            continue
        decision = promotion.get("decision")
        if isinstance(decision, str):
            promotion_by_publish_key[publish_key] = decision
            if publish_key.startswith("child:"):
                draft_issue = entry.get("draftIssue")
                canonical_metadata = (
                    draft_issue.get("canonicalMetadata")
                    if isinstance(draft_issue, dict)
                    else None
                )
                proposal_key = (
                    canonical_metadata.get("proposalKey")
                    if isinstance(canonical_metadata, dict)
                    else None
                )
                if isinstance(proposal_key, str) and proposal_key:
                    child_promotions_by_proposal_key.setdefault(proposal_key, []).append(decision)

    proposed_patch_count = 0
    approved_patches: list[dict[str, str]] = []
    withheld_reason = "no_curated_note_patches_proposed"

    required_child_count_by_proposal_key: dict[str, int] = {}
    for item in plan_items:
        proposal_key = item.get("proposalKey")
        item_key = item.get("itemKey")
        if not isinstance(proposal_key, str) or not proposal_key:
            continue
        if not isinstance(item_key, str) or not item_key:
            continue
        required_child_count_by_proposal_key[proposal_key] = (
            required_child_count_by_proposal_key.get(proposal_key, 0) + 1
        )

    curated_note_patch_sets: list[tuple[str, str, list[dict[str, str]]]] = []
    for proposal in proposals:
        curated_note_patches = proposal.get("curatedNotePatches")
        if not isinstance(curated_note_patches, list) or not curated_note_patches:
            continue
        proposed_patch_count += len(curated_note_patches)
        proposal_key = proposal.get("proposalKey")
        if isinstance(proposal_key, str) and proposal_key:
            curated_note_patch_sets.append(
                (
                    proposal_key,
                    f"epic:{proposal_key}",
                    copy.deepcopy(curated_note_patches),
                )
            )

    if continuity_envelope.get("promotionState") != "publishable":
        withheld_reason = "continuity_promotion_state_not_publishable"
    elif promotion_by_publish_key.get("roadmap") != "promote":
        withheld_reason = "roadmap_issue_not_approved_for_note_updates"
    else:
        for proposal_key, publish_key, curated_note_patches in curated_note_patch_sets:
            if promotion_by_publish_key.get(publish_key) != "promote":
                withheld_reason = "proposal_issue_not_approved_for_note_updates"
                approved_patches = []
                break

            required_child_count = required_child_count_by_proposal_key.get(proposal_key, 0)
            child_promotions = child_promotions_by_proposal_key.get(proposal_key, [])
            if required_child_count and (
                len(child_promotions) != required_child_count
                or any(decision != "promote" for decision in child_promotions)
            ):
                withheld_reason = "child_issue_not_approved_for_note_updates"
                approved_patches = []
                break

            approved_patches.extend(copy.deepcopy(curated_note_patches))

        if approved_patches:
            withheld_reason = "approved_continuity_artifacts_allow_note_updates"

    approved_patch_count = len(approved_patches)
    withheld_patch_count = proposed_patch_count - approved_patch_count

    source_artifact = planning_pack.get("sourceArtifact")
    if not isinstance(source_artifact, dict):
        source_artifact = {}

    note_patch_plan = {
        "schemaVersion": 1,
        "artifactType": "roadmap_continuity_note_patch_plan",
        "generatedAt": planning_pack.get("generatedAt"),
        "sourceArtifacts": {
            "planningPack": {
                "artifactType": planning_pack.get("artifactType"),
                "generatedAt": planning_pack.get("generatedAt"),
                "target": copy.deepcopy(source_artifact.get("target")),
            },
            "issueSetPublishPlan": {
                "artifactType": issue_set_publish_plan.get("artifactType"),
                "generatedAt": issue_set_publish_plan.get("generatedAt"),
            },
        },
        "routing": {
            "lane": "meta",
            "publishTarget": "roadmap_continuity_note_patch",
        },
        "continuityEnvelope": copy.deepcopy(continuity_envelope),
        "approval": {
            "status": "approved" if approved_patches else "withheld",
            "reason": withheld_reason,
        },
        "patches": approved_patches,
        "summary": {
            "proposalCount": len(proposals),
            "proposedPatchCount": proposed_patch_count,
            "approvedPatchCount": approved_patch_count,
            "withheldPatchCount": withheld_patch_count,
        },
    }
    return note_patch_plan


def _build_continuity_envelope(
    *,
    source_artifact: dict[str, Any],
    continuity_context: dict[str, Any],
    artifact_ready_reason: str,
    validated_signal_reason: str,
    has_validated_signal: bool,
) -> dict[str, Any]:
    actionable_findings = continuity_context.get("actionableFindings")
    if not isinstance(actionable_findings, list):
        actionable_findings = []

    source_target = source_artifact.get("target")
    if not isinstance(source_target, dict):
        source_target = {}

    continuity_target = continuity_context.get("target")
    if not isinstance(continuity_target, dict):
        continuity_target = {}

    drift_reasons: list[str] = []
    if (
        isinstance(source_target.get("ref"), str)
        and isinstance(continuity_target.get("ref"), str)
        and source_target.get("ref") != continuity_target.get("ref")
    ):
        drift_reasons.append("continuity_target_mismatch")
    if (
        isinstance(source_target.get("sha"), str)
        and isinstance(continuity_target.get("sha"), str)
        and source_target.get("sha") != continuity_target.get("sha")
        and "continuity_target_mismatch" not in drift_reasons
    ):
        drift_reasons.append("continuity_target_mismatch")

    for finding in actionable_findings:
        if not isinstance(finding, dict):
            continue
        finding_type = finding.get("findingType")
        title = finding.get("title")
        if (
            finding_type == "strategy_drift_candidate"
            or (isinstance(title, str) and "drift" in title.lower())
        ) and "continuity_target_mismatch" not in drift_reasons:
            drift_reasons.append("continuity_target_mismatch")

    strategy_drift_detected = bool(drift_reasons)
    strategy_drift = {
        "status": "drift_detected" if strategy_drift_detected else "aligned",
        "requiresReview": strategy_drift_detected,
        "reasons": drift_reasons,
    }

    operator_review = {
        "status": "required" if strategy_drift_detected else "not_required",
        "required": strategy_drift_detected,
        "reasons": ["strategy_drift_detected"] if strategy_drift_detected else [],
    }

    if strategy_drift_detected:
        promotion_state = "quarantined"
        publish_eligibility = {
            "eligible": False,
            "decision": "quarantined",
            "reasons": [
                "strategy_drift_detected",
                "operator_review_required",
            ],
        }
    else:
        publish_eligibility = {
            "eligible": True,
            "decision": "publishable",
            "reasons": [
                artifact_ready_reason,
            ],
        }
        if actionable_findings:
            publish_eligibility["reasons"].append("actionable_findings_require_follow_up")
        else:
            publish_eligibility["reasons"].append("no_actionable_findings")
        publish_eligibility["reasons"].extend(
            ["no_strategy_drift", "operator_review_not_required"]
        )
        promotion_state = "publishable" if not actionable_findings else "draft_only"
        if promotion_state == "draft_only":
            publish_eligibility["eligible"] = False
            publish_eligibility["decision"] = "draft_only"

    confidence_reasons: list[str] = []
    if not actionable_findings:
        confidence_reasons.append("findings_pack_contains_no_actionable_findings")
    if has_validated_signal:
        confidence_reasons.append(validated_signal_reason)
    if strategy_drift_detected:
        confidence_reasons.append("strategy_drift_signal_present")

    confidence_level = "medium" if strategy_drift_detected or actionable_findings else "high"

    return {
        "promotionState": promotion_state,
        "publishEligibility": publish_eligibility,
        "strategyDrift": strategy_drift,
        "operatorReview": operator_review,
        "confidence": {
            "level": confidence_level,
            "reasons": confidence_reasons,
        },
    }

import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import automationplus.obsidian_sync as obsidian_sync


class ObsidianGeneratedSyncTests(unittest.TestCase):
    def _healthy_loop_status(self) -> dict:
        return {
            "status": "healthy",
            "failurePolicy": {
                "degradedState": "healthy",
                "operatorHold": False,
            },
        }

    def test_generated_sync_writes_allowed_note_under_generated_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            output_path = (
                workspace
                / ".codex-supervisor"
                / "generated"
                / "obsidian"
                / "notes"
                / "daily"
                / "summary.md"
            )

            artifact = obsidian_sync.write_generated_note_sync(
                workspace_root=workspace,
                vault_root=workspace,
                output_path=output_path,
                content="# Daily Summary\n\nGenerated content.\n",
                loop_status_payload=self._healthy_loop_status(),
                generated_at="2026-04-15T10:00:00Z",
            )

            self.assertEqual(artifact["decision"]["status"], "written")
            self.assertEqual(artifact["decision"]["reasonCode"], "allowed_generated_path")
            self.assertEqual(output_path.read_text(encoding="utf-8"), "# Daily Summary\n\nGenerated content.\n")
            self.assertEqual(
                artifact["policy"]["allowedRoots"],
                [
                    ".codex-supervisor/generated/obsidian/notes",
                    "obsidian/generated",
                ],
            )

    def test_generated_sync_anchors_relative_output_paths_to_vault_root(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            relative_output_path = Path("obsidian") / "generated" / "daily" / "summary.md"
            expected_output_path = workspace / relative_output_path

            artifact = obsidian_sync.write_generated_note_sync(
                workspace_root=workspace,
                vault_root=workspace,
                output_path=relative_output_path,
                content="# Daily Summary\n\nGenerated content.\n",
                loop_status_payload=self._healthy_loop_status(),
                generated_at="2026-04-15T10:00:00Z",
            )

            self.assertEqual(artifact["decision"]["status"], "written")
            self.assertEqual(expected_output_path.read_text(encoding="utf-8"), "# Daily Summary\n\nGenerated content.\n")
            self.assertEqual(artifact["requestedPath"], "obsidian/generated/daily/summary.md")
            self.assertEqual(artifact["paths"]["outputPath"], str(expected_output_path.resolve()))

    def test_generated_sync_blocks_paths_outside_generated_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            blocked_path = workspace / "Notes" / "Project.md"

            artifact = obsidian_sync.write_generated_note_sync(
                workspace_root=workspace,
                vault_root=workspace,
                output_path=blocked_path,
                content="# Unsafe overwrite\n",
                loop_status_payload=self._healthy_loop_status(),
                generated_at="2026-04-15T10:00:00Z",
            )

            self.assertEqual(artifact["decision"]["status"], "blocked")
            self.assertEqual(artifact["decision"]["reasonCode"], "generated_path_not_allowed")
            self.assertFalse(blocked_path.exists())
            quarantine_path = workspace / ".codex-supervisor" / "generated" / "obsidian" / "quarantine.json"
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
            self.assertEqual(quarantine["decision"]["reasonCode"], "generated_path_not_allowed")
            self.assertEqual(quarantine["requestedPath"], "Notes/Project.md")

    def test_generated_sync_skips_writes_when_loop_is_degraded_or_on_hold(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            output_path = (
                workspace
                / ".codex-supervisor"
                / "generated"
                / "obsidian"
                / "notes"
                / "daily"
                / "summary.md"
            )

            artifact = obsidian_sync.write_generated_note_sync(
                workspace_root=workspace,
                vault_root=workspace,
                output_path=output_path,
                content="# Daily Summary\n\nGenerated content.\n",
                loop_status_payload={
                    "status": "degraded",
                    "failurePolicy": {
                        "degradedState": "repeated-failure",
                        "operatorHold": True,
                    },
                },
                generated_at="2026-04-15T10:00:00Z",
            )

            self.assertEqual(artifact["decision"]["status"], "skipped")
            self.assertEqual(artifact["decision"]["reasonCode"], "service_not_safe_for_generated_sync")
            self.assertFalse(output_path.exists())
            quarantine_path = workspace / ".codex-supervisor" / "generated" / "obsidian" / "quarantine.json"
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
            self.assertEqual(quarantine["decision"]["status"], "skipped")
            self.assertEqual(quarantine["serviceState"]["status"], "degraded")

    def test_generated_sync_skips_writes_when_operator_hold_flag_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            output_path = (
                workspace
                / ".codex-supervisor"
                / "generated"
                / "obsidian"
                / "notes"
                / "daily"
                / "summary.md"
            )

            artifact = obsidian_sync.write_generated_note_sync(
                workspace_root=workspace,
                vault_root=workspace,
                output_path=output_path,
                content="# Daily Summary\n\nGenerated content.\n",
                loop_status_payload={
                    "status": "healthy",
                    "failurePolicy": {
                        "degradedState": "healthy",
                    },
                },
                generated_at="2026-04-15T10:00:00Z",
            )

            self.assertEqual(artifact["decision"]["status"], "skipped")
            self.assertEqual(artifact["decision"]["reasonCode"], "service_not_safe_for_generated_sync")
            self.assertFalse(output_path.exists())
            quarantine_path = workspace / ".codex-supervisor" / "generated" / "obsidian" / "quarantine.json"
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
            self.assertEqual(quarantine["decision"]["status"], "skipped")
            self.assertIsNone(quarantine["serviceState"]["failurePolicy"].get("operatorHold"))

    def test_generated_sync_blocks_symlink_swap_after_policy_check(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            output_dir = workspace / "obsidian" / "generated" / "daily"
            output_dir.mkdir(parents=True)
            output_path = output_dir / "summary.md"
            escaped_dir = workspace / "Notes"
            escaped_dir.mkdir()
            original_path_allowed = obsidian_sync._path_allowed

            def swap_allowed_directory(candidate: Path, vault_root: Path) -> bool:
                allowed = original_path_allowed(candidate, vault_root)
                moved_dir = workspace / "daily-original"
                output_dir.rename(moved_dir)
                os.symlink(escaped_dir, output_dir, target_is_directory=True)
                return allowed

            with patch("automationplus.obsidian_sync._path_allowed", side_effect=swap_allowed_directory):
                artifact = obsidian_sync.write_generated_note_sync(
                    workspace_root=workspace,
                    vault_root=workspace,
                    output_path=output_path,
                    content="# Daily Summary\n\nGenerated content.\n",
                    loop_status_payload=self._healthy_loop_status(),
                    generated_at="2026-04-15T10:00:00Z",
                )

            self.assertEqual(artifact["decision"]["status"], "blocked")
            self.assertEqual(artifact["decision"]["reasonCode"], "generated_path_not_allowed")
            self.assertFalse((escaped_dir / "summary.md").exists())
            quarantine_path = workspace / ".codex-supervisor" / "generated" / "obsidian" / "quarantine.json"
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
            self.assertEqual(quarantine["decision"]["status"], "blocked")
            self.assertEqual(quarantine["requestedPath"], "obsidian/generated/daily/summary.md")

    def test_curated_note_patch_applies_approved_replace_text_within_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            target_path = workspace / "obsidian" / "roadmap" / "quarterly-plan.md"
            target_path.parent.mkdir(parents=True)
            target_path.write_text(
                "# Quarterly Plan\n\nStatus: Draft\nOwner: Tommy\n",
                encoding="utf-8",
            )

            artifact = obsidian_sync.apply_curated_note_patch_artifact(
                workspace_root=workspace,
                vault_root=workspace,
                patch_artifact={
                    "artifactType": "roadmap_continuity_note_patch_plan",
                    "approval": {"status": "approved"},
                    "patches": [
                        {
                            "targetPath": "obsidian/roadmap/quarterly-plan.md",
                            "operation": "replace_text",
                            "matchText": "Status: Draft",
                            "replacementText": "Status: Confirmed",
                        }
                    ],
                },
                loop_status_payload=self._healthy_loop_status(),
                generated_at="2026-04-15T10:00:00Z",
            )

            self.assertEqual(artifact["decision"]["status"], "applied")
            self.assertEqual(artifact["decision"]["reasonCode"], "curated_note_patch_applied")
            self.assertEqual(artifact["policy"]["mode"], "curated_note_patch_policy")
            self.assertEqual(
                target_path.read_text(encoding="utf-8"),
                "# Quarterly Plan\n\nStatus: Confirmed\nOwner: Tommy\n",
            )
            self.assertEqual(
                artifact["patches"][0]["targetPath"],
                "obsidian/roadmap/quarterly-plan.md",
            )

    def test_curated_note_patch_blocks_out_of_policy_target_path(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            target_path = workspace / "Notes" / "Project.md"
            target_path.parent.mkdir(parents=True)
            target_path.write_text("# Project\n\nStatus: Draft\n", encoding="utf-8")

            artifact = obsidian_sync.apply_curated_note_patch_artifact(
                workspace_root=workspace,
                vault_root=workspace,
                patch_artifact={
                    "artifactType": "roadmap_continuity_note_patch_plan",
                    "approval": {"status": "approved"},
                    "patches": [
                        {
                            "targetPath": "Notes/Project.md",
                            "operation": "replace_text",
                            "matchText": "Status: Draft",
                            "replacementText": "Status: Confirmed",
                        }
                    ],
                },
                loop_status_payload=self._healthy_loop_status(),
                generated_at="2026-04-15T10:00:00Z",
            )

            self.assertEqual(artifact["decision"]["status"], "blocked")
            self.assertEqual(
                artifact["decision"]["reasonCode"],
                "curated_note_patch_policy_violation",
            )
            self.assertEqual(target_path.read_text(encoding="utf-8"), "# Project\n\nStatus: Draft\n")
            quarantine_path = workspace / ".codex-supervisor" / "generated" / "obsidian" / "quarantine.json"
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
            self.assertEqual(quarantine["decision"]["status"], "blocked")
            self.assertEqual(
                quarantine["patches"][0]["decision"]["reasonCode"],
                "target_path_not_allowed",
            )

    def test_curated_note_patch_blocks_unsafe_or_unreadable_source_notes(self) -> None:
        cases = (
            (
                obsidian_sync.UnsafeGeneratedPathError("generated sync path is not safely reachable"),
                "target_path_not_safely_reachable",
            ),
            (PermissionError(errno.EACCES, "Permission denied"), "target_note_unreadable"),
        )

        for raised_error, expected_reason_code in cases:
            with self.subTest(reason_code=expected_reason_code):
                with tempfile.TemporaryDirectory() as tempdir:
                    workspace = Path(tempdir)
                    target_path = workspace / "obsidian" / "roadmap" / "quarterly-plan.md"
                    target_path.parent.mkdir(parents=True)
                    target_path.write_text(
                        "# Quarterly Plan\n\nStatus: Draft\n",
                        encoding="utf-8",
                    )

                    with patch(
                        "automationplus.obsidian_sync._read_text_no_symlinks",
                        side_effect=raised_error,
                    ):
                        artifact = obsidian_sync.apply_curated_note_patch_artifact(
                            workspace_root=workspace,
                            vault_root=workspace,
                            patch_artifact={
                                "artifactType": "roadmap_continuity_note_patch_plan",
                                "approval": {"status": "approved"},
                                "patches": [
                                    {
                                        "targetPath": "obsidian/roadmap/quarterly-plan.md",
                                        "operation": "replace_text",
                                        "matchText": "Status: Draft",
                                        "replacementText": "Status: Confirmed",
                                    }
                                ],
                            },
                            loop_status_payload=self._healthy_loop_status(),
                            generated_at="2026-04-15T10:00:00Z",
                        )

                    self.assertEqual(artifact["decision"]["status"], "blocked")
                    self.assertEqual(
                        artifact["patches"][0]["decision"]["reasonCode"],
                        expected_reason_code,
                    )
                    self.assertEqual(
                        target_path.read_text(encoding="utf-8"),
                        "# Quarterly Plan\n\nStatus: Draft\n",
                    )
                    quarantine_path = (
                        workspace
                        / ".codex-supervisor"
                        / "generated"
                        / "obsidian"
                        / "quarantine.json"
                    )
                    quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        quarantine["patches"][0]["decision"]["reasonCode"],
                        expected_reason_code,
                    )

    def test_curated_note_patch_batch_failures_do_not_leave_partial_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            first_target = workspace / "obsidian" / "roadmap" / "first.md"
            second_target = workspace / "obsidian" / "roadmap" / "second.md"
            first_target.parent.mkdir(parents=True)
            first_target.write_text("# First\n\nStatus: Draft\n", encoding="utf-8")
            second_target.write_text("# Second\n\nStatus: Draft\n", encoding="utf-8")

            original_write_text_atomic = obsidian_sync._write_text_atomic
            write_calls: list[Path] = []

            def fail_on_second_write(root: Path, path: Path, content: str) -> None:
                write_calls.append(path)
                if len(write_calls) == 2:
                    raise obsidian_sync.UnsafeGeneratedPathError(
                        "generated sync path is not safely reachable"
                    )
                original_write_text_atomic(root, path, content)

            with patch(
                "automationplus.obsidian_sync._write_text_atomic",
                side_effect=fail_on_second_write,
            ):
                artifact = obsidian_sync.apply_curated_note_patch_artifact(
                    workspace_root=workspace,
                    vault_root=workspace,
                    patch_artifact={
                        "artifactType": "roadmap_continuity_note_patch_plan",
                        "approval": {"status": "approved"},
                        "patches": [
                            {
                                "targetPath": "obsidian/roadmap/first.md",
                                "operation": "replace_text",
                                "matchText": "Status: Draft",
                                "replacementText": "Status: Confirmed",
                            },
                            {
                                "targetPath": "obsidian/roadmap/second.md",
                                "operation": "replace_text",
                                "matchText": "Status: Draft",
                                "replacementText": "Status: Confirmed",
                            },
                        ],
                    },
                    loop_status_payload=self._healthy_loop_status(),
                    generated_at="2026-04-15T10:00:00Z",
                )

            self.assertEqual(artifact["decision"]["status"], "blocked")
            self.assertEqual(artifact["writeState"]["contentChanged"], False)
            self.assertEqual(artifact["writeState"]["contentChangedBeforeFailure"], True)
            self.assertEqual(artifact["writeState"]["rollbackStatus"], "restored")
            self.assertEqual(
                artifact["patches"][0]["decision"]["reasonCode"],
                "patch_reverted_after_batch_failure",
            )
            self.assertEqual(
                artifact["patches"][1]["decision"]["reasonCode"],
                "target_path_not_safely_reachable",
            )
            self.assertEqual(
                first_target.read_text(encoding="utf-8"),
                "# First\n\nStatus: Draft\n",
            )
            self.assertEqual(
                second_target.read_text(encoding="utf-8"),
                "# Second\n\nStatus: Draft\n",
            )
            quarantine_path = workspace / ".codex-supervisor" / "generated" / "obsidian" / "quarantine.json"
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
            self.assertEqual(quarantine["decision"]["status"], "blocked")
            self.assertEqual(quarantine["writeState"]["contentChangedBeforeFailure"], True)
            self.assertEqual(quarantine["writeState"]["rollbackStatus"], "restored")

    def test_curated_note_patch_rolls_back_in_flight_write_after_late_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            target_path = workspace / "obsidian" / "roadmap" / "quarterly-plan.md"
            target_path.parent.mkdir(parents=True)
            target_path.write_text("# Quarterly Plan\n\nStatus: Draft\n", encoding="utf-8")

            original_write_text_atomic = obsidian_sync._write_text_atomic
            write_calls: list[tuple[Path, str]] = []

            def fail_after_write(root: Path, path: Path, content: str) -> None:
                write_calls.append((path, content))
                original_write_text_atomic(root, path, content)
                if len(write_calls) == 1:
                    raise OSError(errno.EIO, "late write failure after replace")

            with patch(
                "automationplus.obsidian_sync._write_text_atomic",
                side_effect=fail_after_write,
            ):
                artifact = obsidian_sync.apply_curated_note_patch_artifact(
                    workspace_root=workspace,
                    vault_root=workspace,
                    patch_artifact={
                        "artifactType": "roadmap_continuity_note_patch_plan",
                        "approval": {"status": "approved"},
                        "patches": [
                            {
                                "targetPath": "obsidian/roadmap/quarterly-plan.md",
                                "operation": "replace_text",
                                "matchText": "Status: Draft",
                                "replacementText": "Status: Confirmed",
                            }
                        ],
                    },
                    loop_status_payload=self._healthy_loop_status(),
                    generated_at="2026-04-15T10:00:00Z",
                )

            self.assertEqual(artifact["decision"]["status"], "blocked")
            self.assertEqual(artifact["writeState"]["contentChanged"], False)
            self.assertEqual(artifact["writeState"]["contentChangedBeforeFailure"], True)
            self.assertEqual(artifact["writeState"]["rollbackStatus"], "restored")
            self.assertEqual(
                artifact["patches"][0]["decision"]["reasonCode"],
                "target_write_failed",
            )
            self.assertEqual(
                target_path.read_text(encoding="utf-8"),
                "# Quarterly Plan\n\nStatus: Draft\n",
            )
            self.assertGreaterEqual(len(write_calls), 2)
            self.assertEqual(write_calls[0][1], "# Quarterly Plan\n\nStatus: Confirmed\n")
            self.assertEqual(write_calls[1][1], "# Quarterly Plan\n\nStatus: Draft\n")
            quarantine_path = workspace / ".codex-supervisor" / "generated" / "obsidian" / "quarantine.json"
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
            self.assertEqual(quarantine["decision"]["status"], "blocked")
            self.assertEqual(quarantine["writeState"]["contentChanged"], False)
            self.assertEqual(quarantine["writeState"]["contentChangedBeforeFailure"], True)
            self.assertEqual(quarantine["writeState"]["rollbackStatus"], "restored")

    def test_curated_note_patch_write_state_ignores_no_net_delta_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            target_path = workspace / "obsidian" / "roadmap" / "quarterly-plan.md"
            target_path.parent.mkdir(parents=True)
            original_content = "# Quarterly Plan\n\nStatus: Draft\n"
            target_path.write_text(original_content, encoding="utf-8")

            with patch(
                "automationplus.obsidian_sync._write_text_atomic",
                side_effect=AssertionError("unexpected note write for no-op batch"),
            ):
                artifact = obsidian_sync.apply_curated_note_patch_artifact(
                    workspace_root=workspace,
                    vault_root=workspace,
                    patch_artifact={
                        "artifactType": "roadmap_continuity_note_patch_plan",
                        "approval": {"status": "approved"},
                        "patches": [
                            {
                                "targetPath": "obsidian/roadmap/quarterly-plan.md",
                                "operation": "replace_text",
                                "matchText": "Status: Draft",
                                "replacementText": "Status: Confirmed",
                            },
                            {
                                "targetPath": "obsidian/roadmap/quarterly-plan.md",
                                "operation": "replace_text",
                                "matchText": "Status: Confirmed",
                                "replacementText": "Status: Draft",
                            },
                        ],
                    },
                    loop_status_payload=self._healthy_loop_status(),
                    generated_at="2026-04-15T10:00:00Z",
                )

            self.assertEqual(artifact["decision"]["status"], "applied")
            self.assertEqual(artifact["writeState"]["contentChanged"], False)
            self.assertEqual(artifact["writeState"]["contentChangedBeforeFailure"], False)
            self.assertEqual(artifact["writeState"]["rollbackStatus"], "not_needed")
            self.assertEqual(target_path.read_text(encoding="utf-8"), original_content)

    def test_curated_note_patch_rolls_back_on_os_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            first_target = workspace / "obsidian" / "roadmap" / "first.md"
            second_target = workspace / "obsidian" / "roadmap" / "second.md"
            first_target.parent.mkdir(parents=True)
            first_target.write_text("# First\n\nStatus: Draft\n", encoding="utf-8")
            second_target.write_text("# Second\n\nStatus: Draft\n", encoding="utf-8")

            original_write_text_atomic = obsidian_sync._write_text_atomic
            write_calls: list[Path] = []

            def fail_on_second_write(root: Path, path: Path, content: str) -> None:
                write_calls.append(path)
                if len(write_calls) == 2:
                    raise OSError(errno.ENOSPC, "No space left on device")
                original_write_text_atomic(root, path, content)

            with patch(
                "automationplus.obsidian_sync._write_text_atomic",
                side_effect=fail_on_second_write,
            ):
                artifact = obsidian_sync.apply_curated_note_patch_artifact(
                    workspace_root=workspace,
                    vault_root=workspace,
                    patch_artifact={
                        "artifactType": "roadmap_continuity_note_patch_plan",
                        "approval": {"status": "approved"},
                        "patches": [
                            {
                                "targetPath": "obsidian/roadmap/first.md",
                                "operation": "replace_text",
                                "matchText": "Status: Draft",
                                "replacementText": "Status: Confirmed",
                            },
                            {
                                "targetPath": "obsidian/roadmap/second.md",
                                "operation": "replace_text",
                                "matchText": "Status: Draft",
                                "replacementText": "Status: Confirmed",
                            },
                        ],
                    },
                    loop_status_payload=self._healthy_loop_status(),
                    generated_at="2026-04-15T10:00:00Z",
                )

            self.assertEqual(artifact["decision"]["status"], "blocked")
            self.assertEqual(artifact["writeState"]["contentChanged"], False)
            self.assertEqual(artifact["writeState"]["contentChangedBeforeFailure"], True)
            self.assertEqual(artifact["writeState"]["rollbackStatus"], "restored")
            self.assertEqual(
                artifact["patches"][0]["decision"]["reasonCode"],
                "patch_reverted_after_batch_failure",
            )
            self.assertEqual(
                artifact["patches"][1]["decision"]["reasonCode"],
                "target_write_failed",
            )
            self.assertEqual(
                first_target.read_text(encoding="utf-8"),
                "# First\n\nStatus: Draft\n",
            )
            self.assertEqual(
                second_target.read_text(encoding="utf-8"),
                "# Second\n\nStatus: Draft\n",
            )
            quarantine_path = workspace / ".codex-supervisor" / "generated" / "obsidian" / "quarantine.json"
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
            self.assertEqual(quarantine["decision"]["status"], "blocked")
            self.assertEqual(quarantine["writeState"]["contentChangedBeforeFailure"], True)
            self.assertEqual(quarantine["writeState"]["rollbackStatus"], "restored")
            self.assertEqual(
                quarantine["patches"][1]["decision"]["reasonCode"],
                "target_write_failed",
            )


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from automationplus.obsidian_sync import write_generated_note_sync


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

            artifact = write_generated_note_sync(
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

    def test_generated_sync_blocks_paths_outside_generated_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            blocked_path = workspace / "Notes" / "Project.md"

            artifact = write_generated_note_sync(
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

            artifact = write_generated_note_sync(
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

            artifact = write_generated_note_sync(
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


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automationplus.restart_decision as restart_decision


class RestartBudgetStateTests(unittest.TestCase):
    def test_read_budget_state_rejects_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            budget_path = Path(tempdir) / "restart-budget.json"
            budget_path.write_text('{"schemaVersion": 1, "history": [', encoding="utf-8")

            result = restart_decision._read_budget_state(budget_path, expect_present=False)

        self.assertFalse(result["trusted"])
        self.assertEqual(result["error"]["code"], "invalid_json")

    def test_read_budget_state_rejects_schema_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            budget_path = Path(tempdir) / "restart-budget.json"
            budget_path.write_text(
                json.dumps({"schemaVersion": 2, "history": []}),
                encoding="utf-8",
            )

            result = restart_decision._read_budget_state(budget_path, expect_present=False)

        self.assertFalse(result["trusted"])
        self.assertEqual(result["error"]["code"], "schema_mismatch")

    def test_read_budget_state_rejects_missing_file_when_expected(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            budget_path = Path(tempdir) / "restart-budget.json"

            result = restart_decision._read_budget_state(budget_path, expect_present=True)

        self.assertFalse(result["trusted"])
        self.assertEqual(result["error"]["code"], "missing_expected")

    def test_read_budget_state_rejects_unreadable_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            budget_path = Path(tempdir) / "restart-budget.json"

            with mock.patch("pathlib.Path.read_text", side_effect=PermissionError("permission denied")):
                result = restart_decision._read_budget_state(budget_path, expect_present=False)

        self.assertFalse(result["trusted"])
        self.assertEqual(result["error"]["code"], "read_failed")


if __name__ == "__main__":
    unittest.main()

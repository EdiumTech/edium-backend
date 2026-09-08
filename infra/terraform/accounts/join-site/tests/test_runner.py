import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parents[1] / "functions" / "app"))
from runner import RunnerUnavailable, SandboxRunner  # noqa: E402
from contest_content import LEGACY_TASK_SET_VERSION  # noqa: E402


class RunnerReportTests(unittest.TestCase):
    def test_declared_language_capabilities_default_to_js_and_ignore_unknown_values(self):
        with patch.dict(os.environ):
            os.environ.pop("RUNNER_LANGUAGES", None)
            self.assertEqual(SandboxRunner().supported_languages, {"javascript"})
        for configured, expected in [
            ("", set()), ("python,KOTLIN,SWIFT", set()), ("kotlin", {"kotlin"}),
            (" javascript, kotlin, swift, python,kotlin ", {"javascript", "kotlin", "swift"}),
        ]:
            with self.subTest(configured=configured), patch.dict(os.environ, {"RUNNER_LANGUAGES": configured}):
                self.assertEqual(SandboxRunner().supported_languages, expected)

    def test_request_uses_the_assigned_version_and_checks_catalog_case_count(self):
        runner = SandboxRunner()
        runner.url = "https://runner.example.test/"
        response = io.BytesIO(json.dumps({"tests": [{"passed": True}] * 3}).encode())
        response.status = 200
        with patch.object(runner, "_iam_token", return_value="synthetic-token"), patch("runner.urllib.request.urlopen", return_value=response) as send:
            result = runner.run("ai-evidence", "function solve() {}", LEGACY_TASK_SET_VERSION)
        request = send.call_args.args[0]
        self.assertEqual(json.loads(request.data)["taskSetVersion"], LEGACY_TASK_SET_VERSION)
        self.assertEqual(json.loads(request.data)["language"], "javascript")
        self.assertEqual((result["passed"], result["total"]), (3, 3))

    def test_counts_are_derived_from_every_test_not_the_summary(self):
        result = SandboxRunner._sanitize({
            "passed": 100, "total": 100, "allPassed": True,
            "tests": [{"name": "первый", "passed": True}, {"name": "второй", "passed": False}],
            "durationMs": 12,
        }, 2)
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["total"], 2)
        self.assertFalse(result["allPassed"])
        self.assertEqual(len(result["tests"]), 2)

    def test_incomplete_or_malformed_reports_cannot_imply_success(self):
        for report in (
            {"tests": []},
            {"tests": [{"passed": True}]},
            {"tests": [{"passed": True}, {"passed": "false"}]},
            {"tests": [{"passed": True}, None]},
            {"tests": [{"passed": True}, {"passed": True}], "durationMs": "invalid"},
            {"tests": [{"passed": True}, {"passed": True}], "durationMs": True},
            {"tests": [{"passed": True}, {"passed": True}], "durationMs": 120001},
            {"tests": [{"passed": True}, {"passed": True}], "durationMs": float("nan")},
        ):
            with self.subTest(report=report), self.assertRaises(RunnerUnavailable):
                SandboxRunner._sanitize(report, 2)

    def test_all_passed_is_true_only_for_a_complete_successful_suite(self):
        result = SandboxRunner._sanitize({"tests": [{"passed": True}, {"passed": True}]}, 2)
        self.assertEqual((result["passed"], result["total"], result["allPassed"]), (2, 2, True))

    def test_mobile_language_is_forwarded_and_compile_time_is_included(self):
        runner = SandboxRunner()
        runner.url = "https://runner.example.test/"
        response = io.BytesIO(json.dumps({"tests": [{"passed": True}] * 7, "durationMs": 12500}).encode())
        response.status = 200
        with patch.object(runner, "_iam_token", return_value="synthetic-token"), patch("runner.urllib.request.urlopen", return_value=response) as send:
            result = runner.run("mobile-permission-panda", "func solve(_ input: [String: Any]) -> [String: Any] { [:] }", "edium-mobile-2026-09-v3", "swift")
        self.assertEqual(json.loads(send.call_args.args[0].data)["language"], "swift")
        self.assertEqual((result["passed"], result["total"], result["durationMs"]), (7, 7, 12500))


if __name__ == "__main__":
    unittest.main()

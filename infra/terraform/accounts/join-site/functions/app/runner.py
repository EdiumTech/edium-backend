import json
import os
import urllib.error
import urllib.request

from contest_content import public_tasks


class RunnerUnavailable(Exception):
    pass


class SandboxRunner:
    def __init__(self):
        self.url = os.getenv("RUNNER_URL", "").rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    @property
    def supported_languages(self) -> set[str]:
        configured = os.getenv("RUNNER_LANGUAGES", "javascript").split(",")
        return {value.strip() for value in configured if value.strip() in {"javascript", "kotlin", "swift"}}

    def run(self, task_id: str, source: str, task_set_version: str, language: str = "javascript") -> dict:
        if not self.enabled:
            raise RunnerUnavailable("runner_disabled")
        token = self._iam_token()
        data = json.dumps({"taskId": task_id, "source": source, "taskSetVersion": task_set_version, "language": language}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Request-Source": "edium-join-api",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=9 if language == "javascript" else 119) as response:
                if response.status != 200:
                    raise RunnerUnavailable("runner_http_error")
                result = json.loads(response.read(262144).decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            raise RunnerUnavailable("runner_unavailable") from error
        expected_total = next(task["testCount"] for task in public_tasks(task_set_version) if task["id"] == task_id)
        return self._sanitize(result, expected_total)

    @staticmethod
    def _iam_token() -> str:
        request = urllib.request.Request(
            "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                value = json.loads(response.read(16384).decode("utf-8"))
                return value["access_token"]
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, json.JSONDecodeError) as error:
            raise RunnerUnavailable("iam_token_unavailable") from error

    @staticmethod
    def _sanitize(result: dict, expected_total: int) -> dict:
        if not isinstance(result, dict):
            raise RunnerUnavailable("invalid_runner_response")
        reported_tests = result.get("tests")
        if not isinstance(reported_tests, list) or len(reported_tests) != expected_total or not 1 <= expected_total <= 20:
            raise RunnerUnavailable("incomplete_runner_response")
        tests = []
        for item in reported_tests:
            if not isinstance(item, dict) or not isinstance(item.get("passed"), bool):
                raise RunnerUnavailable("invalid_runner_response")
            tests.append(
                {
                    "name": str(item.get("name", "Тест"))[:80],
                    "passed": item["passed"],
                    "message": str(item.get("message", ""))[:500],
                }
            )
        duration = result.get("durationMs", 0)
        # Includes mobile compilation in local/native-capable runners, not only
        # the much shorter per-case sandbox execution time.
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 <= duration <= 120000:
            raise RunnerUnavailable("invalid_runner_response")
        passed = sum(item["passed"] for item in tests)
        return {
            "passed": passed,
            "total": expected_total,
            "allPassed": passed == expected_total,
            "tests": tests,
            "durationMs": int(duration),
        }

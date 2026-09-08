import json
import os
import urllib.error
import urllib.request


class RunnerUnavailable(Exception):
    pass


class SandboxRunner:
    def __init__(self):
        self.url = os.getenv("RUNNER_URL", "").rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def run(self, task_id: str, source: str) -> dict:
        if not self.enabled:
            raise RunnerUnavailable("runner_disabled")
        token = self._iam_token()
        data = json.dumps({"taskId": task_id, "source": source}).encode("utf-8")
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
            with urllib.request.urlopen(request, timeout=9) as response:
                if response.status != 200:
                    raise RunnerUnavailable("runner_http_error")
                result = json.loads(response.read(262144).decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            raise RunnerUnavailable("runner_unavailable") from error
        return self._sanitize(result)

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
    def _sanitize(result: dict) -> dict:
        if not isinstance(result, dict):
            raise RunnerUnavailable("invalid_runner_response")
        tests = []
        for item in result.get("tests", [])[:20]:
            if not isinstance(item, dict):
                continue
            tests.append(
                {
                    "name": str(item.get("name", "Тест"))[:80],
                    "passed": bool(item.get("passed")),
                    "message": str(item.get("message", ""))[:500],
                }
            )
        return {
            "passed": bool(result.get("passed")),
            "tests": tests,
            "durationMs": min(max(int(result.get("durationMs", 0)), 0), 10000),
        }

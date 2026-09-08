"""Exercise the local demo against the real API and QuickJS without opening ports."""

import argparse
import json
import subprocess

from demo_server import ADMIN_KEY, DEMO_DIRECTIONS, ROOT, api_event, make_demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", nargs="+", choices=[item[0] for item in DEMO_DIRECTIONS])
    args = parser.parse_args()
    api, _, links = make_demo()
    if args.tracks:
        links = [item for item in links if item["slug"] in args.tracks]
    references = json.loads(subprocess.check_output(
        ["node", "-e", "process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(require('./task-reference-solutions.testdata').referenceSolutions).map(([id, solve]) => [id, solve.toString()]))))"],
        cwd=ROOT / "runner", text=True,
    ))

    def request(method, path, payload=None, token=None, admin=False, expected=200):
        headers = {"Authorization": f"Bearer {ADMIN_KEY}"} if admin else {"Authorization": f"Contest {token}"} if token else {}
        response = api.api(api_event(method, path, payload, headers))
        body = json.loads(response["body"])
        assert response["statusCode"] == expected, (method, path, response["statusCode"], body)
        return body

    request("GET", "/v1/admin/applications", expected=401)
    applications = request("GET", "/v1/admin/applications", admin=True)["applications"]
    assert len(applications) == 8
    versions = set()
    reference_task = None
    total_tests = 0
    for item in links:
        token = item["url"].split("#invite=", 1)[1]
        opened = request("POST", "/v1/contest/open", token=token)["contest"]
        assert opened["state"] == "opened"
        current = request("POST", "/v1/contest/start", token=token)["contest"]
        assert current["language"] == "javascript"
        assert current["taskSetVersion"] not in versions
        versions.add(current["taskSetVersion"])

        if reference_task:
            request("POST", "/v1/contest/run", {"taskId": reference_task, "source": "function solve() { return null }"}, token, expected=404)
            request("PATCH", f"/v1/contest/answers/{reference_task}", {"source": "function solve() { return null }", "revision": current["revision"]}, token, expected=404)
        else:
            reference_task = current["tasks"][0]["id"]

        tasks = current["tasks"]
        for index, task in enumerate(tasks):
            source = references[task["id"]]
            original_revision = current["revision"]
            current = request("PATCH", f"/v1/contest/answers/{task['id']}", {
                "source": source, "revision": original_revision,
            }, token)["contest"]
            assert current["answers"][task["id"]]["source"] == source
            assert "explanation" not in current["answers"][task["id"]]
            assert "url" not in current["answers"][task["id"]]
            if index == 0:
                request("PATCH", f"/v1/contest/answers/{task['id']}", {
                    "source": "function solve() { return null }", "revision": original_revision,
                }, token, expected=409)
                failed = request("POST", "/v1/contest/run", {
                    "taskId": task["id"], "source": "function solve() { return null }",
                }, token)["result"]
                assert failed["passed"] < failed["total"]
                assert not failed["allPassed"]
            result = request("POST", "/v1/contest/run", {"taskId": task["id"], "source": source}, token)["result"]
            assert result["passed"] == result["total"] == task["testCount"], (task["id"], result)
            assert result["allPassed"]
            assert len(result["tests"]) == task["testCount"]
            total_tests += result["total"]

        submitted = request("POST", "/v1/contest/submit", token=token)
        assert submitted["submitted"] and submitted["contest"]["state"] == "submitted"
        request("POST", "/v1/contest/run", {"taskId": tasks[0]["id"], "source": references[tasks[0]["id"]]}, token, expected=409)
        admin_path = f"/v1/admin/applications/{item['applicationId']}"
        detail = request("GET", admin_path, admin=True)["application"]
        assert detail["contest"]["state"] == "submitted"
        request("PATCH", f"{admin_path}/contest/review", {
            "tasks": {tasks[0]["id"]: {"score": 4, "note": "Локальная проверка"}}, "conclusion": "Готово",
        }, admin=True)
        print(f"OK {item['direction']}: {len(tasks)} tasks")

    uninvited = next(item for item in applications if item["contestState"] is None)
    issued = request("POST", f"/v1/admin/applications/{uninvited['id']}/contest", {
        "durationMinutes": 60, "startWithinDays": 2,
    }, admin=True, expected=201)
    assert issued["contest"]["inviteUrl"]
    print(f"PASS: {len(links)} directions; {total_tests} real reference checks; wrong-source, stale-revision, cross-track, code-only submit and HR flows")


if __name__ == "__main__":
    main()

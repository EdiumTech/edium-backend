"""Exercise the local API and real language runtimes without opening ports.

Native runtime unavailability is reported explicitly. Use --require-native to
make missing Kotlin/Swift execution a failure (for provisioned runner hosts).
"""

import argparse
import json
import subprocess

from demo_server import ADMIN_KEY, DEMO_DIRECTIONS, ROOT, api_event, make_demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", nargs="+", choices=[item[0] for item in DEMO_DIRECTIONS])
    parser.add_argument("--require-native", action="store_true")
    args = parser.parse_args()
    api, _, links = make_demo()
    if args.tracks:
        links = [item for item in links if item["slug"] in args.tracks]
    references = json.loads(subprocess.check_output(
        ["node", "-e", "const js=require('./task-reference-solutions.testdata').referenceSolutions; const native=require('./mobile-reference-solutions.testdata').mobileReferenceSolutions; const roles=require('./role-reference-solutions.testdata').roleReferenceSolutions; process.stdout.write(JSON.stringify({...Object.fromEntries(Object.entries(js).map(([id,solve])=>[id,{javascript:solve.toString()}])),...native,...roles}))"],
        cwd=ROOT / "runner", text=True,
    ))

    def request(method, path, payload=None, token=None, admin=False, expected=200, native_optional=False):
        headers = {"Authorization": f"Bearer {ADMIN_KEY}"} if admin else {"Authorization": f"Contest {token}"} if token else {}
        response = api.api(api_event(method, path, payload, headers))
        body = json.loads(response["body"])
        if native_optional and response["statusCode"] == 503 and body.get("code") == "runtime_unavailable":
            assert not args.require_native, ("Required native runtime is unavailable", payload["language"], payload["taskId"])
            return None
        assert response["statusCode"] == expected, (method, path, response["statusCode"], body)
        return body

    request("GET", "/v1/admin/applications", expected=401)
    applications = request("GET", "/v1/admin/applications", admin=True)["applications"]
    assert len(applications) == 6
    assert [item[0] for item in DEMO_DIRECTIONS] == ["backend", "frontend", "mobile", "ai", "systems"]
    versions = set()
    reference_task = None
    total_tests = 0
    native_skips = []
    for item in links:
        token = item["url"].split("#invite=", 1)[1]
        opened = request("POST", "/v1/contest/open", token=token)["contest"]
        assert opened["state"] == "opened"
        current = request("POST", "/v1/contest/start", token=token)["contest"]
        expected_languages = {"mobile": ["kotlin", "swift"], "backend": ["go"], "ai": ["python"]}.get(item["slug"], ["javascript"])
        assert current["languages"] == expected_languages
        assert current["language"] == ("mixed" if len(expected_languages) > 1 else expected_languages[0])
        assert current["taskSetVersion"] not in versions
        versions.add(current["taskSetVersion"])

        if reference_task:
            request("POST", "/v1/contest/run", {"taskId": reference_task, "source": "function solve() { return null }"}, token, expected=404)
            request("PATCH", f"/v1/contest/answers/{reference_task}", {"source": "function solve() { return null }", "revision": current["revision"]}, token, expected=404)
        else:
            reference_task = current["tasks"][0]["id"]

        tasks = current["tasks"]
        if item["slug"] == "mobile":
            assert [list(task["languages"]) for task in tasks] == [["kotlin"], ["swift"], ["kotlin", "swift"]]
        for task in tasks:
            wrong_language = "javascript" if "javascript" not in task["languages"] else "python"
            for method, path in [("POST", "/v1/contest/run"), ("PATCH", f"/v1/contest/answers/{task['id']}")]:
                invalid = request(method, path, {
                    "taskId": task["id"], "source": "irrelevant", "language": wrong_language, "revision": current["revision"],
                }, token, expected=400)
                assert invalid["code"] == "invalid_language"

            for language, option in task["languages"].items():
                source = references[task["id"]][language]
                original_revision = current["revision"]
                current = request("PATCH", f"/v1/contest/answers/{task['id']}", {
                    "source": source, "language": language, "revision": original_revision,
                }, token)["contest"]
                answer = current["answers"][task["id"]]
                assert answer["source"] == source and answer["language"] == language
                assert "explanation" not in answer and "url" not in answer
                request("PATCH", f"/v1/contest/answers/{task['id']}", {
                    "source": option["starterCode"], "language": language, "revision": original_revision,
                }, token, expected=409)

                response = request("POST", "/v1/contest/run", {
                    "taskId": task["id"], "source": source, "language": language,
                }, token, native_optional=language != "javascript")
                if response is None:
                    native_skips.append(f"{task['id']} ({language})")
                    print(f"SKIP native runtime unavailable: {task['id']} ({language})", flush=True)
                    continue
                run_result = response["result"]
                assert run_result["passed"] == run_result["total"] == task["testCount"], (task["id"], language, run_result)
                assert run_result["allPassed"] and len(run_result["tests"]) == task["testCount"]
                total_tests += run_result["total"]

                # A valid but incomplete starter must fail real checks; compiler
                # errors also belong to the normal 0/N result, never a skip.
                failed = request("POST", "/v1/contest/run", {
                    "taskId": task["id"], "source": option["starterCode"], "language": language,
                }, token)["result"]
                assert failed["passed"] < failed["total"] and not failed["allPassed"]

        submitted = request("POST", "/v1/contest/submit", token=token)
        assert submitted["submitted"] and submitted["contest"]["state"] == "submitted"
        first_answer = current["answers"][tasks[0]["id"]]
        request("POST", "/v1/contest/run", {"taskId": tasks[0]["id"], **first_answer}, token, expected=409)
        admin_path = f"/v1/admin/applications/{item['applicationId']}"
        detail = request("GET", admin_path, admin=True)["application"]
        assert detail["contest"]["state"] == "submitted"
        assert {key: answer["language"] for key, answer in detail["contest"]["answers"].items()} == {
            key: answer["language"] for key, answer in current["answers"].items()
        }
        request("PATCH", f"{admin_path}/contest/review", {
            "tasks": {tasks[0]["id"]: {"score": 4, "note": "Локальная проверка"}}, "conclusion": "Готово",
        }, admin=True)
        print(f"OK API/HR {item['direction']}: {len(tasks)} tasks", flush=True)

    uninvited = next(item for item in applications if item["contestState"] is None)
    issued = request("POST", f"/v1/admin/applications/{uninvited['id']}/contest", {
        "durationMinutes": 60, "startWithinDays": 2,
    }, admin=True, expected=201)
    assert issued["contest"]["inviteUrl"] and issued["contest"]["direction"] == "Бэкенд"
    print(f"PASS API/HR: {len(links)} directions; {total_tests} real reference checks; language validation, stale revisions, cross-track and submit checks")
    if native_skips:
        print(f"INCOMPLETE native execution: {len(native_skips)} reference runs skipped; provision runtimes and rerun with --require-native")
    else:
        print("PASS execution: all selected task/language reference runs completed")


if __name__ == "__main__":
    main()

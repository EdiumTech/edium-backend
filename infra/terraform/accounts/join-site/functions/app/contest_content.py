"""Version-pinned JavaScript assignments shared with the isolated runner."""

from copy import deepcopy
import json
from pathlib import Path

LANGUAGE = "javascript"
LEGACY_TASK_SET_VERSION = "edium-js-2026-09-v1"
TASK_SET_VERSION = "edium-js-2026-09-v2-general"
_CATALOG = json.loads(Path(__file__).with_name("contest_catalog.json").read_text(encoding="utf-8"))
_SETS = _CATALOG["sets"]
_DIRECTION_VERSIONS = {
    entry["direction"]: version
    for version, entry in _SETS.items()
    if version != LEGACY_TASK_SET_VERSION
}


def resolve_task_set(direction: str | None) -> str:
    return _DIRECTION_VERSIONS.get((direction or "").strip(), TASK_SET_VERSION)


def _task_set(version: str) -> dict:
    try:
        return _SETS[version]
    except KeyError:
        raise ValueError("Unknown contest task set") from None


def track_label(version: str = TASK_SET_VERSION) -> str:
    return _task_set(version)["direction"]


def public_tasks(version: str = TASK_SET_VERSION) -> list[dict]:
    result = []
    for task in _task_set(version)["tasks"]:
        public = deepcopy({key: value for key, value in task.items()
                           if key not in {"tests", "publicExampleCount"}})
        if "publicExamples" not in public:
            public["publicExamples"] = [
                {"input": deepcopy(test["input"]), "expected": deepcopy(test["expected"])}
                for test in task["tests"][:task.get("publicExampleCount", 2)]
            ]
        public["testCount"] = len(task["tests"])
        result.append(public)
    return result


def task_ids(version: str = TASK_SET_VERSION) -> set[str]:
    return {task["id"] for task in _task_set(version)["tasks"]}


# Compatibility for callers importing the default tasks; assigned contests use their pinned version.
TASKS = public_tasks()

"""Version-pinned role assignments shared with the isolated runner."""

from copy import deepcopy
import json
from pathlib import Path

LANGUAGE = "javascript"
LEGACY_TASK_SET_VERSION = "edium-js-2026-09-v1"
TASK_SET_VERSION = "edium-js-2026-09-v2-general"
ACTIVE_DIRECTIONS = (
    "Бэкенд",
    "Фронтенд",
    "Мобильная разработка",
    "AI/ML",
    "Системная разработка",
)
_CATALOG = json.loads(Path(__file__).with_name("contest_catalog.json").read_text(encoding="utf-8"))
_PRESENTATIONS = json.loads(Path(__file__).with_name("contest_presentations.json").read_text(encoding="utf-8"))
_SETS = _CATALOG["sets"]
_DIRECTION_VERSIONS = {
    entry["direction"]: version
    for version, entry in _SETS.items()
    if version != LEGACY_TASK_SET_VERSION
}
# Previously saved applications may still use a retired direction. Their v2
# assignments remain available, while all new forms accept ACTIVE_DIRECTIONS.
_DIRECTION_VERSIONS.update({
    "Бэкенд": "edium-go-2026-09-v4-backend",
    "Фронтенд": "edium-js-2026-09-v3-frontend",
    "Мобильная разработка": "edium-mobile-2026-09-v3",
    "AI/ML": "edium-python-2026-09-v4-ai-ml",
    "Системная разработка": "edium-js-2026-09-v3-boost",
    # Compatibility for applications saved before the public direction was renamed.
    "Системная разработка (EdiumBoost)": "edium-js-2026-09-v3-boost",
})


def resolve_task_set(direction: str | None) -> str:
    return _DIRECTION_VERSIONS.get((direction or "").strip(), TASK_SET_VERSION)


def _task_set(version: str) -> dict:
    try:
        return _SETS[version]
    except KeyError:
        raise ValueError("Unknown contest task set") from None


def track_label(version: str = TASK_SET_VERSION) -> str:
    label = _task_set(version)["direction"]
    return "Системная разработка" if label == "Системная разработка (EdiumBoost)" else label


def supported_languages(version: str = TASK_SET_VERSION) -> list[str]:
    return list(_task_set(version).get("languages", [LANGUAGE]))


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
        if "languages" not in public:
            public["languages"] = {
                LANGUAGE: {
                    "label": "JavaScript",
                    "signature": public["signature"],
                    "starterCode": public["starterCode"],
                }
            }
        public.setdefault("defaultLanguage", next(iter(public["languages"])))
        # Existing clients can still display a default starter while upgraded
        # clients use the explicit per-task language options.
        default = public["languages"][public["defaultLanguage"]]
        public.setdefault("signature", default["signature"])
        public.setdefault("starterCode", default["starterCode"])
        if task["id"] in _PRESENTATIONS:
            public["statement"] = deepcopy(_PRESENTATIONS[task["id"]])
            # Keep upgraded and older clients free of retired product copy.
            public["summary"] = public["statement"]["situation"]
            public["description"] = public["statement"]["goal"]
        result.append(public)
    return result


def task_ids(version: str = TASK_SET_VERSION) -> set[str]:
    return {task["id"] for task in _task_set(version)["tasks"]}


# Compatibility for callers importing the default tasks; assigned contests use their pinned version.
TASKS = public_tasks()

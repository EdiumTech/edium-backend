TASK_SET_VERSION = "edium-js-2026-09-v1"
LANGUAGE = "javascript"


TASKS = [
    {
        "id": "quiz-progress",
        "title": "Прогресс первого квиза",
        "summary": "Собери устойчивое состояние прохождения из событий, которые могут повторяться и приходить не по порядку.",
        "description": (
            "В Edium ученик может открыть квиз на нескольких устройствах или ненадолго потерять сеть. "
            "Реализуй функцию solve(input), которая получает массив событий и возвращает состояние каждой задачи, "
            "число завершённых задач и последний подтверждённый revision. Событие с тем же eventId учитывается один раз; "
            "для одной задачи побеждает больший revision, а при равенстве — более поздний serverAt. Некорректные события игнорируются."
        ),
        "signature": "function solve(input) -> { answers, completedCount, revision }",
        "starterCode": "function solve(input) {\n  // Верни JSON-совместимый объект.\n  return { answers: {}, completedCount: 0, revision: 0 }\n}\n",
        "minExplanation": 120,
        "maxExplanation": 2000,
        "publicExamples": [
            {
                "input": {
                    "events": [
                        {"eventId": "a", "taskId": "q1", "revision": 1, "answer": "4", "completed": True, "serverAt": "2026-09-01T10:00:00Z"},
                        {"eventId": "a", "taskId": "q1", "revision": 1, "answer": "4", "completed": True, "serverAt": "2026-09-01T10:00:00Z"},
                    ]
                },
                "expected": {"answers": {"q1": "4"}, "completedCount": 1, "revision": 1},
            }
        ],
    },
    {
        "id": "ai-evidence",
        "title": "Надёжное объяснение AI",
        "summary": "Не показывай уверенный ответ, если его ключевые утверждения не подтверждены источниками.",
        "description": (
            "Реализуй solve(input). На вход приходят claims и sources. Утверждение подтверждено, если существует источник "
            "с тем же claimId, confidence не ниже input.threshold и статусом verified. Верни supportedIds, uncertainIds "
            "и action: answer, caution или human_review. human_review нужен, если не подтверждено ключевое утверждение; "
            "caution — если не подтверждены только необязательные; иначе answer. Не изменяй входные данные."
        ),
        "signature": "function solve(input) -> { supportedIds, uncertainIds, action }",
        "starterCode": "function solve(input) {\n  return { supportedIds: [], uncertainIds: [], action: 'human_review' }\n}\n",
        "minExplanation": 120,
        "maxExplanation": 2000,
        "publicExamples": [
            {
                "input": {
                    "threshold": 0.8,
                    "claims": [{"id": "c1", "required": True}, {"id": "c2", "required": False}],
                    "sources": [{"claimId": "c1", "confidence": 0.92, "status": "verified"}],
                },
                "expected": {"supportedIds": ["c1"], "uncertainIds": ["c2"], "action": "caution"},
            }
        ],
    },
    {
        "id": "offline-merge",
        "title": "Синхронизация без потерь",
        "summary": "Объедини локальный и серверный черновики попытки так, чтобы более свежий ответ не исчез.",
        "description": (
            "Реализуй solve(input), получающий server и local с answers и revision. Для каждой задачи выбирай запись "
            "с большим revision. Если revisions равны, но значения различаются, сохрани серверную запись и добавь taskId "
            "в conflicts. Верни merged, conflicts и nextRevision, равный максимуму всех revisions плюс один. "
            "Порядок conflicts должен быть лексикографическим."
        ),
        "signature": "function solve(input) -> { merged, conflicts, nextRevision }",
        "starterCode": "function solve(input) {\n  return { merged: {}, conflicts: [], nextRevision: 1 }\n}\n",
        "minExplanation": 160,
        "maxExplanation": 2400,
        "publicExamples": [
            {
                "input": {
                    "server": {"answers": {"q1": {"value": "A", "revision": 2}}},
                    "local": {"answers": {"q1": {"value": "B", "revision": 3}}},
                },
                "expected": {"merged": {"q1": {"value": "B", "revision": 3}}, "conflicts": [], "nextRevision": 4},
            }
        ],
    },
]


def public_tasks() -> list[dict]:
    return [dict(task) for task in TASKS]


def task_ids() -> set[str]:
    return {task["id"] for task in TASKS}

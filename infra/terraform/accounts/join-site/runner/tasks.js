const taskSuites = {
  'quiz-progress': [
    {
      name: 'не учитывает дубликат события',
      input: {
        events: [
          { eventId: 'a', taskId: 'q1', revision: 1, answer: '4', completed: true, serverAt: '2026-09-01T10:00:00Z' },
          { eventId: 'a', taskId: 'q1', revision: 1, answer: '4', completed: true, serverAt: '2026-09-01T10:00:00Z' },
        ],
      },
      expected: { answers: { q1: '4' }, completedCount: 1, revision: 1 },
    },
    {
      name: 'выбирает новую revision независимо от порядка',
      input: {
        events: [
          { eventId: 'b', taskId: 'q1', revision: 3, answer: 'new', completed: false, serverAt: '2026-09-01T10:03:00Z' },
          { eventId: 'a', taskId: 'q1', revision: 2, answer: 'old', completed: true, serverAt: '2026-09-01T10:04:00Z' },
          { eventId: 'c', taskId: 'q2', revision: 4, answer: 'ok', completed: true, serverAt: '2026-09-01T10:05:00Z' },
        ],
      },
      expected: { answers: { q1: 'new', q2: 'ok' }, completedCount: 1, revision: 4 },
    },
    {
      name: 'игнорирует повреждённые события',
      input: { events: [null, {}, { eventId: 'x', taskId: '', revision: -1 }, { eventId: 'z', taskId: 'q1', revision: 0, answer: null, completed: false, serverAt: 'bad' }] },
      expected: { answers: {}, completedCount: 0, revision: 0 },
    },
    {
      name: 'при равной revision выбирает позднее серверное событие',
      input: {
        events: [
          { eventId: 'old', taskId: 'q1', revision: 2, answer: 'old', completed: false, serverAt: '2026-09-01T10:00:00Z' },
          { eventId: 'new', taskId: 'q1', revision: 2, answer: 'new', completed: true, serverAt: '2026-09-01T10:01:00Z' },
        ],
      },
      expected: { answers: { q1: 'new' }, completedCount: 1, revision: 2 },
    },
  ],
  'ai-evidence': [
    {
      name: 'помечает неподтверждённое необязательное утверждение',
      input: {
        threshold: 0.8,
        claims: [{ id: 'c1', required: true }, { id: 'c2', required: false }],
        sources: [{ claimId: 'c1', confidence: 0.92, status: 'verified' }],
      },
      expected: { supportedIds: ['c1'], uncertainIds: ['c2'], action: 'caution' },
    },
    {
      name: 'отправляет ключевое утверждение человеку',
      input: {
        threshold: 0.75,
        claims: [{ id: 'fact', required: true }],
        sources: [{ claimId: 'fact', confidence: 0.74, status: 'verified' }],
      },
      expected: { supportedIds: [], uncertainIds: ['fact'], action: 'human_review' },
    },
    {
      name: 'принимает полностью подтверждённый ответ',
      input: {
        threshold: 0.5,
        claims: [{ id: 'a', required: true }, { id: 'b', required: false }],
        sources: [{ claimId: 'b', confidence: 0.7, status: 'verified' }, { claimId: 'a', confidence: 0.5, status: 'verified' }],
      },
      expected: { supportedIds: ['a', 'b'], uncertainIds: [], action: 'answer' },
    },
  ],
  'offline-merge': [
    {
      name: 'локальная новая версия побеждает',
      input: {
        server: { answers: { q1: { value: 'A', revision: 2 } } },
        local: { answers: { q1: { value: 'B', revision: 3 } } },
      },
      expected: { merged: { q1: { value: 'B', revision: 3 } }, conflicts: [], nextRevision: 4 },
    },
    {
      name: 'равные разные версии образуют конфликт',
      input: {
        server: { answers: { q2: { value: 'server', revision: 4 }, q1: { value: 'same', revision: 1 } } },
        local: { answers: { q2: { value: 'local', revision: 4 }, q1: { value: 'same', revision: 1 } } },
      },
      expected: {
        merged: { q1: { value: 'same', revision: 1 }, q2: { value: 'server', revision: 4 } },
        conflicts: ['q2'],
        nextRevision: 5,
      },
    },
    {
      name: 'объединяет непересекающиеся ответы',
      input: {
        server: { answers: { q1: { value: 1, revision: 2 } } },
        local: { answers: { q3: { value: 3, revision: 5 } } },
      },
      expected: {
        merged: { q1: { value: 1, revision: 2 }, q3: { value: 3, revision: 5 } },
        conflicts: [],
        nextRevision: 6,
      },
    },
  ],
}

module.exports = { taskSuites }

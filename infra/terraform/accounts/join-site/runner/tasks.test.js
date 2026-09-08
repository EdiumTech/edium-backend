const test = require('node:test')
const assert = require('node:assert/strict')
const catalog = require('../functions/app/contest_catalog.json')
const { getTaskSuite, LEGACY_TASK_SET_VERSION } = require('./tasks')
const { referenceSolutions } = require('./task-reference-solutions.testdata')
const { runTask } = require('./sandbox')

test('catalog pins seven distinct role assignments and retains legacy IDs', () => {
  const current = Object.entries(catalog.sets).filter(([version]) => version !== LEGACY_TASK_SET_VERSION)
  assert.equal(current.length, 7)
  const ids = []
  for (const [, taskSet] of current) {
    assert.equal(taskSet.tasks.length, 3)
    for (const task of taskSet.tasks) {
      ids.push(task.id)
      assert.ok(task.tests.length >= 6 && task.tests.length <= 8)
      assert.equal(new Set(task.tests.map(item => item.name)).size, task.tests.length)
      assert.equal(task.publicExampleCount, 2)
      assert.equal(typeof referenceSolutions[task.id], 'function')
    }
  }
  assert.equal(new Set(ids).size, 21)
  assert.deepEqual(catalog.sets[LEGACY_TASK_SET_VERSION].tasks.map(task => task.id),
    ['quiz-progress', 'ai-evidence', 'offline-merge'])
})

test('unknown versions and cross-direction task IDs fail closed', () => {
  for (const version of ['missing-version', '__proto__', 'constructor', 'toString']) {
    assert.equal(getTaskSuite(version, 'quiz-progress'), undefined)
  }
  assert.equal(getTaskSuite('edium-js-2026-09-v2-design', 'notification-bouncer'), undefined)
})

for (const [version, taskSet] of Object.entries(catalog.sets)) {
  if (version === LEGACY_TASK_SET_VERSION) continue
  for (const task of taskSet.tasks) {
    test(taskSet.direction + ': ' + task.title + ' — every fixture agrees with the reference', async () => {
      const solve = referenceSolutions[task.id]
      assert.deepEqual(getTaskSuite(version, task.id), task.tests)
      for (const fixture of task.tests) {
        const input = structuredClone(fixture.input)
        assert.deepEqual(solve(input), fixture.expected, fixture.name)
        assert.deepEqual(input, fixture.input, 'reference mutated input: ' + fixture.name)
      }
      const result = await runTask(task.id, solve.toString(), version)
      assert.equal(result.total, task.tests.length)
      assert.equal(result.passed, task.tests.length, JSON.stringify(result.tests))
      assert.equal(result.allPassed, true)
    })
  }
}

const test = require('node:test')
const assert = require('node:assert/strict')
const { server } = require('./server')

test('HTTP runner supports legacy callers and rejects other-assignment tasks', async t => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => server.close(resolve)))
  const url = `http://127.0.0.1:${server.address().port}/`
  const post = payload => fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  const legacy = await post({ taskId: 'ai-evidence', source: 'function solve() { throw new Error("ошибка") }' })
  assert.equal(legacy.status, 200)
  const report = await legacy.json()
  assert.equal(report.total, 3)
  assert.equal(report.passed, 0)
  assert.equal(report.tests.length, 3)
  const wrongAssignment = await post({
    taskId: 'ai-evidence', source: 'function solve() {}', taskSetVersion: 'edium-js-2026-09-v2-design',
  })
  assert.equal(wrongAssignment.status, 400)
  assert.deepEqual(await wrongAssignment.json(), { message: 'unknown_task' })
})

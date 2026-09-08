const { newQuickJSWASMModule } = require('quickjs-emscripten')
const { taskSuites } = require('./tasks')

const MEMORY_LIMIT = 64 * 1024 * 1024
const STACK_LIMIT = 512 * 1024
const TEST_TIMEOUT_MS = 750
const MAX_SOURCE_BYTES = 65536
const MAX_RESULT_BYTES = 262144

function stable(value) {
  if (Array.isArray(value)) return value.map(stable)
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]))
  }
  return value
}

function equals(left, right) {
  return JSON.stringify(stable(left)) === JSON.stringify(stable(right))
}

function safeMessage(value) {
  const text = String(value || 'Ошибка выполнения').replace(/[\r\n\t]+/g, ' ')
  return text.slice(0, 500)
}

async function executeTest(QuickJS, source, test) {
  const runtime = QuickJS.newRuntime()
  runtime.setMemoryLimit(MEMORY_LIMIT)
  runtime.setMaxStackSize(STACK_LIMIT)
  const deadline = Date.now() + TEST_TIMEOUT_MS
  runtime.setInterruptHandler(() => Date.now() >= deadline)
  const context = runtime.newContext()
  try {
    const input = JSON.stringify(test.input)
    const program = `
      "use strict";
      const fetch = undefined;
      const XMLHttpRequest = undefined;
      const WebSocket = undefined;
      const Worker = undefined;
      const process = undefined;
      const require = undefined;
      ${source}
      if (typeof solve !== "function") throw new Error("Объяви функцию solve(input).");
      JSON.stringify(solve(JSON.parse(${JSON.stringify(input)})));
    `
    const evaluation = context.evalCode(program, 'candidate.js')
    if (evaluation.error) {
      const dumped = context.dump(evaluation.error)
      evaluation.error.dispose()
      return { name: test.name, passed: false, message: safeMessage(dumped && dumped.message ? dumped.message : dumped) }
    }
    const json = context.getString(evaluation.value)
    evaluation.value.dispose()
    if (Buffer.byteLength(json, 'utf8') > MAX_RESULT_BYTES) {
      return { name: test.name, passed: false, message: 'Результат слишком большой.' }
    }
    let actual
    try { actual = JSON.parse(json) } catch { return { name: test.name, passed: false, message: 'solve должна вернуть JSON-совместимое значение.' } }
    return {
      name: test.name,
      passed: equals(actual, test.expected),
      message: equals(actual, test.expected) ? '' : `Ожидалось ${JSON.stringify(test.expected)}, получено ${JSON.stringify(actual)}`.slice(0, 500),
    }
  } finally {
    context.dispose()
    runtime.dispose()
  }
}

async function runTask(taskId, source) {
  if (!Object.hasOwn(taskSuites, taskId)) throw new Error('unknown_task')
  if (typeof source !== 'string' || Buffer.byteLength(source, 'utf8') > MAX_SOURCE_BYTES) throw new Error('invalid_source')
  const started = Date.now()
  // A separate Wasm module per request keeps linear memory isolated across candidates.
  const QuickJS = await newQuickJSWASMModule()
  const tests = []
  for (const test of taskSuites[taskId]) tests.push(await executeTest(QuickJS, source, test))
  return { passed: tests.every(test => test.passed), tests, durationMs: Date.now() - started }
}

module.exports = { runTask }

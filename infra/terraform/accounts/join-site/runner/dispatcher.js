'use strict'

const { getTask, LEGACY_TASK_SET_VERSION } = require('./tasks')
const { runTask, runCompiledTask, safeMessage } = require('./sandbox')

const MAX_SOURCE_BYTES = 65536

function unavailable() {
  const error = new Error('runtime_unavailable')
  error.code = 'runtime_unavailable'
  return error
}

function runtime(modulePath) {
  try {
    return require(modulePath)
  } catch (error) {
    // JS-only deployments must explicitly refuse mobile execution, not interpret
    // Kotlin or Swift as JavaScript or fabricate a successful report.
    if (error.code === 'MODULE_NOT_FOUND') throw unavailable()
    throw error
  }
}

async function runSubmission(taskId, source, taskSetVersion = LEGACY_TASK_SET_VERSION, language = 'javascript') {
  const task = getTask(taskSetVersion, taskId)
  if (!task) throw new Error('unknown_task')
  if (typeof language !== 'string' || !Object.hasOwn(task.languages, language)) throw new Error('invalid_language')
  if (typeof source !== 'string' || Buffer.byteLength(source, 'utf8') > MAX_SOURCE_BYTES) throw new Error('invalid_source')
  const started = Date.now()
  try {
    if (language === 'javascript') return await runTask(taskId, source, taskSetVersion)
    if (language === 'kotlin') {
      const compiled = await runtime('./kotlin-compile').compileKotlin(source)
      const result = await runCompiledTask(taskId, compiled, taskSetVersion)
      return { ...result, durationMs: Date.now() - started }
    }
    if (language === 'swift') {
      const tests = await runtime('./swift-runtime').runSwift(source, task.tests)
      if (!Array.isArray(tests) || tests.length !== task.tests.length || tests.some(test => typeof test.passed !== 'boolean')) {
        throw new Error('incomplete_runner_response')
      }
      const passed = tests.filter(test => test.passed).length
      return { passed, total: tests.length, allPassed: passed === tests.length, tests, durationMs: Date.now() - started }
    }
    throw unavailable()
  } catch (error) {
    if (error.code !== 'compilation_failed') throw error
    const message = safeMessage(`Ошибка компиляции: ${error.message}`)
    return {
      passed: 0, total: task.tests.length, allPassed: false,
      tests: task.tests.map(test => ({ name: test.name, passed: false, message })),
      durationMs: Date.now() - started,
    }
  }
}

module.exports = { runSubmission }

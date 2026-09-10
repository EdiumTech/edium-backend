const fs = require('node:fs')
const path = require('node:path')

const LEGACY_TASK_SET_VERSION = 'edium-js-2026-09-v1'
const catalogPath = process.env.CONTEST_CATALOG_PATH || path.join(__dirname, '../functions/app/contest_catalog.json')
const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'))

function getTask(taskSetVersion, taskId) {
  if (!Object.hasOwn(catalog.sets, taskSetVersion)) return undefined
  const taskSet = catalog.sets[taskSetVersion]
  const task = taskSet.tasks.find(task => task.id === taskId)
  if (!task) return undefined
  return {
    ...task,
    defaultLanguage: task.defaultLanguage || 'javascript',
    languages: task.languages || {
      javascript: { label: 'JavaScript', signature: task.signature, starterCode: task.starterCode },
    },
  }
}

function getTaskSuite(taskSetVersion, taskId) {
  return getTask(taskSetVersion, taskId)?.tests
}

// Older direct runner consumers retain the v1 suites; HTTP requests select a pinned version.
const taskSuites = Object.fromEntries(
  catalog.sets[LEGACY_TASK_SET_VERSION].tasks.map(task => [task.id, task.tests]),
)

module.exports = { getTask, getTaskSuite, taskSuites, LEGACY_TASK_SET_VERSION }

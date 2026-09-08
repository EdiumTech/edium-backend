const fs = require('node:fs')
const path = require('node:path')

const LEGACY_TASK_SET_VERSION = 'edium-js-2026-09-v1'
const catalogPath = process.env.CONTEST_CATALOG_PATH || path.join(__dirname, '../functions/app/contest_catalog.json')
const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'))

function getTaskSuite(taskSetVersion, taskId) {
  if (!Object.hasOwn(catalog.sets, taskSetVersion)) return undefined
  const taskSet = catalog.sets[taskSetVersion]
  return taskSet.tasks.find(task => task.id === taskId)?.tests
}

// Older direct runner consumers retain the v1 suites; HTTP requests select a pinned version.
const taskSuites = Object.fromEntries(
  catalog.sets[LEGACY_TASK_SET_VERSION].tasks.map(task => [task.id, task.tests]),
)

module.exports = { getTaskSuite, taskSuites, LEGACY_TASK_SET_VERSION }

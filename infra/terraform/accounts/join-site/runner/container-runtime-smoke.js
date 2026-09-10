'use strict'

// CI-only production image check. This file and the reference solutions are
// mounted read-only by the workflow and are never copied into the image.
const { runSubmission } = require('/app/dispatcher')
const { roleReferenceSolutions } = require('./role-reference-solutions.testdata')
const { mobileReferenceSolutions } = require('./mobile-reference-solutions.testdata')

const versions = {
  go: 'edium-go-2026-09-v4-backend',
  python: 'edium-python-2026-09-v4-ai-ml',
  kotlin: 'edium-mobile-2026-09-v3',
  swift: 'edium-mobile-2026-09-v3',
}

async function main() {
  let checks = 0
  const nativeReferences = { ...roleReferenceSolutions, ...mobileReferenceSolutions }
  for (const [taskId, sources] of Object.entries(nativeReferences)) {
    for (const [language, source] of Object.entries(sources)) {
      if (!versions[language]) continue
      const result = await runSubmission(taskId, source, versions[language], language)
      if (!result.allPassed || result.passed !== result.total) {
        process.stderr.write(`${JSON.stringify(result.tests)}\n`)
        throw new Error(`${language}/${taskId}: ${result.passed}/${result.total}`)
      }
      checks += result.total
      process.stdout.write(`PASS ${language}/${taskId}: ${result.passed}/${result.total}\n`)
    }
  }
  if (!checks) throw new Error('No native runtime checks were discovered')
  process.stdout.write(`PASS production image: ${checks} native checks\n`)
}

main().catch(error => {
  process.stderr.write(`${error.message}\n`)
  process.exitCode = 1
})

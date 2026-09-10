// Local transport only. Every language uses the same dispatcher and isolated
// execution path as production; Node never evaluates candidate source.
const { runSubmission } = require('../runner/dispatcher')

let size = 0
const chunks = []
process.stdin.on('data', chunk => {
  size += chunk.length
  if (size > 131072) process.exit(1)
  chunks.push(chunk)
})
process.stdin.on('end', async () => {
  try {
    const { taskId, source, taskSetVersion, language = 'javascript' } = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    const result = await runSubmission(taskId, source, taskSetVersion, language)
    process.stdout.write(JSON.stringify(result))
  } catch (error) {
    if (error?.code === 'runtime_unavailable') process.stdout.write(JSON.stringify({ error: 'runtime_unavailable' }))
    else process.exitCode = 1
  }
})

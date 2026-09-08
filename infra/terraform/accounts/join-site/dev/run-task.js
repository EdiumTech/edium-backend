// Local transport only. Candidate JavaScript is evaluated by the same QuickJS
// sandbox as production, never by Node's eval/vm/Function APIs.
const { runTask } = require('../runner/sandbox')

let size = 0
const chunks = []
process.stdin.on('data', chunk => {
  size += chunk.length
  if (size > 131072) process.exit(1)
  chunks.push(chunk)
})
process.stdin.on('end', async () => {
  try {
    const { taskId, source, taskSetVersion } = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    const result = await runTask(taskId, source, taskSetVersion)
    process.stdout.write(JSON.stringify(result))
  } catch {
    process.exitCode = 1
  }
})

const http = require('node:http')
const { runSubmission } = require('./dispatcher')
const { LEGACY_TASK_SET_VERSION } = require('./tasks')

const port = Number(process.env.PORT || 8080)
// JSON escaping can expand a permitted 64 KB source up to sixfold.
const MAX_REQUEST_BYTES = 6 * 65536 + 4096

function reply(response, status, payload) {
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  })
  response.end(JSON.stringify(payload))
}

const server = http.createServer((request, response) => {
  if (request.method !== 'POST' || request.url !== '/') return reply(response, 404, { message: 'not_found' })
  let size = 0
  const chunks = []
  request.on('data', chunk => {
    size += chunk.length
    if (size > MAX_REQUEST_BYTES) request.destroy()
    else chunks.push(chunk)
  })
  request.on('end', async () => {
    try {
      const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'))
      if (!payload || (payload.taskSetVersion !== undefined && typeof payload.taskSetVersion !== 'string')) throw new Error('unknown_task')
      // The previous API did not send a version; it can only address the original set.
      const result = await runSubmission(payload.taskId, payload.source, payload.taskSetVersion ?? LEGACY_TASK_SET_VERSION, payload.language ?? 'javascript')
      reply(response, 200, result)
    } catch (error) {
      const code = error.code === 'runtime_unavailable' ? 503 : ['unknown_task', 'invalid_source', 'invalid_language'].includes(error.message) ? 400 : 422
      // Deliberately do not log source, inputs, outputs, stack traces, or request headers.
      reply(response, code, { message: code === 400 ? error.message : code === 503 ? 'runtime_unavailable' : 'sandbox_error' })
    }
  })
})

if (require.main === module) server.listen(port, '0.0.0.0')

module.exports = { server }

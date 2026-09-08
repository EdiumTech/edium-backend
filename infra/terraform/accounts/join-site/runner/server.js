const http = require('node:http')
const { runTask } = require('./sandbox')

const port = Number(process.env.PORT || 8080)

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
    if (size > 131072) request.destroy()
    else chunks.push(chunk)
  })
  request.on('end', async () => {
    try {
      const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'))
      const result = await runTask(payload.taskId, payload.source)
      reply(response, 200, result)
    } catch (error) {
      const code = ['unknown_task', 'invalid_source'].includes(error.message) ? 400 : 422
      // Deliberately do not log source, inputs, outputs, stack traces, or request headers.
      reply(response, code, { message: code === 400 ? error.message : 'sandbox_error' })
    }
  })
})

server.listen(port, '0.0.0.0')

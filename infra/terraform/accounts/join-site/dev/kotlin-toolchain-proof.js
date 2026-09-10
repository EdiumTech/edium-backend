'use strict'

// This proof compiles a trusted reference function, then executes only inside
// the existing capability-free QuickJS engine. No candidate code runs in Node.
const assert = require('node:assert/strict')
const { newQuickJSWASMModule } = require('../runner/node_modules/quickjs-emscripten')
const { compileKotlin } = require('../runner/kotlin-compile')

const reference = `fun solve(input: Map<String, Any?>): Map<String, Any?> {
    val names = input["names"] as List<*>
    val threshold = (input["threshold"] as Number).toInt()
    val accepted = names.filterIsInstance<String>().filter { it.length >= threshold }.sorted()
    return mapOf("accepted" to accepted, "count" to accepted.size, "metadata" to input["metadata"])
}`

async function main() {
  const started = Date.now()
  const compiled = compileKotlin(reference, { compilerDirectory: process.argv[2], javaDirectory: process.argv[3] })
  const QuickJS = await newQuickJSWASMModule()
  const runtime = QuickJS.newRuntime()
  runtime.setMemoryLimit(64 * 1024 * 1024)
  runtime.setMaxStackSize(512 * 1024)
  const deadline = Date.now() + 750
  runtime.setInterruptHandler(() => Date.now() >= deadline)
  const context = runtime.newContext()
  try {
    const input = { names: ['Кот', 'Edium', 'Boost', null, 'AI'], threshold: 4, metadata: { ready: true, nothing: null, values: [1, -2.5, '🐈'] } }
    const result = context.evalCode(`${compiled}\nJSON.stringify(solve(${JSON.stringify(input)}));`, 'compiled-kotlin.js')
    if (result.error) {
      const error = context.dump(result.error)
      result.error.dispose()
      throw new Error(JSON.stringify(error))
    }
    const actual = JSON.parse(context.getString(result.value))
    result.value.dispose()
    assert.deepEqual(actual, { accepted: ['Boost', 'Edium'], count: 2, metadata: input.metadata })
    process.stdout.write(JSON.stringify({ kotlin: '2.2.21', compiledBytes: Buffer.byteLength(compiled), elapsedMs: Date.now() - started, output: actual }) + '\n')
  } finally {
    context.dispose()
    runtime.dispose()
  }
}

main().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1 })

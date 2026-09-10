'use strict'

// Local macOS compiler bridge. The compiler never executes the submitted code.
// Its standalone JS output must only be evaluated by the QuickJS runner.
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')

const KOTLIN_VERSION = '2.2.21'
const KOTLIN_SHA256 = 'a623871f1cd9c938946948b70ef9170879f0758043885bbd30c32f024e511714'
const MAX_SOURCE_BYTES = 65536
const MAX_COMPILED_BYTES = 4 * 1024 * 1024
const COMPILE_TIMEOUT_MS = 45000

function quoted(value) {
  if (typeof value !== 'string' || /[\r\n\0]/.test(value)) throw new Error('Invalid sandbox path')
  return JSON.stringify(value)
}

function compilerProfile({ compilerDirectory, javaDirectory, jobDirectory }) {
  return `(version 1)
(deny default)
(allow process-exec (literal ${quoted(path.join(javaDirectory, 'bin/java'))}))
(allow process-fork)
(allow signal (target self))
(allow sysctl-read)
(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo"))
(allow file-read-metadata)
; /System/Library/Sandbox/Profiles/dyld-support.sb documents that libignition
; opens the root directory as an openat base. This literal matches only the
; directory itself; descendant files remain denied by default.
(allow file-read-data (require-all (literal "/") (vnode-type DIRECTORY)))
(allow file-read* (subpath ${quoted(compilerDirectory)}) (subpath ${quoted(javaDirectory)})
  (subpath "/System/Library") (subpath "/usr/lib") (subpath "/private/var/db/dyld")
  (subpath "/System/Volumes/Preboot/Cryptexes/OS")
  (literal "/private/etc/passwd")
  (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random")
  (subpath ${quoted(jobDirectory)}))
(allow file-write* (subpath ${quoted(jobDirectory)}) (literal "/dev/null"))
`
}

function runtimeUnavailable() {
  const error = new Error('Kotlin пока недоступен: нужен настроенный изолированный компилятор.')
  error.code = 'runtime_unavailable'
  return error
}

function compileKotlin(source, {
  compilerDirectory = process.env.EDIUM_KOTLIN_HOME,
  javaDirectory = process.env.EDIUM_JAVA_HOME,
} = {}) {
  const supportedHost = (process.platform === 'darwin' && process.arch === 'arm64')
    || (process.platform === 'linux' && process.arch === 'x64')
  if (!supportedHost || !compilerDirectory || !javaDirectory) throw runtimeUnavailable()
  if (typeof source !== 'string' || Buffer.byteLength(source) > MAX_SOURCE_BYTES) throw new Error('invalid_source')
  try {
    compilerDirectory = fs.realpathSync(compilerDirectory)
    javaDirectory = fs.realpathSync(javaDirectory)
    if (process.platform === 'darwin') fs.accessSync('/usr/bin/sandbox-exec', fs.constants.X_OK)
    fs.accessSync(path.join(javaDirectory, 'bin/java'), fs.constants.X_OK)
    fs.accessSync(path.join(javaDirectory, 'lib/modules'), fs.constants.R_OK)
    for (const file of ['kotlin-preloader.jar', 'kotlin-compiler.jar', 'kotlin-stdlib-js.klib']) {
      fs.accessSync(path.join(compilerDirectory, 'lib', file), fs.constants.R_OK)
    }
    if (fs.readFileSync(path.join(compilerDirectory, 'build.txt'), 'utf8').trim() !== '2.2.21-release-469') throw runtimeUnavailable()
  } catch {
    throw runtimeUnavailable()
  }
  const jobDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-kotlin-job-'))
  const profile = process.platform === 'darwin' ? compilerProfile({ compilerDirectory, javaDirectory, jobDirectory }) : null
  const deadline = Date.now() + COMPILE_TIMEOUT_MS
  const common = [
    '-Xmx512m', '-Xms64m', '-XX:ActiveProcessorCount=2', '-XX:+UseSerialGC',
    '-XX:MaxMetaspaceSize=192m', '-XX:ReservedCodeCacheSize=64m',
    '-XX:-UsePerfData', '-XX:+DisableAttachMechanism',
    `-Djava.io.tmpdir=${jobDirectory}`, `-Duser.home=${jobDirectory}`, '-Duser.name=contest',
    '-Djava.awt.headless=true', '-Dfile.encoding=UTF-8',
    '-cp', path.join(compilerDirectory, 'lib/kotlin-preloader.jar'),
    'org.jetbrains.kotlin.preloading.Preloader', '-cp', path.join(compilerDirectory, 'lib/kotlin-compiler.jar'),
    'org.jetbrains.kotlin.cli.js.K2JSCompiler',
    '-kotlin-home', compilerDirectory,
    '-libraries', path.join(compilerDirectory, 'lib/kotlin-stdlib-js.klib'),
  ]
  function compilationFailure(message) {
    const error = new Error(message)
    error.code = 'compilation_failed'
    return error
  }
  function stage(args) {
    const remaining = deadline - Date.now()
    if (remaining <= 0) throw compilationFailure('Компиляция заняла слишком много времени.')
    const executable = process.platform === 'darwin' ? '/usr/bin/sandbox-exec' : path.join(javaDirectory, 'bin/java')
    const commandArgs = process.platform === 'darwin' ? ['-p', profile, path.join(javaDirectory, 'bin/java'), ...common, ...args] : [...common, ...args]
    const result = spawnSync(executable, commandArgs, {
      cwd: jobDirectory,
      env: { PATH: '/usr/bin:/bin', LANG: 'en_US.UTF-8', LC_ALL: 'en_US.UTF-8' },
      timeout: remaining, maxBuffer: 1024 * 1024, encoding: 'utf8',
    })
    if (result.error || result.status !== 0) {
      const diagnostic = `${result.stderr || ''}\n${result.stdout || ''}`
        // JDK 25 emits these startup notices before the useful Kotlin error.
        // Preserve compiler warnings/errors, source lines, and caret markers.
        .split('\n').filter(line => !/^WARNING: (?:A restricted method in java\.lang\.System|java\.lang\.System::load|Use --enable-native-access=ALL-UNNAMED|Restricted methods will be blocked|A terminally deprecated method in sun\.misc\.Unsafe|sun\.misc\.Unsafe::|Please consider reporting this to the maintainers of class com\.intellij\.)/.test(line)).join('\n').trim()
        .replaceAll(jobDirectory, '<contest>').replaceAll(compilerDirectory, '<compiler>').replaceAll(javaDirectory, '<java>').slice(0, 6000)
      if (['ENOENT', 'EACCES', 'EPERM'].includes(result.error?.code)
        || /sandbox-exec:|Error occurred during initialization of VM|Could not find or load main class/.test(diagnostic)) {
        throw runtimeUnavailable()
      }
      throw compilationFailure(result.error?.code === 'ETIMEDOUT' ? 'Компиляция заняла слишком много времени.' : diagnostic.trim() || 'Не удалось скомпилировать Kotlin. Проверь ограничения памяти и времени.')
    }
  }
  try {
    fs.writeFileSync(path.join(jobDirectory, 'Candidate.kt'), source)
    fs.copyFileSync(path.join(__dirname, 'kotlin-bridge.kt'), path.join(jobDirectory, 'Bridge.kt'))
    stage(['-Xir-produce-klib-file', '-ir-output-dir', jobDirectory, '-ir-output-name', 'candidate', 'Candidate.kt', 'Bridge.kt'])
    stage(['-Xir-produce-js', '-Xir-dce', '-Xinclude=' + path.join(jobDirectory, 'candidate.klib'),
      '-module-kind', 'plain', '-main', 'noCall', '-ir-output-dir', path.join(jobDirectory, 'js'), '-ir-output-name', 'ediumCandidate'])
    const adapter = '\nfunction solve(input) { return JSON.parse(ediumCandidate.runCase(JSON.stringify(input))); }\n'
    const artifactPath = path.join(jobDirectory, 'js/ediumCandidate.js')
    const resolvedArtifact = fs.realpathSync(artifactPath)
    if (!resolvedArtifact.startsWith(jobDirectory + path.sep)) throw compilationFailure('Некорректный результат компиляции.')
    const descriptor = fs.openSync(artifactPath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK)
    try {
      // Size-check the opened regular file before allocating its contents.
      const metadata = fs.fstatSync(descriptor)
      if (!metadata.isFile() || metadata.size > MAX_COMPILED_BYTES - Buffer.byteLength(adapter)) {
        throw compilationFailure('Скомпилированный код слишком большой.')
      }
      const compiled = fs.readFileSync(descriptor, 'utf8')
      return compiled + adapter
    } finally {
      fs.closeSync(descriptor)
    }
  } finally {
    fs.rmSync(jobDirectory, { recursive: true, force: true })
  }
}

module.exports = { compileKotlin, compilerProfile, KOTLIN_VERSION, KOTLIN_SHA256, MAX_COMPILED_BYTES }

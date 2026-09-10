'use strict'

// Only synthetic canaries are probed. This never attempts to read a user secret.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { compilerProfile } = require('../runner/kotlin-compile')

const compilerDirectory = fs.realpathSync(process.argv[2])
const javaDirectory = fs.realpathSync(process.argv[3])
const proofDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-kotlin-isolation-'))
const jobDirectory = path.join(proofDirectory, 'job')
fs.mkdirSync(jobDirectory)
const outsideRead = path.join(proofDirectory, 'synthetic-outside-canary.txt')
const outsideWrite = path.join(proofDirectory, 'forbidden-write.txt')
const toolchainWrite = path.join(compilerDirectory, `.edium-sandbox-proof-${process.pid}.txt`)
fs.writeFileSync(outsideRead, 'synthetic canary; contains no actual secret')
assert.equal(fs.existsSync(toolchainWrite), false)
const program = `import java.nio.file.*;
import java.net.*;
import java.util.*;
class Probe {
  interface Action { void run() throws Exception; }
  static void denied(String name, Action action) throws Exception {
    try { action.run(); } catch (java.io.IOException expected) {
      if (!expected.toString().contains("Operation not permitted") && !expected.toString().contains("Permission denied")) throw expected;
      System.out.println(name + ": denied"); return;
    }
    throw new AssertionError(name + " was allowed");
  }
  public static void main(String[] args) throws Exception {
    denied("outside-read", () -> Files.readString(Path.of(args[0])));
    denied("outside-write", () -> Files.writeString(Path.of(args[1]), "canary"));
    denied("toolchain-write", () -> Files.writeString(Path.of(args[2]), "canary"));
    denied("network", () -> { try (Socket socket = new Socket()) { socket.connect(new InetSocketAddress("127.0.0.1", 9), 500); } });
    denied("child-executable", () -> new ProcessBuilder("/usr/bin/true").start());
    // CoreFoundation adds this non-secret locale variable while launching Java.
    if (!System.getenv().keySet().equals(Set.of("PATH", "LANG", "LC_ALL", "__CF_USER_TEXT_ENCODING"))) throw new AssertionError("unexpected environment names: " + System.getenv().keySet());
    System.out.println("environment: sanitized");
    Files.writeString(Path.of("job-write.txt"), "allowed");
    System.out.println("job-write: allowed");
  }
}
`
try {
  fs.writeFileSync(path.join(jobDirectory, 'Probe.java'), program)
  const result = spawnSync('/usr/bin/sandbox-exec', [
    '-p', compilerProfile({ compilerDirectory, javaDirectory, jobDirectory }),
    path.join(javaDirectory, 'bin/java'), '-Xmx128m', '-XX:-UsePerfData', '-XX:+DisableAttachMechanism', '-Djdk.lang.Process.launchMechanism=FORK',
    `-Djava.io.tmpdir=${jobDirectory}`, `-Duser.home=${jobDirectory}`, '-Duser.name=contest',
    'Probe.java', outsideRead, outsideWrite, toolchainWrite,
  ], { cwd: jobDirectory, env: { PATH: '/usr/bin:/bin', LANG: 'en_US.UTF-8', LC_ALL: 'en_US.UTF-8' },
    timeout: 20000, maxBuffer: 1024 * 1024, encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr || result.error?.message || `signal ${result.signal}`)
  assert.equal(fs.existsSync(outsideWrite), false)
  assert.equal(fs.existsSync(toolchainWrite), false)
  assert.equal(fs.readFileSync(path.join(jobDirectory, 'job-write.txt'), 'utf8'), 'allowed')
  process.stdout.write(result.stdout)
} finally {
  fs.rmSync(proofDirectory, { recursive: true, force: true })
}

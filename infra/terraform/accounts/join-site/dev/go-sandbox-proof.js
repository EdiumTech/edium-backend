'use strict'

// Fixed native diagnostic for Seatbelt itself; candidate Go always runs in WASI.
// Every outside file is a synthetic canary created for this proof.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { compilerProfile } = require('../runner/go-runtime')
const goDirectory = fs.realpathSync(process.env.EDIUM_GO_HOME)
const proofDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-go-isolation-'))
const jobDirectory = path.join(proofDirectory, 'job')
fs.mkdirSync(jobDirectory)
const outsideRead = path.join(proofDirectory, 'outside-canary.txt')
const outsideWrite = path.join(proofDirectory, 'outside-write.txt')
const toolchainWrite = path.join(goDirectory, `.edium-sandbox-canary-${process.pid}`)
const runtimeCache = path.join(proofDirectory, 'host-cache-canary.txt')
const executable = path.join(jobDirectory, 'trusted-proof')
fs.writeFileSync(outsideRead, 'synthetic, no secrets')
fs.writeFileSync(runtimeCache, 'synthetic, no executable cache')
assert.equal(fs.existsSync(toolchainWrite), false)
const probe = `
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
void denied(const char *name, int result) {
  if (result >= 0 || (errno != EPERM && errno != EACCES)) { fprintf(stderr, "%s: wrong result %d errno %d\\n", name, result, errno); exit(1); }
  printf("%s: denied\\n", name);
}
int main(int argc, char **argv) {
  if (argc != 5) return 2;
  denied("outside-read", open(argv[1], O_RDONLY));
  denied("outside-write", open(argv[2], O_CREAT | O_WRONLY, 0600));
  denied("toolchain-write", open(argv[3], O_CREAT | O_WRONLY, 0600));
  denied("runtime-cache-read", open(argv[4], O_RDONLY));
  int descriptor = socket(AF_INET, SOCK_STREAM, 0);
  if (descriptor < 0) denied("network", descriptor);
  else {
    struct sockaddr_in address = {0}; address.sin_family = AF_INET; address.sin_port = htons(9); address.sin_addr.s_addr = htonl(0x7f000001);
    denied("network", connect(descriptor, (struct sockaddr *)&address, sizeof(address))); close(descriptor);
  }
  pid_t child = fork(); if (child < 0) return 3;
  if (!child) { execl("/usr/bin/true", "/usr/bin/true", (char *)NULL); _exit(errno == EPERM || errno == EACCES ? 0 : 1); }
  int status = 0; waitpid(child, &status, 0);
  if (!WIFEXITED(status) || WEXITSTATUS(status)) return 4;
  puts("outside-executable: denied");
  if (getenv("EDIUM_SYNTHETIC_PARENT_SECRET")) return 5;
  puts("environment: sanitized");
  int output = open("allowed.txt", O_CREAT | O_WRONLY, 0600); if (output < 0) return 6; close(output);
  puts("job-write: allowed"); return 0;
}
`
try {
  const built = spawnSync('/usr/bin/clang', ['-x', 'c', '-', '-o', executable], { input: probe, encoding: 'utf8', timeout: 20000, maxBuffer: 1048576 })
  assert.equal(built.status, 0, built.stderr || built.error?.message)
  const profile = compilerProfile({ goDirectory, jobDirectory }) + `\n(allow process-exec (literal ${JSON.stringify(executable)}))\n`
  const result = spawnSync('/usr/bin/sandbox-exec', ['-p', profile, executable, outsideRead, outsideWrite, toolchainWrite, runtimeCache], {
    cwd: jobDirectory, env: { LANG: 'en_US.UTF-8' }, timeout: 10000, maxBuffer: 16384, encoding: 'utf8',
  })
  assert.equal(result.status, 0, result.stderr || result.error?.message || `signal ${result.signal}`)
  assert.equal(fs.existsSync(outsideWrite), false)
  assert.equal(fs.existsSync(toolchainWrite), false)
  assert.equal(fs.existsSync(path.join(jobDirectory, 'allowed.txt')), true)
  process.stdout.write(result.stdout)
} finally { fs.rmSync(proofDirectory, { recursive: true, force: true }) }

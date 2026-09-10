'use strict'

// A tiny, trusted native diagnostic probes only synthetic canaries. Candidate
// Swift programs never execute natively: swift-runtime compiles them to Wasm.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { compilerProfile } = require('../runner/swift-runtime')

const toolchainDirectory = fs.realpathSync(process.env.EDIUM_SWIFT_HOME)
const sdkDirectory = fs.realpathSync(process.env.EDIUM_SWIFT_SDK)
const proofDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-swift-isolation-'))
const jobDirectory = path.join(proofDirectory, 'job')
fs.mkdirSync(jobDirectory)
const outsideRead = path.join(proofDirectory, 'synthetic-outside-canary.txt')
const outsideWrite = path.join(proofDirectory, 'forbidden-write.txt')
const toolchainWrite = path.join(toolchainDirectory, `.edium-sandbox-proof-${process.pid}.txt`)
const cacheRead = path.join(proofDirectory, 'synthetic-runtime-cache.txt')
const executable = path.join(jobDirectory, 'trusted-probe')
fs.writeFileSync(outsideRead, 'synthetic canary; contains no actual secret')
fs.writeFileSync(cacheRead, 'synthetic cache; contains no executable code')
assert.equal(fs.existsSync(toolchainWrite), false)
const program = `
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
static void denied(const char *name, int result) {
  if (result >= 0 || (errno != EACCES && errno != EPERM)) { fprintf(stderr, "%s unexpectedly allowed or wrong error %d\\n", name, errno); exit(1); }
  printf("%s: denied\\n", name);
}
int main(int argc, char **argv) {
  if (argc != 5) return 2;
  denied("outside-read", open(argv[1], O_RDONLY));
  denied("outside-write", open(argv[2], O_CREAT | O_WRONLY, 0600));
  denied("toolchain-write", open(argv[3], O_CREAT | O_WRONLY, 0600));
  denied("runtime-cache-read", open(argv[4], O_RDONLY));
  int network = socket(AF_INET, SOCK_STREAM, 0);
  if (network < 0) denied("network", network);
  else {
    struct sockaddr_in address = {0}; address.sin_family = AF_INET; address.sin_port = htons(9); address.sin_addr.s_addr = htonl(0x7f000001);
    denied("network", connect(network, (struct sockaddr *)&address, sizeof(address)));
    close(network);
  }
  pid_t pid = fork();
  if (pid < 0) return 3;
  if (pid == 0) { execl("/usr/bin/true", "/usr/bin/true", (char *)NULL); _exit(errno == EACCES || errno == EPERM ? 0 : 1); }
  int status = 0; waitpid(pid, &status, 0);
  if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) return 4;
  puts("outside-executable: denied");
  if (getenv("EDIUM_SYNTHETIC_PARENT_SECRET") != NULL) return 5;
  puts("environment: sanitized");
  int file = open("allowed-job-write.txt", O_CREAT | O_WRONLY, 0600);
  if (file < 0) return 6;
  close(file);
  puts("job-write: allowed");
  return 0;
}
`
try {
  const compilation = spawnSync('/usr/bin/clang', ['-x', 'c', '-', '-o', executable], {
    input: program, encoding: 'utf8', timeout: 20000, maxBuffer: 1048576,
  })
  assert.equal(compilation.status, 0, compilation.stderr || compilation.error?.message)
  // Permit only this trusted proof binary in addition to the production policy.
  // All tested filesystem/network rules are exactly the production ones.
  const profile = compilerProfile({ toolchainDirectory, sdkDirectory, jobDirectory }) + `\n(allow process-exec (literal ${JSON.stringify(executable)}))\n`
  const result = spawnSync('/usr/bin/sandbox-exec', ['-p', profile, executable, outsideRead, outsideWrite, toolchainWrite, cacheRead], {
    cwd: jobDirectory, env: { LANG: 'en_US.UTF-8' }, timeout: 10000, maxBuffer: 16384, encoding: 'utf8',
  })
  assert.equal(result.status, 0, result.stderr || result.error?.message || `signal ${result.signal}`)
  assert.equal(fs.existsSync(outsideWrite), false)
  assert.equal(fs.existsSync(toolchainWrite), false)
  assert.equal(fs.existsSync(path.join(jobDirectory, 'allowed-job-write.txt')), true)
  process.stdout.write(result.stdout)
} finally {
  fs.rmSync(proofDirectory, { recursive: true, force: true })
}

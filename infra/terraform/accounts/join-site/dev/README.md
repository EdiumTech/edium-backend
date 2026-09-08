# Local contest preview

Run from this `join-site` directory with Python 3.10+ and Node.js:

```sh
# Once, when the existing runner dependencies have not yet been installed:
npm --prefix runner ci

python3 dev/demo_server.py
```

For actual mobile test execution, first configure the isolated [Kotlin](KOTLIN-TOOLCHAIN.md)
and [Swift](SWIFT-TOOLCHAIN.md) toolchains in this process's environment, then start
the same command. Swift runs include native compilation and Wasmtime startup and
can take around 20–30 seconds per full suite on the verified local machine.

The API binds only to `127.0.0.1:8787`. Start the landing frontend at
`http://127.0.0.1:4173`, then open <http://127.0.0.1:8787/> for links to all five
direction-specific candidate previews. The HR screen is
<http://127.0.0.1:4173/join/admin/>; its local key is `demo-admin`.

Five synthetic candidates have an invitation; a sixth has no contest so that
HR can try creating one. Invitations use the real token generation code and stay
stable across demo restarts. To print them without starting a server:

```sh
python3 dev/demo_server.py --links
```

Run a no-network smoke check of all directions, full test counts, deliberately
wrong code, cross-direction access, stale saves, code-only submission and HR
review/create flows:

```sh
python3 dev/smoke_demo.py

# On a host with both isolated native toolchains provisioned:
python3 dev/smoke_demo.py --tracks mobile --require-native
```

The preview calls the production `handler.api` and contest domain functions with
an in-memory repository. Start, save, optimistic revisions, submit, review, invite
creation, revoke and extend use the actual API contracts. The test button runs
the complete real task suite through `dev/run-task.js` and the production
language dispatcher: JavaScript in QuickJS, Kotlin/Swift only when their isolated
toolchains are available. Missing native runtimes produce an explicit 503 and
the default smoke reports a skip; `--require-native` makes that a failure.

Production invitation creation defaults to JavaScript capability only. A mobile
invitation requires both `kotlin` and `swift` in the API's `RUNNER_LANGUAGES`
comma-separated environment setting, otherwise it returns 503 before saving the
contest or queuing an invitation email. Configure that setting only after the
deployed isolated runner supports both languages. Existing JavaScript invitation
behavior is unchanged. The local demo overrides the declared capability list so
both mobile editors can be previewed before installation; that override does
not simulate compilation or successful tests.

All changes disappear when the API restarts. The demo does not load cloud SDKs,
use production credentials, connect to cloud services, send email, or store real
applications. Candidate code is never evaluated by Node.js. CORS accepts only
the frontend origin above. Resume download returns a synthetic text file;
application and resume upload endpoints are outside this preview.

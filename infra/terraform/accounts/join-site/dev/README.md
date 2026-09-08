# Local contest preview

Run from this `join-site` directory with Python 3.10+ and Node.js:

```sh
# Once, when the existing runner dependencies have not yet been installed:
npm --prefix runner ci

python3 dev/demo_server.py
```

The API binds only to `127.0.0.1:8787`. Start the landing frontend at
`http://127.0.0.1:4173`, then open <http://127.0.0.1:8787/> for links to all seven
direction-specific candidate previews. The HR screen is
<http://127.0.0.1:4173/join/admin/>; its local key is `demo-admin`.

Seven synthetic candidates have an invitation; an eighth has no contest so that
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
```

The preview calls the production `handler.api` and contest domain functions with
an in-memory repository. Start, save, optimistic revisions, submit, review, invite
creation, revoke and extend use the actual API contracts. The test button runs
the complete real task suite in the existing isolated QuickJS sandbox through
`dev/run-task.js`, including failures and the actual passed/total counts.

All changes disappear when the API restarts. The demo does not load cloud SDKs,
use production credentials, connect to cloud services, send email, or store real
applications. Candidate code is never evaluated by Node.js. CORS accepts only
the frontend origin above. Resume download returns a synthetic text file;
application and resume upload endpoints are outside this preview.

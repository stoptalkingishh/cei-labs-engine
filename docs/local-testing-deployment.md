# Local test deployment (Docker Desktop, single node)

This documents standing up the full stack on a single developer machine for
testing — not a venue deployment. It records the path actually exercised on
2026-07-22 against a Windows machine running Docker Desktop (Swarm mode
already active, single node), including the real problems that came up and
how they were fixed, so the next person doing this doesn't rediscover them.

## When to use this vs. the venue path

`scripts/stack-up.sh`'s own printed next-steps assume an operator: complete
CTFd's interactive setup wizard by hand, generate an admin API token through
the UI, then run `scripts/challenges-load.sh`. That's the right path for a
real event. For repeated local test spins, the interactive wizard is the
main friction point — this doc's Path B bootstraps CTFd's admin account
non-interactively instead, so the whole thing is scriptable and repeatable
without a browser.

## Prerequisites

- Docker Desktop with Swarm mode active (`docker info --format
  '{{.Swarm.LocalNodeState}}'` should print `active`; if not,
  `scripts/stack-up.sh` will run `docker swarm init` for you).
- `ctfcli` (`ctf` command) installed and on `PATH`. On Windows/Git Bash it
  may land under the Python user-site `Scripts` directory rather than a
  standard location — check `python -m pip show ctfcli` if `ctf` isn't
  found, and add that directory to `PATH` for the session.
- `python3` on `PATH`. Git Bash on Windows may only expose `python` — if so,
  create a `python3` shim (e.g. a small wrapper script on `PATH` that execs
  `python`) rather than editing anything in this repo.
- A sibling checkout of `CEI-Labs-Wargames` (challenge content lives there,
  not in this repo).

## 1. Configure

```bash
cp docker/.env.example docker/.env
cp -r docker/secrets.example docker/secrets
```

Fill in `docker/.env`:
- `BASE_DOMAIN=ctf.local` is fine for local testing.
- `GITHUB_ORG=local-test` and `IMAGE_TAG=dev` if you're building images
  locally rather than pulling from GHCR (see step 2) — `docker stack deploy`
  will otherwise try to pull a real `ghcr.io/<org>/...` image and fail.

Replace every `CHANGE_ME` placeholder in `docker/secrets/*.txt` with a real
random value (`openssl rand -hex 32` per file is fine for a local test —
these are never committed, `docker/secrets/` is gitignored). There are six:
`ctf_key.txt`, `ctfd_db_password.txt`, `ctfd_db_root_password.txt`,
`ctfd_secret_key.txt`, `orchestrator_admin_password.txt`,
`plugin_shared_secret.txt`.

**If you're redeploying after a previous local attempt**, don't assume old
secrets and volumes still agree with each other. A stale `ctfd_db_data`
volume has the *old* DB password baked into MariaDB's own first-run init —
regenerating `docker/secrets/ctfd_db_password.txt` without also wiping that
volume produces `Access denied for user 'ctfd'` on every CTFd start. If in
doubt: `./scripts/stack-down.sh` (or `docker stack rm cei-labs`) then remove
the stack's named volumes before redeploying clean.

## 2. Build images locally (if not pulling from GHCR)

With `GITHUB_ORG=local-test` / `IMAGE_TAG=dev`, build and tag the three
custom images yourself using the same build contexts CI uses
(`.github/workflows/build-*.yml` for the exact context/Dockerfile paths):

```bash
docker build -t ghcr.io/local-test/cei-labs-engine/ctfd:dev          -f docker/ctfd/Dockerfile docker/ctfd
docker build -t ghcr.io/local-test/cei-labs-engine/orchestrator:dev  -f docker/orchestrator/Dockerfile docker/orchestrator
docker build -t ghcr.io/local-test/cei-labs-engine/tcp-gateway:dev   -f docker/tcp-gateway/Dockerfile docker/tcp-gateway
```

(Confirm exact paths against the actual workflow files if they've moved —
don't copy this blindly.)

## 3. Deploy

```bash
./scripts/stack-up.sh
```

This sources `docker/.env`, labels the local node `ctfd-data=true`, and runs
`docker stack deploy`. Watch for all five services (`ctfd`, `ctfd-db`,
`ctfd-redis`, `orchestrator`, `traefik`) to reach `1/1`:

```bash
docker stack services cei-labs
```

## 4. Reach it — use the IP, not `localhost`

Traefik's routing rule is `Host(\`ctfd.${BASE_DOMAIN}\`) ||
HostRegexp(\`^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$\`)` (see `docker/stack.yml`) —
it matches the configured domain **or a raw dotted-quad IP**, not the
literal string `localhost`. For a local test:

- `https://127.0.0.1/` works directly (self-signed cert, browser will warn
  — expected).
- `https://localhost/` does **not** match the rule and won't route.
- `https://ctfd.ctf.local/` works if you add a `127.0.0.1 ctfd.ctf.local`
  line to your hosts file.

## 5. Bootstrap CTFd and load challenges

**Path A (venue-realistic):** open the reachable URL, complete CTFd's setup
wizard by hand, generate an admin API token from the UI, then:
```bash
export CTFD_URL=https://ctfd.ctf.local
export CTFD_ADMIN_TOKEN=...
./scripts/challenges-load.sh
```

**Path B (non-interactive, repeatable local testing):** bootstrap the admin
account programmatically instead of through the wizard — CTFd's first-run
`/setup` endpoint accepts a POST with the same fields the wizard form
submits (name, email, password, CSRF nonce). `CEI-Labs-Wargames/scripts/
local-ctfd/setup_local_ctfd.py` does something close to this for a lighter
CTFd-only test stack; it needs adapting for this repo's full production
`stack.yml` (TLS endpoint, real secrets) rather than using it unmodified.
Generate a strong random admin password per spin-up — don't reuse one
across environments.

One easy-to-miss CTFd behavior either path can hit: CTFd's `Authorization:
Token ...` header is only honored on requests that also send `Content-Type:
application/json` (see CTFd's `tokens()` before-request hook). Without it
the token is silently ignored and you get redirected to the login page
instead of a clean 401 — worth checking first if a script's requests are
mysteriously landing on `/login`.

Then upload the actual challenge content from the sibling `CEI-Labs-Wargames`
checkout:
```bash
cd ../CEI-Labs-Wargames
CTFD_URL=https://<your-url> CTFD_TOKEN=<admin-token> CTFD_SYNC_SECRET=<from docker/secrets/plugin_shared_secret.txt> ./deploy.sh
```

## 6. Verify

Don't trust "containers are running" — confirm the application layer:

```bash
curl -sk https://127.0.0.1/api/v1/challenges -H "Authorization: Token $CTFD_ADMIN_TOKEN" -H "Content-Type: application/json" | jq '.data | length'
# expect 59 (35 Bandit + 16 Natas + 8 Krypton)
```

and confirm login actually works (nonce → `POST /login` → session cookie →
`GET /api/v1/users/me` returns the admin identity), not just that the login
page renders.

## Known limitation of this setup

A single-node Docker Desktop Swarm satisfies every placement constraint the
stack declares (`node.role == manager`, `node.labels.ctfd-data == true` —
auto-labeled onto the sole node by `stack-up.sh`). It does **not** exercise
multi-node scheduling/placement — that only gets tested on the real
multi-node Fedora Swarm target. Don't treat a clean local single-node
deployment as proof the stack behaves correctly across multiple nodes.

## Not covered here

`cei-labs-net`, `cei-labs-event`, the Juice Shop on-demand instance-launch
flow, and bulk `scripts/spawn-workspaces.sh` analyst workstations weren't
exercised in the session this doc is based on.

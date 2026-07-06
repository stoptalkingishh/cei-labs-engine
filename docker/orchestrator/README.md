# Challenge Instance Orchestrator

Generalized replacement for MultiJuicer. Spins up per-team, on-demand Docker
Swarm services — today for Juice Shop, later for target+attacker wargame
pairs — and tears them down after a configurable idle period. Internal-only:
it is never reachable from Traefik/the internet, only from CTFd's
`instance-launcher` plugin (docker/ctfd/plugins/instance-launcher, Phase 4)
over the `orchestrator-internal` overlay network.

## Auth

Every `/instances*` route requires:

```
X-Orchestrator-Auth: <contents of the plugin_shared_secret Docker secret>
```

Every `/admin/*` route requires:

```
X-Admin-Auth: <contents of the orchestrator_admin_password Docker secret>
```

`/healthz` requires nothing.

## API

### `POST /instances`

Create-or-get one team's instance. Idempotent — calling this again for the
same `owner_id`/`instance_key` just refreshes its idle timer (this is how the
CTFd plugin should "touch" an instance every time a participant opens the
challenge page).

```jsonc
{
  "type": "web-app",            // "web-app" | "single-target" | "target-attacker"
  "owner_id": "team-42",        // CTFd team/user id — isolates instances per owner
  "instance_key": "juice-shop", // logical challenge slug — a team can have
                                 // multiple concurrent instances of different challenges
  "spec": {
    // web-app / single-target:
    "image": "bkimminich/juice-shop:v17.1.1",
    "port": 3000,                // default 3000
    "env": { "KEY": "value" },   // optional

    // target-attacker:
    "target_image": "ghcr.io/org/cei-labs-engine/otw-target:latest",
    "attacker_image": "ghcr.io/org/cei-labs-engine/ctf-kali-novnc:latest",
    "attacker_port": 6080,       // default 6080
    "target_env": {},
    "attacker_env": {}
  }
}
```

Response `201` (created) or `200` (already existed):

```jsonc
// web-app / single-target
{ "status": "created", "type": "web-app", "access": { "url": "https://team-42-juice-shop.apps.ctf.local" } }

// target-attacker
{ "status": "created", "type": "target-attacker", "access": { "attacker_url": "https://team-42-otw-range.apps.ctf.local" } }
```

`503` if at `ORCHESTRATOR_MAX_INSTANCES` capacity, `400` for a malformed spec.

### `GET /instances/<owner_id>/<instance_key>`

Status + access info for an existing instance. Also touches its idle timer.
`404` if it doesn't exist.

### `DELETE /instances/<owner_id>/<instance_key>`

Tears down all containers (and, for `target-attacker`, the per-team network)
immediately. Used for a participant-facing "reset my instance" action.

### `GET /admin/instances` / `DELETE /admin/instances/<owner_id>/<instance_key>`

Same shapes, admin-authenticated, for a future ops dashboard.

## Isolation model

- `web-app`/`single-target` containers join only the shared `challenge-edge`
  network (so Traefik can route to them).
- `target-attacker` containers get a **fresh overlay network created at
  spin-up and destroyed at teardown**, named `chnet-<owner>-<key>`. The
  target container joins *only* that network — never `challenge-edge` —  so
  it is unreachable from Traefik, other teams, or anything but its own
  paired attacker. The attacker joins both (its own team network, to reach
  the target, plus `challenge-edge`, so Traefik can route to its noVNC port).

## Idle reaping

A background thread sweeps every `ORCHESTRATOR_REAP_INTERVAL_SECONDS`
(default 60) and tears down anything idle longer than
`ORCHESTRATOR_IDLE_GRACE_MINUTES` (default 120) — mirrors MultiJuicer's
`cleanup.gracePeriod`. State is in-memory only (single replica, pinned to a
manager node); a restart resets idle timers for whatever's still running in
Docker, it never loses or orphans a real instance.

## Local development

```bash
python -m venv .venv && .venv/Scripts/activate  # or source .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q
```

The test suite (`tests/`) never touches a real Docker daemon — `FakeDockerOrchestratorClient`
(`tests/fakes.py`) stands in for it, so `instance_types`, `controller`, and
`reaper` logic can all be verified without a swarm.

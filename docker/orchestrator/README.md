# Challenge Instance Orchestrator

Generalized replacement for MultiJuicer. Spins up per-team, on-demand Docker
Swarm services — today for Juice Shop, and for on-demand target+attacker
wargame ranges — and tears them down after a configurable idle period or a
post-solve countdown. Internal-only: it is never reachable from Traefik/the
internet, only from CTFd's `instance-launcher` plugin
(docker/ctfd/plugins/instance-launcher) over the `orchestrator-internal`
overlay network.

## Auth

Every `/instances*` and `/ranges*` route requires:

```
X-Orchestrator-Auth: <contents of the plugin_shared_secret Docker secret>
```

Every `/admin/*` route requires:

```
X-Admin-Auth: <contents of the orchestrator_admin_password Docker secret>
```

`/healthz` requires nothing.

## Instance types

- **web-app** — one container, HTTP-routed through Traefik on the shared
  `challenge-edge` network. Used for Juice Shop.
- **single-target** — one container on its own dedicated, airgapped
  (`internal: true`) network, reachable only via a directly published port
  (SSH by default). No Traefik involvement.
- **target-attacker** — a per-team **range**: one shared attacker (browser
  noVNC, Traefik-routed) plus any number of targets, all sharing one
  persistent, airgapped network scoped to the *team* (`owner_id`), not the
  individual challenge. The first target-attacker challenge a team launches
  creates the range; every subsequent one just adds another target to it.
  A target is only ever reachable from its own range's attacker — never
  Traefik, other teams, or CTFd.

## API

### `POST /instances`

Create-or-get one team's instance. Idempotent — calling this again for the
same `owner_id`/`instance_key` just refreshes its idle timer (the CTFd
plugin does this every time a participant opens the challenge page).

```jsonc
{
  "type": "web-app",            // "web-app" | "single-target" | "target-attacker"
  "owner_id": "team-42",        // CTFd team/user account_id — isolates instances per owner
  "instance_key": "juice-shop", // logical challenge slug — a team can have
                                 // multiple concurrent instances of different challenges
  "relaunch": false,            // true = "Relaunch Environment": tear down + recreate fresh
  "spec": {
    // web-app:
    "image": "bkimminich/juice-shop:v17.1.1",
    "port": 3000,                // default 3000
    "env": { "KEY": "value" },

    // single-target:
    "image": "ghcr.io/org/cei-labs-engine/target-base-linux:latest",
    "target_port": 22,           // default 22, the port INSIDE the container
    "protocol": "ssh",           // default "ssh", purely informational for the UI
    "env": {},

    // target-attacker:
    "target_image": "ghcr.io/org/cei-labs-engine/otw-target:latest",
    "attacker_image": "ghcr.io/org/cei-labs-engine/ctf-kali-novnc:latest", // only used the FIRST time this team launches a range
    "attacker_port": 6080,
    "target_env": {},
    "attacker_env": {}
  }
}
```

Responses (`201` created / `200` already existed or relaunched):

```jsonc
// web-app
{ "status": "created", "type": "web-app", "access": { "url": "https://team-42-juice-shop.apps.ctf.local" } }

// single-target
{ "status": "created", "type": "single-target",
  "access": { "connect_host": "ctf.local", "connect_port": 32000, "protocol": "ssh", "note": "..." } }

// target-attacker
{ "status": "created", "type": "target-attacker",
  "access": { "attacker_url": "https://team-42-attacker.apps.ctf.local",
              "target_hostname": "chrange-team-42-otw-range-target",
              "note": "Target is reachable only from your attacker workstation, at the hostname above." } }
```

`503` if at `ORCHESTRATOR_MAX_INSTANCES` capacity or the SSH port range is
exhausted; `400` for a malformed spec.

### `GET /instances/<owner_id>/<instance_key>`

Status + access info. Touches the idle timer. Includes `shutdown_at` /
`extensions_used` if a post-solve countdown is active. `404` if it doesn't exist.

### `DELETE /instances/<owner_id>/<instance_key>`

Tears down immediately: the container(s) this specific instance owns, and
(for `single-target`) its dedicated network + published port. For
`target-attacker`, only removes THIS challenge's target — the range's shared
attacker/network are untouched (see `DELETE /ranges/<owner_id>` for that).

### `POST /instances/<owner_id>/<instance_key>/reboot`

**"Reboot Host"** — restarts the container(s) in place (`docker service
update --force`equivalent): same identity, same network/port, no state
carried over inside the container. Faster than a relaunch, useful when a
participant wedges a service without needing a fresh image pull/reschedule.

### `POST /instances/<owner_id>/<instance_key>/schedule-shutdown`

Starts (or restarts) a countdown to automatic teardown. Called by the CTFd
plugin the moment it observes a correct flag submission for the mapped
challenge.

```jsonc
{ "delay_seconds": 30 }  // optional, defaults to ORCHESTRATOR_SHUTDOWN_DELAY_SECONDS
```

### `POST /instances/<owner_id>/<instance_key>/extend-shutdown`

The participant-facing "keep it running 5 more minutes" action.

```jsonc
{ "extend_seconds": 300 }  // optional, defaults to ORCHESTRATOR_SHUTDOWN_EXTEND_SECONDS
```

`409` if no shutdown is currently pending, or if
`ORCHESTRATOR_SHUTDOWN_MAX_EXTENSIONS` extensions have already been used
(default 3, i.e. 15 extra minutes max).

### `POST /ranges/<owner_id>/attacker/reboot`

Reboots just the shared attacker workstation for a team's range, in place.

### `DELETE /ranges/<owner_id>`

Full range teardown: every target the team currently has, the shared
attacker, and the range's network.

### `GET /admin/instances`, `GET /admin/ranges`, `DELETE /admin/instances/<owner>/<key>`, `DELETE /admin/ranges/<owner>`

Same shapes as above, admin-authenticated, for an ops dashboard.

## Isolation model (airgapping)

- `web-app` containers join only the shared `challenge-edge` network, which
  is itself `internal: true` (Docker-level: no outbound route to the
  internet at all) — Traefik still reaches them because routing-mesh/published
  ports operate independently of a network's `internal` flag.
- `single-target` containers get their own dedicated `internal: true`
  network, existing solely for that one instance, plus a directly published
  port. No other container — including other teams' targets — is ever on
  that network.
- `target-attacker` ranges get one dedicated `internal: true` network per
  **team** (not per challenge). The attacker joins that network plus
  `challenge-edge` (for Traefik); every target in that range joins **only**
  the range's network. Nothing outside a team's own range can ever reach
  that team's targets, and the targets themselves have no path to the
  internet or to CTFd.

## Idle reaping and shutdown countdowns

A background thread sweeps every `ORCHESTRATOR_REAP_INTERVAL_SECONDS`
(default 60) and does two independent things:

1. Tears down any instance or range idle longer than
   `ORCHESTRATOR_IDLE_GRACE_MINUTES` (default 120) — mirrors MultiJuicer's
   `cleanup.gracePeriod`. An instance on an active shutdown countdown is
   exempt from idle-reaping (it's already scheduled to go).
2. Tears down any instance whose `schedule-shutdown` deadline has passed.

State is in-memory only (single replica, pinned to a manager node); a
restart resets idle timers and cancels pending countdowns for whatever's
still running in Docker — it never loses or orphans a real instance.

## Local development

```bash
python -m venv .venv && .venv/Scripts/activate  # or source .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q
```

The test suite (`tests/`) never touches a real Docker daemon —
`FakeDockerOrchestratorClient` (`tests/fakes.py`) stands in for it, so
`instance_types`, `controller`, `reaper`, and `ports` logic can all be
verified without a swarm. **Not covered by these tests** (needs a real
swarm): actual container scheduling/networking behavior, `force_update()`
restart semantics, and Swarm's routing-mesh reachability guarantees —
verify those against a real deployment before relying on them for an event.

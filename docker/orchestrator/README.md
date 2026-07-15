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

- **web-app** — one vulnerable container on a dedicated internal network.
  A capability-free, read-only TCP gateway joins that network and
  `challenge-edge`; Traefik routes to the gateway, never to the vulnerable
  workload. Used for Juice Shop.
- **single-target** — one container on its own dedicated, airgapped
  (`internal: true`) network. A trusted gateway owns the published port and
  forwards only the configured protocol/port to the target. No Traefik
  involvement.
- **target-attacker** — a per-team **range**: one shared attacker (browser
  noVNC, Traefik-routed) plus any number of targets, all sharing one
  persistent, airgapped network scoped to the *team* (`owner_id`), not the
  individual challenge. The participant-owned attacker remains only on that
  network; a trusted gateway owns its SSH, noVNC, and Traefik exposure. The
  first target-attacker challenge a team launches
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

- Every untrusted `web-app` joins only its own dedicated `internal: true`
  network. Traefik reaches a hardened gateway on `challenge-edge`; the app
  never joins a shared overlay or Swarm ingress network.
- `single-target` containers get their own dedicated `internal: true`
  network, existing solely for that one instance. A hardened gateway, not the
  target, owns the directly published port. No other untrusted container —
  including other teams' targets — is ever on that network.
- `target-attacker` ranges get one dedicated `internal: true` network per
  **team** (not per challenge). The attacker and every target join **only**
  that range network. A non-root, read-only, capability-free gateway joins
  the range plus `challenge-edge`, disables IPv4/IPv6 forwarding, and proxies
  only fixed listener/destination pairs. Nothing outside a team's own range
  can directly reach its workloads.

The gateway design removes participant-controlled workloads from shared
overlays and Swarm ingress. Its isolation claims still require the documented
live checks on the release platform: external access succeeds through the
gateway; target egress, cross-team reach, management-plane reach, and route
abuse through the gateway all fail; and the gateway runs non-root with a
read-only root filesystem, zero capabilities, and forwarding sysctls disabled.

## Configuring multi-challenge deployments (for the wargames repo)

Not every CTF maps one challenge to one container. The `instance_group` and
`shutdown_on_solve` fields (set per challenge, synced from challenge YAML by
`scripts/challenges-load.sh`, stored in the CTFd plugin's
`InstanceChallengeConfig` table — see
`docker/ctfd/plugins/instance-launcher/models.py`) let a content repo like
`CEI-Labs-Wargames` describe several different shapes with the same
mechanism, no orchestrator changes required:

**1. One challenge, one box** (the default — omit both fields):

```yaml
name: "Natas-style Web Challenge"
instance_type: single-target
image: "ghcr.io/org/cei-labs-engine/otw-target:natas0"
```

**2. One challenge, one target+attacker pair:**

```yaml
name: "Intro Recon Range"
instance_type: target-attacker
target_image: "ghcr.io/org/cei-labs-engine/otw-target:recon1"
attacker_image: "ghcr.io/org/cei-labs-engine/ctf-kali-novnc:latest"
```

**3. Several challenges, one shared box** (a boot2root with multiple
flags at different privilege levels) — give them the same `instance_group`;
the environment only auto-shuts-down once every one of them is solved:

```yaml
# challenge A
name: "Boot2Root — User Flag"
instance_type: single-target
image: "ghcr.io/org/cei-labs-engine/wargame-box3:latest"
instance_group: "box3"
---
# challenge B — same box, same group, launched from either challenge's page
name: "Boot2Root — Root Flag"
instance_type: single-target
image: "ghcr.io/org/cei-labs-engine/wargame-box3:latest"
instance_group: "box3"
```

**4. A range with multiple distinct targets behind one attacker** — this
needs no extra field at all: give each challenge its own `instance_key`
(automatic, derived from challenge id) but the same team naturally shares
one attacker/network already (see "Instance types" above), so a team
launching "Range Target A" and "Range Target B" gets one Kali workstation
that can reach both targets.

**5. Combine 3 and 4** — some challenges in a range share a target
(`instance_group` set, `instance_type: target-attacker`), others in the same
range get their own dedicated target — mix and match per challenge.

**Opting out of auto-shutdown for a specific challenge:**

```yaml
name: "Recon Stage (don't tear down after this one)"
instance_type: target-attacker
target_image: "..."
attacker_image: "..."
shutdown_on_solve: false
```

That challenge's own solve never starts a countdown, though it can still be
a member of an `instance_group` gating a *different* challenge's shutdown.

Constraint: all challenges sharing an `instance_group` should declare the
same `instance_type` and images — whichever one is launched first is what
actually creates the container; the others just reuse it.

## Idle reaping and shutdown countdowns

A background thread sweeps every `ORCHESTRATOR_REAP_INTERVAL_SECONDS`
(default 60) and does two independent things:

1. Tears down any instance or range idle longer than
   `ORCHESTRATOR_IDLE_GRACE_MINUTES` (default 120) — mirrors MultiJuicer's
   `cleanup.gracePeriod`. An instance on an active shutdown countdown is
   exempt from idle-reaping (it's already scheduled to go).
2. Tears down any instance whose `schedule-shutdown` deadline has passed.

State is stored in SQLite and the Swarm stack mounts it from the persistent
`orchestrator_data` volume. Routine service restarts therefore retain instance,
range, port, idle-timer, and shutdown-countdown state. Disaster recovery onto a
clean cluster may intentionally discard active labs, but must remove all
label-managed resources and require clean participant relaunches; see
`docs/backup-and-recovery.md`.

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

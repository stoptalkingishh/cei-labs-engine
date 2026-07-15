# Network Prerequisites

CEI Labs Engine is network-agnostic — it runs on any Docker Swarm with
outbound internet access and the ports below reachable. This doc lists
exactly what a front-end network needs to provide; it does not assume or
require any specific firewall/router product.

## Ports

| Port(s) | Protocol | Direction | Purpose |
| :--- | :--- | :--- | :--- |
| 80, 443 | TCP | Inbound to any Swarm node | Traefik ingress (CTFd + Traefik-routed challenge instances). Swarm's routing mesh means any node's IP works, regardless of where the container is actually scheduled. |
| `ORCHESTRATOR_SSH_PORT_RANGE_START`–`END` (default `30000`–`32767`) | TCP | Inbound to any Swarm node | Directly-published ports for `single-target` orchestrator instances (e.g. SSH challenges) and bulk-spawned analyst/Kali workspaces (`scripts/spawn-workspaces.sh`, via `ANALYST_BASE_PORT`). Both provisioning paths share this one range — see `docker/.env.example`. |
| 2377/tcp, 7946/tcp+udp, 4789/udp | — | Between Swarm nodes only | Swarm cluster management, node gossip, and overlay network data path (VXLAN). Only relevant for multi-node deployments — irrelevant on a single-host swarm. |

## Swarm ingress capacity

Direct TCP ports belong only to hardened gateway services, but each such
service uses Swarm ingress. The release station exhausted Docker's default
`10.0.0.0/24` ingress allocator during repeated launch/delete testing even
though inspection showed only two live endpoints. Provision a non-overlapping
ingress subnet with substantially more headroom (the tested station uses a
`/16`) before an event, and record the chosen subnet in the station runbook.

Changing ingress is a maintenance operation: every service that publishes a
port must first be removed or stopped, including Traefik, and the stack is
unavailable until ingress and those services are recreated. Confirm the new
subnet does not overlap the LAN, VPNs, Docker address pools, or any CEI Labs
overlay. Afterward, the release gate must prove external gateway access,
target egress denial, cross-tenant denial, management-plane denial, and clean
launch/delete/relaunch behavior.

## DNS

Traefik routes on hostname, not bare IP (with a `HostRegexp` fallback for
direct-IP access — see `docker/stack.yml`'s `ctfd` router rule). Whatever
sits in front of this stack needs to resolve, **as a wildcard**:

- `${BASE_DOMAIN}` (bare) and `ctfd.${BASE_DOMAIN}` — the CTFd scoreboard
- `*.apps.${BASE_DOMAIN}` — per-team orchestrator-launched instance
  subdomains (e.g. `team-42-juice-shop.apps.${BASE_DOMAIN}`), generated on
  demand, not knowable in advance

A single non-wildcard DNS record only covers the scoreboard and will
silently break every orchestrator-launched challenge instance for players.

## TLS

LAN/air-gapped default (`USE_LETSENCRYPT=false`): Traefik presents a
self-signed or staff-provided cert (`docker/traefik/certs/`,
`docker/traefik/dynamic/tls.yml.example`) — expect a browser warning for
players, which is normal for this deployment mode. Internet-facing events
should set `USE_LETSENCRYPT=true` with a real wildcard DNS record instead.

Any tooling that talks to CTFd's API against a self-signed cert (e.g.
`ctfcli`-based content pipelines) needs its own TLS-trust decision — that's
outside this repo's scope; see whatever tool you're using for its
certificate-verification override.

## Reference implementation

[`cei-labs-net`](https://github.com/stoptalkingishh/cei-labs-net) is one
worked example of a front-end network satisfying all of the above — a
pfSense/OPNsense-based VLAN layout with per-player bandwidth shaping, DNS
interception, and QoS prioritization for a high-density event. It is not a
dependency of this repo; any network meeting the requirements above works
equally well.

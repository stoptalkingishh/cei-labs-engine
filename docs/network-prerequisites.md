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

## Stable access endpoint

**The failure this section prevents:** an earlier event advertised a
station address of `192.168.1.131` in participant launch instructions.
That address was later reassigned away from the station (DHCP renewal /
NIC change), so the advertised endpoint timed out for players mid-event,
while a direct virtual-switch address (`172.20.10.2`) still worked locally.
The root problem was not the code — `BASE_DOMAIN` (`docker/.env`,
consumed by `Config.BASE_DOMAIN` in `docker/orchestrator/app/config.py`)
is already a configurable value, never a hardcoded IP anywhere in this
repo — it was that a person copied a point-in-time LAN IP into a
participant-facing document and it was never revisited.

**The fix is operational, not architectural:**

- Always give players the DNS/hostname form: `https://ctfd.${BASE_DOMAIN}/`
  (see the DNS section above for what must resolve, as a wildcard). Do not
  hand out a bare IP as the primary instructions unless there genuinely is
  no DNS for this event.
- If a bare-IP fallback is unavoidable (no DNS available), Traefik's
  `HostRegexp` rule on the `ctfd` router already accepts it — but treat
  that IP as **perishable, event-day state**, not a fact to freeze into a
  handout. Re-check and re-document it immediately before each event,
  every time, from the actual running station — never reuse a value from
  a previous event's notes.
- **Current deployment network, confirmed live 2026-08-05:** the live
  Fedora Swarm nodes now live on the OPNsense LAN/server network
  `192.168.10.0/24`, gateway `192.168.10.1` (OPNsense). This replaces the
  earlier `192.168.1.0/24` station network and is **not** the older
  five-VLAN / VLAN-20 reference topology. Player Wi-Fi stays separate on
  `10.10.32.0/22` and does not host Swarm nodes. As of 2026-08-05,
  `192.168.10.192` is the confirmed SSH-reachable server candidate (Docker
  engine / Swarm ports not yet open); `192.168.10.235` is a DHCP/lease
  candidate whose OS is unconfirmed. The operator laptop at
  `192.168.10.120` is explicitly excluded from the Swarm stack; do not count
  Docker Desktop on that laptop as CEI Labs capacity. See
  `ansible/inventory-fedora-live.ini` for the live inventory template.
  Verify each node's OS identity and SSH credentials before relying on any
  address, and re-verify with `ip addr` / `hostname -I` on the manager node
  before each event — do not assume this document is current.
- Before an event: from a clean client on the player network, confirm the
  documented hostname (or, if unavoidable, IP) actually resolves/reaches
  CTFd, SSH, and noVNC — this is the acceptance check called out in the
  P1 fix notes. Reboot or reconnect the station and re-run the same check;
  update the documented endpoint immediately if it changed.

### Live Fedora pool update - 2026-08-05

This section supersedes the earlier point-in-time candidate notes above.

The current intended local Fedora server pool is:

- `192.168.10.13` (`cei-ryzen5-61g-swarm01`) - primary manager candidate;
  Ryzen 5 7600, 61 GiB RAM, Docker installed, stale one-node Swarm currently
  advertises `192.168.1.173`
- `192.168.10.11` (`cei-i7-31g-swarm02`) - worker candidate; Intel
  i7-10750H, 31 GiB RAM, Docker installed, stale one-node Swarm currently
  advertises `192.168.1.98`
- `192.168.10.192` (`cei-xeon-e3-8g-swarm03`) - worker candidate; Xeon
  E3-1240 v2, 7.7 GiB RAM, Docker not installed
- `192.168.10.112` (`cei-ryzen5-15g-swarm04`) - worker candidate; Ryzen 5
  1600X, 15 GiB RAM, Docker not installed. Proposed static target
  `192.168.10.12` is not reachable yet.

Do not mark the Swarm deployable until:

- Luna has configured static DHCP reservations or static addressing for these
  four hosts in OPNsense.
- Outbound IPv4 internet works from each Fedora host.
- Existing stale one-node Swarms are reset/reinitialized with current
  `192.168.10.x` advertise addresses.
- Docker is installed on `cei-xeon-e3-8g-swarm03`.
- SSH from the deployment workstation works for any additional accepted host.

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

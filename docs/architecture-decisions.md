# Architecture decisions

Lightweight ADR log for choices worth a written record — not every change,
just the ones a future contributor (or a future us) would otherwise have to
reverse-engineer from git history. Numbered, append-only; a superseded
decision gets a new entry that says so, the old one isn't deleted.

## ADR-001: Orchestration platform is Docker Swarm, not K3s

**Status:** Accepted (resolved 2026-07-12, written up 2026-07-15)

**Context:** Early planning notes and the project's own GitHub repository
description referred to a "K3s mini-PC cluster." The actual deployment
tooling — `docker/stack.yml` (a Swarm stack file), `ansible/roles/swarm/`
(which replaced earlier `k3s-server`/`k3s-agent`/`metallb` roles), and the
top-level `README.md` — has described and implemented Swarm for some time.
This left the repository's own public-facing description out of sync with
what the code actually does, which the production-readiness tracker flagged
as a P0 item ("resolve the orchestration source of truth").

**Decision:** Docker Swarm is the one supported production topology.
Swarm's routing mesh replaces the need for MetalLB, and since every image
is already published to GHCR by CI there's no need for an internally
distributed registry either — both were K3s-era dependencies this design
no longer carries. There is no tier to choose between: the same
`docker/stack.yml` deploys to one machine or many; adding capacity is
`docker swarm join` plus a re-run of `stack-up.sh` (or `ansible/site.yml`
for a whole inventory), with nothing in the stack definition changing.

**Consequence:** Any remaining reference to K3s (repo description,
planning docs, diagrams outside this repo) should be updated to say Swarm,
or removed if it no longer reflects an active option. No code or ansible
role changes are implied by this decision — it is a documentation-
consistency fix, not an open engineering item.

## ADR-002: Docker socket access for Traefik and the orchestrator

**Status:** Accepted, with a follow-up recommendation not yet scheduled

**Context:** `docker/stack.yml` mounts `/var/run/docker.sock` into two
services:

- `traefik` — **read-only** (`:ro`). Required because Traefik's Swarm
  provider (`--providers.swarm.endpoint=unix:///var/run/docker.sock`)
  discovers routable services by reading Swarm service labels directly
  from the Docker API; there is no other supported way for Traefik to
  learn what's running without a separate service-discovery system this
  project doesn't otherwise need.
- `orchestrator` — **read-write**. Required because the challenge-instance
  orchestrator's entire job is creating and removing per-team challenge
  containers/networks on demand (the MultiJuicer-equivalent role) — that
  is only possible through the Docker API. `stack.yml` already carries an
  inline comment on this service acknowledging the implication: read-write
  socket access is "equivalent to root on whichever node it runs on,"
  the same trust boundary a Kubernetes ServiceAccount with pod-create
  permission would represent.

Neither container runs with `privileged: true`, and this was previously
flagged as an open item in the production-readiness tracker's risk
register ("Docker socket exposed to Traefik (ro) and orchestrator (rw)
without written justification") because the justification existed only as
a scattered inline comment, not a discoverable written record.

**Decision:** Both mounts are accepted as necessary for the current
architecture. Compensating controls already in place: both services are
constrained to `node.role == manager` placement (`deploy.placement.
constraints`), the orchestrator is only reachable from CTFd's
instance-launcher plugin over the internal-only `orchestrator-internal`
network authenticated with `plugin_shared_secret` (never exposed to
Traefik or the public edge), and it carries its own memory limit/
restart policy like every other service in the stack.

**Not yet done — a real follow-up, not a rubber stamp:** the orchestrator
currently has unrestricted read-write access to the entire Docker Engine
API, when in practice it only ever needs a narrow slice of it (create/
list/remove containers and networks matching its own naming convention).
A Docker socket proxy (e.g. the common `docker-socket-proxy` pattern,
sitting between the orchestrator and the real socket, allow-listing only
the specific API endpoints it uses) would shrink that blast radius
meaningfully without changing the orchestrator's own code. This is
scoped as a P1 hardening item, not required for the current risk
acceptance to stand, and is not scheduled yet.

# Natas target URL depends on DNS that isn't available when running independent of the full network setup (2026-07-24)

Reported: running independent of the venue network setup (`cei-labs-net`),
there is no DNS server available, and the URL Natas gives for its target
cannot be resolved. Notes only, no code changed.

## Two different DNS dependencies exist in this stack -- they need to be told apart

### 1. `attacker_url` (reaching the attacker workstation itself) -- has a known, documented, already-shipped DNS dependency and fallback

`cei-labs-engine/docker/orchestrator/app/instance_types.py` builds
`attacker_url` as `https://{hostname}`, where `hostname` is a
`*.apps.<base_domain>` wildcard name routed through Traefik. The code's
own comment is explicit about this:

```python
# Traefik's route depends on `*.apps.<base_domain>` actually resolving
# (cei-labs-net's DNS, or some other wildcard DNS aimed at the swarm) --
# with no wildcard DNS available at all, that route is simply
# unreachable, and unlike single-target (which only ever used a bare
# published port to begin with) there was no fallback.
```

This is a recorded, intentional finding from earlier work on the
engine, not new. It's why the orchestrator also generates a
DNS-independent fallback: `novnc_url` (a direct `https://<base_domain>:<port>/vnc.html`
link, self-signed TLS, "no DNS required" per its own `novnc_note`
field) and `connect_host`/`connect_port` for raw SSH. Confirmed this
fallback is actually surfaced to players, not just generated and
unused: `challenge-launch.js`'s `renderAccess()` renders an "Open
Attacker Workstation (direct link)" button from `access.novnc_url`
directly under the primary DNS-based button whenever it's present, and
a Host/Port block for SSH.

So: if `attacker_url` doesn't resolve, the launch panel already shows a
working direct-link alternative -- this specific dependency was
identified and mitigated previously. If this is what's actually being
hit, the existing "(direct link)" button is the intended workaround,
not a new gap.

### 2. `target_hostname` (reaching the Natas target FROM INSIDE the attacker) -- designed to need no external DNS at all

Every Natas hint/description in `CEI-Labs-Wargames/scripts/build_natas.py`
tells the player to browse to `http://<target-host>:800N/` **from
inside the attacker workstation**, not from their own machine. The
orchestrator populates that `<target-host>` value as `target_hostname`
-- confirmed in `instance_types.py`, it's the Docker Swarm **service
name** of the target container (`naming.range_target_service_name(...)`),
displayed as plain text ("Target (from inside the attacker only):
`<name>`"), not a link.

Docker Swarm overlay networks run an embedded DNS server for every
service attached to them -- resolving a sibling service's name to its
virtual IP is a built-in feature of the overlay network itself and does
not depend on `cei-labs-net`, a wildcard domain, or any external
resolver. As long as the attacker container and the target container
are both attached to the same `range_network` (which the orchestrator
creates per team), this resolution is expected to work with zero
external DNS infrastructure.

This means "no DNS server available, running independent of the net
setup" should not, by this design, affect reaching the Natas target
from inside the attacker -- that resolution never leaves the Swarm
overlay network. If it's actually failing, the likely causes are
environmental rather than something in the challenge/hint content
itself:

- The attacker and target containers aren't actually joined to the
  same overlay network (e.g. a non-Swarm `docker run`/`docker compose`
  setup that doesn't reproduce `range_network`'s Swarm-managed service
  discovery the same way plain user-defined bridge networks do -- not
  every Docker networking mode provides embedded DNS the same way).
- Docker's own embedded DNS resolver (normally at `127.0.0.11` inside
  each container on a user-defined/overlay network) isn't reachable
  from inside the attacker container for some other local reason.

This doc doesn't attempt to diagnose which of those applies to the
reporter's specific setup -- that would need live inspection of the
running attacker/target containers and their networks, which wasn't
done here per the docs-only scope of this pass.

## Update 2026-07-24: the documented "(direct link)" fallback was tried live and did not work either

Following this doc's own suggested workaround, the reporter tried the
`novnc_url` direct-link fallback (`https://192.168.1.173:<novnc_port>/vnc.html`,
reached via the "Open Attacker Workstation (direct link)" button
described above) against the live instance on `192.168.1.173`. It did
not work.

This is a new, unverified data point, recorded as-is -- no live
debugging was done in this pass (no container/network/port inspection
on `192.168.1.173`, no check of what error the browser actually showed
for that URL). It's not yet known whether the failure is:

- the same DNS-independent fallback path being broken for an unrelated
  reason (e.g. the cert/TLS handshake itself, the port not actually
  being published/reachable from outside the swarm, `tcp-gateway` not
  forwarding correctly), or
- a difference between this doc's read of the source (`instance_types.py`,
  `challenge-launch.js`) and what's actually deployed/running on
  `192.168.1.173` right now (e.g. an older image predating this
  fallback -- `banner-art-not-deployed-gap-2026-07-24.md` already
  documented a separate instance of the running box lagging behind
  `main`), or
- something specific to the reporter's own network path to
  `192.168.1.173` unrelated to this stack at all.

None of these were distinguished in this pass. Closing this now
additionally needs live reproduction against the actual running
instance: what URL was opened, what error/behavior the browser showed
(connection refused vs. cert error vs. blank page vs. something else),
and whether `access.novnc_url` as returned by the orchestrator API for
that specific instance actually matches the URL that was tried.

## What closing this needs

- Confirm which of the two dependencies above is actually being hit:
  is it the launch-panel URL for the attacker workstation itself
  (`attacker_url`, which already has a documented fallback), or the
  in-challenge target URL shown once inside the attacker
  (`target_hostname`, which is designed to need no external DNS at
  all)?
- If it's `target_hostname`: verify live, on the actual environment
  being run "independent of the net setup," that the attacker and
  target containers are on the same Docker network and that container
  DNS (`127.0.0.11`) is reachable and functioning from inside the
  attacker container. This is an environment/deployment-mode check,
  not a code change to this repo or `CEI-Labs-Wargames`.
- If it's `attacker_url`: this is already covered by the existing
  "(direct link)" `novnc_url` button and the Host/Port SSH fallback --
  worth confirming the reporter actually saw and could use those before
  treating this as an open gap.

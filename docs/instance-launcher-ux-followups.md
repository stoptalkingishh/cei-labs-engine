# Instance-Launcher UX Follow-Ups (notes, not yet planned/executed)

Found while a real user tried to actually launch and play Bandit through
the live CTFd UI (not a scripted test) after Phases 1-8 of the self-hosted
wargames migration were otherwise verified working. These are real gaps in
the *player experience*, not caught by any of the automated/scripted
verification in that work, because scripted testing drove the plugin's
HTTP endpoints directly instead of navigating CTFd's UI the way a person
actually would.

**Status: capturing findings only. Not planned, not executed, not verified.**
Do not treat anything below as done.

## 1. The "Launch Environment" flow isn't discoverable from the challenge itself

`docker/ctfd/plugins/instance-launcher` has no frontend integration with
CTFd's own challenge-view modal (checked: no JS override, no injected
button — the plugin's only UI is its own standalone `launch.html` at
`/plugins/instance-launcher/launch/<challenge_id>`). A player reading a
challenge in the normal CTFd UI has no link, button, or visible path to
that page at all. Every challenge description in `CEI-Labs-Wargames`
*says* "launch your environment" but never links to where.

**Confirmed real:** a user pointed at the live Bandit 0->1 challenge and
could not find any way to start an instance.

## 2. Feature request: a "Start Here" onboarding challenge, one per track (Bandit, Krypton, AND Natas)

Proposed by the user, scope confirmed as **all three tracks, not just
Bandit**: a zero'th challenge in each of Bandit, Krypton, and Natas, name
like "<Track>: Start Here," whose entire job is teaching the launch UI
itself — explain what Launch/Reboot/Relaunch/Extend each do, have the
player click through the actual flow once, award points just for
successfully reaching a running instance. Same underlying mechanism
("build out that same functionality") across all three, not three
independently-designed challenges.

Would need, per track:
- New challenge content in `CEI-Labs-Wargames` (category, description,
  probably no real flag-based solve — or a trivial one, e.g. submit
  something the launch page itself displays once the instance is up).
- Wired to that track's own shared `instance_group` (`bandit`, `krypton`,
  `natas`) so it demonstrates the *actual* mechanism each track already
  uses, not a fake/separate one — Bandit/Krypton are `single-target`,
  Natas is `target-attacker` (range + attacker), so the Natas version of
  this challenge needs to account for that difference (e.g. showing both
  the attacker-workstation link and, once item 4 below is resolved, the
  target reachability info) rather than being a copy-paste of Bandit's.

This depends on item 1 (the launch flow becoming a real, linkable/
embeddable part of the challenge experience) being solved first — a
"Start Here" challenge that still points players at an undiscoverable
standalone page doesn't actually fix the underlying problem, it just
narrows where the problem is first encountered.

## 3. Challenge descriptions don't show connection info because there isn't any to show statically

Bandit 0->1's description says "connect via SSH to the host/port CTFd
shows you" but the *port is dynamically allocated at launch time* — it
genuinely cannot be known until the instance exists, so it can't be baked
into static challenge.yml text. This is a structural consequence of item 1:
once the launch page is actually reachable and shows `ssh ... -p <port>`,
this stops being confusing — but until item 1 is fixed, the description
reads as if it should already know the connection info and doesn't say
where to find it.

## 4. CTFd needs a real DNS name (from `cei-labs-net` if present) or a clean IP-only fallback

**Partially already true, confirmed by reading `docker/stack.yml`:** CTFd's
own scoreboard route already has a raw-IP fallback —
`Host(\`ctfd.${BASE_DOMAIN}\`) || HostRegexp(\`^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$\`)`
— so hitting Traefik directly by IP reaches CTFd correctly even with no DNS
at all.

**Confirmed gap:** the *dynamically-generated per-team subdomains*
(`*.apps.${BASE_DOMAIN}`, used by `target-attacker`'s noVNC attacker
workstation — i.e. Natas) have **no IP fallback at all**. Without either
`cei-labs-net`'s DNS interception (or *any* wildcard DNS pointed at the
swarm) or a manually-added hosts-file entry per team, Natas's attacker
workstation is simply unreachable in an IP-only deployment. Bandit/Krypton
(`single-target`) don't have this problem — they connect via bare IP +
published port, no hostname involved at all.

Needs figuring out:
- Whether `cei-labs-net`'s DNS interception is the *only* supported way to
  get wildcard resolution, or whether there should be a simpler built-in
  fallback (e.g. CTFd/orchestrator settings that work in "no wildcard DNS
  available" mode — maybe forcing Natas ranges to also get a directly
  published port instead of relying on a subdomain, mirroring the SSH fix
  already made for the attacker in Phase 6).
- What "if the network repo is never installed" should concretely mean for
  `cei-labs-engine`'s own setup docs/`.env.example` — right now
  `network-prerequisites.md` documents `cei-labs-net` as *a* reference
  implementation, not *the* dependency, but doesn't spell out what to do
  operationally if you skip it.

## 5. The launch page should show a simple instance status

Right now `launch.html` shows connection info once an instance exists, or
an error if creation failed — nothing in between. A player clicking
"Launch" while the container is still pulling/starting (which took
anywhere from a few seconds to ~30s+ for the larger images during this
session's own testing) sees nothing indicating progress. Should show
something like: not started / starting / running / error, ideally without
requiring a manual page refresh.

## Suggested next step

Ask the user whether to move straight into planning solutions for these
(likely deserves its own `EnterPlanMode` pass, similar to the Phase 6-8
work, given items 1 and 5 are real plugin/frontend changes and item 4
needs a real architectural decision) or to keep collecting more findings
first.

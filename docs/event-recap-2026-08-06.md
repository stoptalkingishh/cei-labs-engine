---
title: "Event Recap — 2026-08-06 CEI Labs Wargames"
tags: [postmortem, event, wargames, docker-swarm, ctfd, cei-labs]
status: active
created: 2026-08-06
---

# Event Recap: CEI Labs Wargames (2026-08-06)

## Headline Facts

| Item | Value |
|------|-------|
| Date | 2026-08-06 |
| Infrastructure | 3-node Docker Swarm, manager `192.168.1.150`, workers `.193`, `.125` |
| CEI Stack | CTFd 3.8.6 (teams mode), MariaDB, Redis, Orchestrator, Traefik — all `1/1` |
| Games | Bandit (35), Krypton (8), Natas (16), AI Copilot (6) — 65 challenges total |
| Players | 18 users, 10 teams |
| Total submissions | Unknown (API rate-limited during capture) |
| Platform host | `192.168.1.150` (cei-ryzen5-61g-swarm01) |

## Timeline

### 2026-08-05 — Server LAN & OPNsense Discovery

- **21:22 UTC** — Four Fedora server candidates identified on `192.168.10.0/24`: `.235`, `.112`, `.192`, `.67`. All pingable but no admin ports reachable from the operator workstation.
- **21:55 UTC** — SSH credentials supplied; three servers reachable. Discovered existing stale single-node Swarms on `.13` (cei-ryzen5-61g-swarm01) and `.11` (cei-i7-31g-swarm02), both advertising dead `192.168.1.x` addresses. Server internet through OPNsense non-functional.
- **22:03 UTC** — Fedora stations renamed per processor/RAM convention:
  - `192.168.10.13` → `cei-ryzen5-61g-swarm01` (AMD Ryzen 5, 61 GiB)
  - `192.168.10.11` → `cei-i7-31g-swarm02` (Intel Core i7, 31 GiB)
  - `192.168.10.192` → `cei-xeon-e3-8g-swarm03` (Intel Xeon E3, 8 GiB)
  - `192.168.10.112` → `cei-ryzen5-15g-swarm04` (AMD Ryzen 5, 15 GiB) — found later at 22:26
- **00:00 UTC (Aug 6)** — Static IPs applied; MTU 1400 set for Wi-Fi WAN compatibility; stale Wi-Fi routes disabled on manager. Swarm not yet rebuilt.
- **00:25 UTC** — Swarm rebuilt as 2-node: `.13` (manager leader) + `.11` (worker). Nodes `.10` (swarm03) and `.12` (swarm04) pending Docker installation.

### 2026-08-06 — OPNsense Troubleshooting & Subnet Migration

- **02:44 UTC** — Attempted OPNsense repair for server internet. Management ports unreachable from agent sandbox (false negatives). Correct state discovered outside sandbox: HTTPS reachable.
- **03:06 UTC** — Verified OPNsense management was actually reachable. Manager `.13` had competing Wi-Fi route; forced it through OPNsense, but server internet still failed — confirming the issue was OPNsense upstream config.
- **Key decision: subnet re-home.** Instead of continuing to fight the dead OPNsense upstream path on `192.168.10.0/24`, the Swarm was re-homed to `192.168.1.0/24` where the upstream gateway was functional.

### 2026-08-06 — Swarm Subnet Re-home (major infra surgery)

- **~15:00 UTC** — Fizz recreated the swarm on `192.168.1.0/24` with manager `.150`, workers `.193`, `.125`.
- **Critical issues found and fixed:**
  1. **Stale advertise address:** Manager still advertised `192.168.10.13:2377` even after re-IP. Workers joined ok but were told to reach manager at an unreachable address. Fixed by re-initializing with `--advertise-addr 192.168.1.150`.
  2. **Stack redeploy:** Re-init wiped the swarm-level stack definition. Redeployed from `/home/ismaelrodriguez/deployments/engine-31a6471`, updated `ORCHESTRATOR_OFFLINE_HOST` to `192.168.1.150`.
  3. **CTFd DB password mismatch:** Persistent DB volume held old passwords. Recovered with a temporary MariaDB rescue container — no data purged.
- **Result:** 3-node swarm healthy, CEI stack all 1/1, CTFd login working at `https://192.168.1.150`.

### 2026-08-06 — CTFd Reset & Game Rollout

- **~15:30 UTC** — Fizz received request to reset games (only Bandit + AI Copilot), provide admin password, and reset all users.
- **Backup taken** at `/home/ismaelrodriguez/ctfd-backup-20260806-090853.sql.gz` (55 KB, 28 users, 65 challenges).
- **Admin password reset:** `CEI-Labs-Admin2026!` (old hash unrecoverable). Verified with CTFd's own `verify_password` utility.
- **Games reset:** Only Bandit (35) + AI Copilot (6) set visible. Krypton (8) and Natas (16) hidden.
- **User accounts:** All 28 non-admin users and teams deleted. Solves, submissions, unlocks, awards, tokens all cleared.
- **Hint display cache bug:** The user reset deleted `hint_wallet_catalog_cache`, causing hints to return `409 no_active_catalog` for everyone. Root cause: the display cache lives in CTFd's DB and is only rebuilt by a wallet sync, not automatic on reset.
  - **Fix:** Reconstructed the display bundle from the orchestrator's `wallet_catalog` (revision 9, 3 tracks) and upserted it back.

### 2026-08-06 — Game Progression

- **~16:42 UTC** — Krypton unlocked (user request).
- **~16:44 UTC** — Workhorse (team 22) reported unable to SSH into Krypton.
  - **Root cause:** Team 22's Krypton instance had never been created — the orchestrator's create failed at 15:18 with a Docker overlay network race (`network chnet-22-group-krypton not found`) and rolled back silently. Other teams (20/21/23/26/27/29) got boxes; team 22 had CTFd secrets but no SSH target.
  - **Fix:** Recreated instance via orchestrator API on port 32018, re-synced flag secrets to CTFd.
- **~17:12 UTC** — Workhorse's Krypton box went down again (same race bug — a relaunch tore down the working box). Fixed with a clean non-relaunch create.
- **~17:34 UTC** — Bandit user passwords for Workhorse rotated by a relaunch. Set the resume point (bandit19) to `bandit19/bandit19` per user request.
- **~17:16 UTC** — Natas unlocked (all four games now active).

## What Was Sacrificed

### Network Architecture

The original reference design was a **five-VLAN router-on-a-stick** (Management 10, CTF Infra 20, Player WiFi 30, Player Wired 40, Staff 50). This was **explicitly dropped** for the live deployment in favor of a simplified two-network layout:

| Network | Interface | Subnet | Role |
|---------|-----------|--------|------|
| LAN/CTF Infra | `em0` | `192.168.10.0/24` | Management + server infrastructure |
| Player WiFi | `ue1` (opt3) | `10.10.32.0/22` | Player wireless access |

Stale leftover VLAN interfaces (`vlan01`-`vlan05`) remain active at the OS level but are unused.

### Server Outbound Internet

The **original OPNsense `192.168.10.0/24` server LAN was abandoned** because server outbound internet through OPNsense was never successfully restored. Root causes identified but not resolved:

- WAN interface `ue0` (Lenovo USB-C Ethernet) reported `no carrier` intermittently
- OPNsense web GUI was reachable but non-interactive SSH root login was unavailable to agents
- The upstream path issue (WAN DHCP lease, gateway, outbound NAT, or LAN rule) was never diagnosed from the console

Instead, the entire CTF stack was **re-homed to a directly-connected `192.168.1.0/24` subnet** where upstream internet was functional. The `.150` manager is connected to both networks but serves CTF from `192.168.1.150`.

### Four-Node Swarm → Three-Node

The original plan called for 4 Fedora nodes. `cei-ryzen5-15g-swarm04` (`.12`) had Docker installation failures and was unreachable during critical setup windows. The Swarm ships as a **3-node cluster**.

### Agent-Driven OPNsense Repair

The automated agent approach to OPNsense repair was abandoned after repeated sandbox false negatives and lack of non-interactive root credentials. All OPNsense changes were deferred to console/GUI manual intervention.

## End State

### Server Hardware

| Host | IP | Role | Specs | Docker |
|------|----|------|-------|--------|
| `cei-ryzen5-61g-swarm01` | `192.168.1.150` | Swarm manager leader | Ryzen 5 7600, 61 GiB, Fedora 44 | 29.6.2 |
| `cei-i7-31g-swarm02` | `192.168.1.193` | Swarm worker | i7-10750H, 31 GiB, Fedora 44 | 29.7.1 |
| `cei-xeon-e3-8g-swarm03` | `192.168.1.125` | Swarm worker | Xeon E3-1240 v2, 8 GiB, Fedora 44 | 29.6.2 |

### CTFd Instance State

- Service running: `ctfd`, `ctfd-db`, `ctfd-redis`, `orchestrator`, `traefik` — all `1/1`
- Admin login: `admin` / `CEI-Labs-Admin2026!`
- Games: All 4 active
- Challenges: 65 total (35 Bandit, 8 Krypton, 16 Natas, 6 AI Copilot)

### Active Player Services (port mappings on .150)

| Service | Port Range | Purpose |
|---------|-----------|---------|
| Bandit per-team | 32000-32009 | SSH bandit levels |
| Krypton per-team | 32010-32018 | SSH krypton levels |
| Natas attacker | 32002, 32010+ | SSH Kali attacker boxes |
| Natas targets | 32003, 32009+ | Web target VNC |
| CTFd web | 80/443 | Scoreboard + challenge UI |
| Traefik | 80/443 | SSL termination + routing |

### Player Data

- **18 users**, **10 teams** (1 unaffiliated)
- 1 admin user

#### Final Scoreboard

| # | Team | Score | Members |
|---|------|-------|---------|
| 1 | Workhorse | 15,533 | SGT_Steele, Workhorse, TryingMyBest, gonzarelli |
| 2 | DexMix | 14,291 | DexMix, csllvn29 |
| 3 | Ducks | 13,370 | oggieee |
| 4 | 0100 | 7,231 | 0100, ctf_brown |
| 5 | cyberparkour | 4,198 | cyberparkour |
| 6 | Nerd_Nuggies | 3,428 | Nerd_Nuggies, mcmilker |
| 7 | west point grads | 2,833 | demers, big dawg |
| 8 | Computers are evil | 1,780 | Computers are evil |
| 9 | TeamSloth2ElectricBoogaloo | 1,710 | secretsloth |

## Issues Encountered (Complete List)

| # | Issue | Root Cause | Fix | Severity |
|---|-------|------------|-----|----------|
| 1 | OPNsense server LAN had no internet egress | Dead upstream path (WAN DHCP/gateway/NAT/rules) unresolved | **Circumvented:** Re-homed Swarm to `192.168.1.0/24` | P0 |
| 2 | Stale Swarm advertise address | Manager advertised old IP after subnet change | Re-initialized swarm with `--advertise-addr 192.168.1.150` | P0 |
| 3 | CTFd DB password mismatch after re-home | Persistent volume held old secrets | MariaDB rescue container to realign passwords | P1 |
| 4 | Hints returned 409 for all users | User reset deleted `hint_wallet_catalog_cache` display row | Reconstructed display bundle from orchestrator catalog | P1 |
| 5 | Team 22 Krypton instance missing | Docker overlay network race during create; rollback was silent | Recreated instance via orchestrator API | P1 |
| 6 | Team 22 Krypton box went down | Relaunch triggered destructive teardown of working box | Clean non-relaunch create | P1 |
| 7 | Bandit per-team passwords invalid after relaunch | Relaunch regenerated secrets | Set resume-level to username=password | P2 |
| 8 | Agent sandbox false negatives for OPNsense/SSH | Sandbox isolation blocked TCP tests | Reran critical tests outside sandbox | P2 |
| 9 | Worker `.12` (swarm04) never joined | Docker install failures during setup | Excluded from swarm; ships as 3-node | P2 |
| 10 | Portainer detected on manager port 9000 | Pre-existing service, not CI-related | Noted; no action taken | Info |

## Channel Communications

All operational coordination for this event occurred in the **CEI-LABS** channel (`#ac7f6bcc-5f6f-4f75-a811-8459c8954a37`) on the Buzz platform. Key participants:

- **stoptalkingishh** — Event director, gave all operational orders
- **Fizz** — Lead infra engineer (swarm setup, CTFd reset, game rollout, player support)
- **Codex 5.5** — Network diagnostics, server provisioning, CTF automated playthrough
- **Codex 5.6 Luna** — OPNsense analysis, DNS/network troubleshooting
- **Bumble** — Context-aware coordination
- **Honey** — Coordination agent
- **Opencode_DeepSeek** (this session) — Event post-mortem documentation

Full message history (41 messages, 2026-08-05 through 2026-08-06) documents the complete arc from server discovery through final game unlock.

## Lessons Learned

1. **Per-team instance provisioning is fragile.** The orchestrator's overlay network race caused a silent create failure that left a team with CTFd secrets but no SSH target. Fix: before declaring a team provisioned, verify the instance exists in the orchestrator `instances` table AND that the container is running. Consider adding a retry-with-relaunch fallback.
2. **CTFd user reset must preserve hint display cache.** The `hint_wallet_catalog_cache` table is not rebuilt automatically; any full reset script must exclude this row.
3. **Sandbox false negatives waste time.** Agent sandboxes can block TCP connections that actually work. Always cross-check critical connectivity claims outside the sandbox.
4. **Subnet re-homing is high-risk.** Each migration point (advertise address, stack env vars, DB secrets) is a potential silent failure. Have a documented migration checklist.
5. **OPNsense console access is a prerequisite.** Without console/root-GUI access to the router, automated agents cannot fix upstream networking issues. Ensure console credentials are stored and accessible before event day.
6. **Backup early, backup often.** The CTFd DB backup taken before the reset saved the deployment — there was no working admin password to recover without it.

## Claude/ChatGPT Conversation Recap

The user requested pulling the last 9 conversations from Claude Desktop and ChatGPT apps. These conversations are stored in each application's local database (not as exportable plain-text files on the filesystem). No export files or conversation transcripts were found in Downloads or the workspace. The following CEI-Labs-related context was identified from available workspace and repo files:

- `CEI-Labs-CTF-Kickoff.pptx` (in Downloads) — event kickoff presentation
- `OKComputer_CEI_Labs_Repo_Review` (in Downloads) — a repo review archive of `cei-labs-engine`
- Several `cei-fixes.tar.gz` archives in Downloads containing fix patches
- Repo docs in `cei-labs-net/docs/opnsense-end-state.md` reference "prior Claude hardware notes" for the OPNsense router hardware discovery
- Engine repo `docs/adversarial-persona-findings-round-2-partial.md` references a Claude session performing orchestrator log analysis

To recover the full conversation history, the user would need to:
1. Open Claude Desktop and export the relevant CEI-Labs conversations
2. Open ChatGPT and export/screenshot the relevant conversations
3. Upload or share the exports via the channel
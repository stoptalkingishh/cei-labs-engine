# CEI Labs Engine

## Analyst Training Range & CTF Infrastructure Platform

CEI Labs Engine is a self-hosted analyst training range designed to support technical skill development through progressively challenging hands-on exercises.

Built on Docker (Swarm mode), CEI Labs provides a scalable platform for hosting Capture-the-Flag (CTF) events, analyst development programs, and competition preparation exercises — deployed with a single command, on one machine or many, with no cluster to manage.

The platform is designed to support users ranging from intelligence analysts with limited technical experience to advanced cyber practitioners preparing for competition and operational environments.

---

# Project Goals

## Current Goal

Deliver a stable analyst-focused training platform capable of supporting a live CTF event.

The immediate focus is providing:

* Reliable CTF hosting
* Browser-based analyst workstations
* Progressive technical learning paths
* Competition management through CTFd
* President's Cup preparation exercises

---

## Future Vision

Following the initial event, CEI Labs will expand into a broader training ecosystem supporting:

* SOC Analyst Training
* Threat Hunting Exercises
* Digital Forensics Labs
* Cyber Threat Intelligence (CTI)
* SIGINT-Oriented Challenges
* CYBINT-Oriented Challenges
* Intelligence Analysis Exercises
* Custom Scenario-Based Training, including self-service target+attacker wargame ranges (see the companion `CEI-Labs-Wargames` repository and the Challenge Instance Orchestrator below)

These capabilities are part of the long-term roadmap and are not required for the current event deployment.

---

# Training Tracks

The current curriculum is organized around progressive learning tracks designed to accommodate multiple experience levels. **Challenge content itself is not bundled in this repo** — this platform hosts and orchestrates challenges, it doesn't author them. Content comes from dedicated wargames repos (e.g. [`CEI-Labs-Wargames`](https://github.com/stoptalkingishh/CEI-Labs-Wargames), which self-hosts Bandit/Krypton/Natas rather than pointing at OverTheWire's live infrastructure) via `scripts/challenges-load.sh` or that repo's own equivalent loader — see each track below for which repo/topic it maps to.

## Foundations Track

Based primarily on OverTheWire Bandit.

Focus Areas:

* Linux Fundamentals
* Command Line Navigation
* SSH
* File Permissions
* Bash Usage
* System Enumeration

Target Audience:

* Intelligence Analysts
* New Cyber Personnel
* Non-Technical Users
* Entry-Level Participants

Objective:

Develop confidence operating in Linux environments and understanding basic system concepts.

---

## Operations Track

Based primarily on OverTheWire Natas.

Focus Areas:

* Web Applications
* HTTP
* Authentication
* Cookies
* Sessions
* Basic Reconnaissance

Target Audience:

* Technical Analysts
* SOC Personnel
* Intermediate Participants

Objective:

Develop familiarity with common web technologies and application security concepts.

---

## Analysis Track

Based primarily on OverTheWire Leviathan.

Focus Areas:

* Enumeration
* Investigation
* Privilege Concepts
* Binary Interaction
* Analytical Problem Solving

Target Audience:

* Advanced Users
* Cyber Practitioners
* Competition Participants

Objective:

Develop the ability to solve unfamiliar technical problems using analytical methods.

---

# Competition Preparation

The platform is intended to support preparation for events such as the President's Cup Cybersecurity Competition.

Planned challenge categories include:

* Linux
* Networking
* Web
* Forensics
* Reverse Engineering
* Cryptography
* OSINT
* Threat Analysis

Additional challenge content will be added over time as the platform matures.

---

# Core Platform Components

The current CEI Labs deployment includes:

| Component                       | Purpose                                                     |
| -------------------------------- | ------------------------------------------------------------ |
| Docker Engine (Swarm mode)       | Container runtime and orchestrator                           |
| Traefik                          | Ingress, routing, and TLS termination                        |
| CTFd (custom image)              | Competition management platform + instance-launcher plugin   |
| Challenge Instance Orchestrator  | Spins up per-team Juice Shop / target+attacker instances on demand |
| OWASP Juice Shop                 | Web security training environment                            |
| Kali noVNC                       | Browser-based analyst/attacker workstations                  |
| Analyst Containers                | SSH-based training environments                              |
| MariaDB                          | Persistent application database                              |
| Redis                            | Caching and session management                                |

MetalLB and a dedicated internal image registry are gone: Swarm's routing mesh gives every node the same reachability MetalLB provided, and images are already published to GHCR by CI, so there's nothing to distribute internally.

---

# Deployment Model

CEI Labs runs on **Docker Swarm**. There is no tier to choose between: the same `docker/stack.yml` deploys to one machine or many, and adding capacity is just joining another host to the swarm and re-running the deploy — nothing in the stack definition changes.

## Single Host

Recommended for development, testing, and small training events.

Resources: 4 CPU cores, 16 GB RAM, 100 GB SSD — supports roughly up to 10 concurrent users.

`docker swarm init` on that one machine is enough; it becomes a one-node swarm.

## Multiple Hosts

Recommended for larger events and competition hosting. Join as many hosts as you need — `docker swarm join` on each additional machine (or let `ansible/site.yml` do it for a whole inventory at once). The Swarm scheduler spreads services (including on-demand challenge instances and bulk-spawned workspaces) across every available node automatically.

---

# High-Level Architecture

```text
                        Internet
                            │
                            ▼
                        Traefik  (Swarm routing mesh — any node's IP works)
        ┌───────────────────┼──────────────────────────┐
        │                   │                           │
        ▼                   ▼                           ▼
      CTFd          Challenge Instance          Bulk-Spawned
   (+ instance-      Orchestrator             Workspaces (SSH/
    launcher plugin) (Docker API)              noVNC, admin-run)
        │                   │
        ▼                   ▼
  MariaDB + Redis    Juice Shop / Target+Attacker
                      instances (per-team, on-demand,
                      isolated overlay networks)
```

The instance-launcher plugin (baked into the CTFd image) calls the orchestrator server-to-server whenever a participant opens a challenge configured with an instance type; the orchestrator talks to the Docker API to create the actual containers, which Traefik then discovers and routes to on its own. See `docker/orchestrator/README.md` for the full contract.

---

# Network Prerequisites

This platform is network-agnostic — see [`docs/network-prerequisites.md`](docs/network-prerequisites.md) for the exact ports, DNS, and TLS expectations any front-end network needs to satisfy, and a pointer to [`cei-labs-net`](https://github.com/stoptalkingishh/cei-labs-net) as one worked reference implementation (not a dependency).

---

# Security & Anti-Automation Posture

Automated tools (scripted or AI-driven) attacking CTFd itself to extract flags directly — rather than solving the intended challenge — are mitigated in layers. None of these are unique to "AI" specifically; they're the same controls that blunt any high-volume automated abuse, human-driven or not:

1. **Network segmentation (already structural, not configuration).** Challenge containers (Juice Shop, targets, attacker workstations) live on networks that never include CTFd or its database — see the isolation model above and in `docker/orchestrator/README.md`. Even a fully compromised challenge container has no path to CTFd's data.
2. **Traefik rate limiting** (`docker/stack.yml`, the `ctfd` service's labels): a tight per-source-IP limit specifically on the flag-submission endpoint (`/api/v1/challenges/attempt`), plus a more generous whole-app limit as a second layer.
3. **CTFd's own submission rate limiting/lockout.** Enable this in CTFd's admin configuration (Config → Security in the admin panel) after initial setup — it complements the Traefik layer with CTFd's own awareness of per-account (not just per-IP) submission patterns.
4. **Keep the CTFd image current.** `docker/ctfd/Dockerfile` pins a specific upstream CTFd version; track CTFd's security releases and rebuild/redeploy (`./scripts/patch-secrets.sh`-style redeploy, or a version bump + `stack-up.sh`) when they land.
5. **Minimize admin surface.** Don't expose `/admin` beyond what's needed; consider an additional Traefik BasicAuth or IP-allowlist middleware on admin routes for events open to the public internet.

This is a baseline, not a complete WAF — for a large or high-profile public event, put a dedicated WAF (e.g. CrowdSec, ModSecurity) in front in addition to the above.

---

# Operating System Requirements

## Supported Linux Distributions

* Ubuntu
* Debian
* Rocky Linux
* Fedora
* Arch Linux

Kernel Version:

```text
5.15+
```

Anything that can run a current Docker Engine works.

The current Ansible provisioning role uses Debian's `apt` modules and is
automated/tested for Ubuntu and Debian. Other distributions can run the
Docker stack, but their host packages and firewall must currently be
installed manually.

---

## Non-Linux Hosts

Windows and macOS users must deploy CEI Labs within a Linux virtual machine (or, for local single-host evaluation only, Docker Desktop's own Linux VM).

Supported Hypervisors:

* Proxmox
* VMware
* Hyper-V
* VirtualBox
* Parallels

---

# Quick Start

Clone the repository:

```bash
git clone https://github.com/stoptalkingishh/cei-labs-engine.git
cd cei-labs-engine
```

Prepare scripts and case storage:

```bash
chmod +x scripts/*.sh
./scripts/setup-cases.sh
```

Configure the stack:

```bash
cp docker/.env.example docker/.env            # edit BASE_DOMAIN, GITHUB_ORG, etc.
cp -r docker/secrets.example docker/secrets   # replace every CHANGE_ME value
```

## Single machine

```bash
./scripts/stack-up.sh
```

This initializes a one-node swarm if needed and deploys the full stack.

## Multiple machines

Edit `ansible/inventory.ini` to list your hosts under `[swarm_managers]`/`[swarm_workers]`, then:

```bash
ansible-playbook -i ansible/inventory.ini ansible/site.yml
```

This installs Docker, forms the swarm across every listed host, and labels the primary manager to hold stateful services. Then, from that primary manager:

```bash
./scripts/stack-up.sh
```

Or just run the interactive installer for either path: `./scripts/install.sh`.

---

# Initial CTFd Configuration

After deployment:

1. Open `https://ctfd.<your-base-domain>`.
2. Complete the initial setup wizard (or let `install.sh` do this non-interactively).
3. Generate an API token, then: `export CTFD_ADMIN_TOKEN=...`
4. Load challenge content. This repo ships no bundled challenges — pull
   content from a wargames repo first (e.g. clone
   [`CEI-Labs-Wargames`](https://github.com/stoptalkingishh/CEI-Labs-Wargames)
   and run its own `deploy.sh`), or drop challenge YAML under
   `challenges/sprintN-*/` here and run:

```bash
./scripts/challenges-load.sh
```

Any challenge whose YAML declares `instance_type` (see `docker/orchestrator/README.md`) automatically gets a "Launch Environment" link wired up via the instance-launcher plugin — both loading paths populate the same CTFd instance/mapping tables.

For a staggered Bandit/Krypton/Natas event, follow
[`docs/staggered-wargame-stages.md`](docs/staggered-wargame-stages.md). The
custom CTFd image includes an administrator **Wargame stages** page for exact
category sync, independent game starts, per-game scoreboards, lock cutoffs,
hide/show controls, and CSV/JSON result exports. Run the automated and manual
checks in
[`docs/staggered-wargame-stage-verification.md`](docs/staggered-wargame-stage-verification.md)
before production use.

---

# Monitoring

```bash
./scripts/status.sh
```

Shows a live snapshot: swarm nodes, stack service health, bulk-spawned workspaces, and active self-service challenge instances.

For an interactive host view, the common Ansible role installs `btop` and
`tmux` on every Ubuntu/Debian Swarm node:

```bash
ssh <node>
tmux new -As cei-monitor btop
```

Detach with `Ctrl-b d`; reconnect with the same command. `btop` opens no
network port and runs only while an operator's terminal session is active.

`btop` is a live view, not retained test evidence. During load or persona
tests, run the timestamped collector in a second terminal:

```bash
./scripts/capture-resources.sh evidence/run-YYYYMMDD-HHMMSS
```

Stop it with `Ctrl-C`. The output contains host CPU/load, memory, swap, disk,
network counters, Docker container stats, service state, and Docker events;
it does not collect secrets or challenge flags. Keep any Glances web view
bound to loopback and reach it through an SSH tunnel rather than exposing a
monitoring port to the participant LAN.

---

# Event Readiness Roadmap

## Phase 1 — Platform Stability

* Validate deployments
* Verify backups
* Verify recovery procedures
* Verify persistence

## Phase 2 — Training Delivery

* Deploy Bandit curriculum (self-hosted — see `CEI-Labs-Wargames`)
* Deploy Natas curriculum (self-hosted — see `CEI-Labs-Wargames`)
* Deploy Leviathan curriculum
* Validate challenge progression

## Phase 3 — Event Execution

* Support live users
* Validate scoring
* Validate workstations
* Conduct competition operations

---

# Contributing

Contributions are welcome.

Priority areas include:

* Deployment Reliability
* Documentation
* Challenge Automation
* Infrastructure Hardening
* Competition Tooling
* Analyst Training Content

---

# Disclaimer

CEI Labs Engine is intended for educational, training, and competition purposes only.

Users are responsible for ensuring compliance with all applicable organizational policies, laws, and regulations when deploying or operating the platform.

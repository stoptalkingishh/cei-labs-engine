# cei-labs-engine
CEI Labs is a modular, multi-tenant cyber range engineered on a lightweight K3s mini-PC cluster. Built to decouple core infrastructure from individual modules, CEI Labs functions as an evolving platform capable of orchestrating diverse capture-the-flag environments, threat hunting sandboxes, and deep network analysis pipelines.

# CEI Labs // CTF Infrastructure Engine
A self-hosted, progressive cybersecurity training platform built for the President's Cup Cybersecurity Competition (PCCC) pipeline. Flexible K3s topology supporting standalone single-host, dual-host, or high-availability three-node hardware configurations running CTFd, MultiJuicer (OWASP Juice Shop), SSH analyst containers, Kali noVNC workstations, and self-hosted target wrappers.

## Confirmed Dependency Versions
| Component | Version | Source / Notes |
| :--- | :--- | :--- |
| **K3s** | `v1.32.5+k3s1` | Stable — Single-server SQLite control plane |
| **Helm** | `v4.2.0` | Feature release — Uses Server-Side Apply (SSA) |
| **CTFd** | `v3.8.2` | Latest stable — Security patched |
| **MultiJuicer** | `v10.0.0` | Latest stable — Root-level parameter schema layout |
| **OWASP Juice Shop** | `v17+` | Dynamic deployment via MultiJuicer |
| **MetalLB** | `latest` | Bare-metal Layer 2 LoadBalancer pool |
| **Traefik** | `v3.x` | Managed Helm Ingress handling dedicated external TLS routing |
| **MariaDB** | `10.11` | Pinned PVC storage backing ctfd-db |
| **Redis** | `7-alpine` | Banking CTFd caching layer |
| **Ubuntu** | `24.04 LTS` | Node base operating system |

---

## Technical Hardware Performance Profiles

Before initiating a deployment, update `ansible/group_vars/all.yml` to reflect your physical target hardware limits:

| Deployment Mode | Managed Host Nodes | Target vCPUs | Baseline RAM | Target Storage Size | Max Target Participants |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`single`** | 1 Node (Combined) | 4 Cores | 16 GB | 100 GB SSD | Up to 10 Users |
| **`dual`** | 2 Nodes (Segregated) | 4 + 4 Cores | 16 + 16 GB | 100 + 100 GB SSD | Up to 20 Users |
| **`cluster`** | 3 Nodes (HA Dedicated) | 4 + 4 + 4 Cores | 32 + 16 + 16 GB | 256 + 100 + 100 GB SSD | Up to 30 Users |

---

## Hardware Layout & Topology (Production Cluster Example)
┌─────────────────────────────┐   ┌─────────────────────────────┐
│  NODE 1 — Control Plane     │   │  NODE 2 — Juice Shop        │
│  Hardware: OptiPlex 7080    │   │  Hardware: N100 Mini PC     │
│  Specs: i5, 32GB, 256GB SSD │   │  Specs: 16GB RAM, 512GB SSD │
│                             │   │                             │
│  - K3s Server (Leader)      │   │  - K3s Agent                │
│  - CTFd + MariaDB (PVC)     │   │  - Node Label: role=juiceshop│
│  - Local Registry (PVC)     │   │  - MultiJuicer Instances    │
│  - Traefik / MetalLB        │   │     (Up to 30 dynamic pods)  │
└─────────────────────────────┘   └─────────────────────────────┘
┌─────────────────────────────┐
│  NODE 3 — Analyst + Targets │
│  Hardware: N100 Mini PC     │
│  Specs: 16GB RAM, 512GB SSD │
│                             │
│  - K3s Agent                │
│  - Node Label: role=analyst │
│  - SSH Analyst Containers   │
│  - Kali noVNC Workstations  │
│  - Self-Hosted PCCC Targets │
└─────────────────────────────┘

---

## Training Sprint Structure (CTFd Gated)

### Sprint 1 — Foundational Fluency (UNLOCKED)
* **Labs:** OverTheWire Bandit (0–15), CmdChallenge (1–20)
* **Mechanism:** External targets with static flag verification.
* *Gating:* Passwords from Bandit 15 and final CmdChallenge unlock Sprint 2.

### Sprint 2 — Systems, Web & Forensics (LOCKED)
* **Labs:** OverTheWire Bandit (16–34), Natas (0–15), Krypton, Leviathan
* **Self-Hosted:** OWASP Juice Shop (100+ challenges via MultiJuicer), PCAP/Log Forensics
* *Gating:* Requires Sprint 1 complete + 25 Juice Shop flags + 5 PCAP challenges.

### Sprint 3 — Advanced Operations (LOCKED)
* **Labs:** OverTheWire Narnia & Behemoth (Binary Exploitation), Natas (16–34)
* **Self-Hosted:** OWASP crAPI (Shared instance), PCCC Skilling Labs, Historical PCCC Scenarios.
* *Destination:* Open matrix sandbox; no downstream restrictions.

---

## Quick Start (Post-Hardware Assembly)

```bash
# 1. Clone the repository
git clone [https://github.com/](https://github.com/)<your-org>/cei-labs-engine
cd cei-labs-engine

# 2. Configure inventory and cluster variable parameters
cp ansible/group_vars/all.yml.example ansible/group_vars/all.yml
nano ansible/group_vars/all.yml
nano ansible/inventory.ini

# 3. Provision the host forensic file structures and mount points
./scripts/setup-cases.sh

# 4. Execute cluster provisioning playbook
# (Installs OS updates, secures runtimes, handles token acquisition, and brings up K3s)
ansible-playbook -i ansible/inventory.ini ansible/site.yml

# 5. Spin up Core Engine Platforms
# (Deploys Core Namespaces, Local Registry PVC, cert-manager, Traefik, CTFd, and MultiJuicer)
./scripts/platform-up.sh

# 6. Populate curriculum challenges and scoring engines
# (Run after initial administrative account setup steps)
./scripts/juice-shop-ctf-import.sh
./scripts/challenges-load.sh

# 7. Audit local operational range state
kubectl get nodes -o wide
kubectl get pods -A
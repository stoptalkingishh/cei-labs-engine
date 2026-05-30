# cei-labs-engine
CEI Labs is a modular, multi-tenant cyber range engineered on a lightweight K3s mini-PC cluster. Built to decouple core infrastructure from individual modules, CEI Labs functions as an evolving platform capable of orchestrating diverse capture-the-flag environments, threat hunting sandboxes, and deep network analysis pipelines.

# CEI Labs // CTF Infrastructure Engine
A self-hosted, progressive cybersecurity training platform built for the President's Cup Cybersecurity Competition (PCCC) pipeline. Three-node K3s cluster running CTFd, MultiJuicer (OWASP Juice Shop), SSH analyst containers, Kali noVNC workstations, and self-hosted target wrappers.

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
| **Redis** | `7-alpine` | CTFd caching layer |
| **Ubuntu** | `24.04 LTS` | Node base operating system |

---

## Hardware Layout & Topology
┌─────────────────────────────┐   ┌─────────────────────────────┐
│  NODE 1 — Control Plane     │   │  NODE 2 — Juice Shop        │
│  Hardware: OptiPlex 7080    │   │  Hardware: N100 Mini PC     │
│  Specs: i5, 32GB, 256GB SSD │   │  Specs: 16GB RAM, 512GB SSD │
│                             │   │                             │
│  - K3s Server (Leader)      │   │  - K3s Agent                │
│  - CTFd + MariaDB (PVC)     │   │  - Node Label: role=juiceshop│
│  - Local Registry (PVC)     │   │  - MultiJuicer Instances    │
│  - Traefik / MetalLB        │   │    (Up to 30 dynamic pods)  │
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

# 2. Configure inventory and secrets
cp ansible/group_vars/all.yml.example ansible/group_vars/all.yml
nano ansible/group_vars/all.yml
nano ansible/inventory.ini

# 3. Execute cluster provisioning
# (Installs Ubuntu dependencies, secures runtimes, and provisions K3s environment)
ansible-playbook ansible/site.yml -i ansible/inventory.ini

# 4. Spin up Core Engine Services
# (Deploys Local Registry PVC, MetalLB, CTFd, cert-manager, and MultiJuicer via Helm)
./scripts/platform-up.sh

# 5. Populate curriculum flags via ctfcli
./scripts/challenges-load.sh

# 6. Audit local range status
kubectl get nodes -o wide
kubectl get pods -A
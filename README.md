# cei-labs-engine
CEI Labs is a modular, multi-tenant cyber range engineered on a lightweight K3s mini-PC cluster. Built to decouple core infrastructure from individual modules, CEI Labs functions as an evolving platform capable of orchestrating diverse capture-the-flag environments, threat hunting sandboxes, and deep network analysis pipelines.

# CEI Labs // CTF Infrastructure Engine
A self-hosted, progressive cybersecurity training platform built for the 
President's Cup Cybersecurity Competition (PCCC) pipeline. Three-node K3s 
cluster running CTFd, MultiJuicer (OWASP Juice Shop), SSH analyst containers, 
Kali noVNC workstations, and self-hosted target wrappers.

Confirmed dependency versions (as of May 2026)
| Component | Version | Source / Notes |
| :--- | :--- | :--- |
| **K3s** | `v1.32.5+k3s1` | Stable — Single-server SQLite control plane |
| **Helm** | `v4.2.0` | Feature release — Uses Server-Side Apply (SSA) |
| **CTFd** | `v3.8.2` | Latest stable — Security patched |
| **MultiJuicer** | `v10.0.0` | Latest stable — Consolidated architecture |
| **OWASP Juice Shop** | `v17+` | Dynamic deployment via MultiJuicer |
| **MetalLB** | `latest` | Bare-metal Layer 2 LoadBalancer pool |
| **Traefik** | `v3.x` | Native ingress bundled directly into K3s v1.32+ |
| **MariaDB** | `10.11` | Pinned hostPath storage on Node 1 |
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
│  - CTFd + MariaDB + Redis   │   │  - Node Label: role=webapps │
│  - Local Registry Cache     │   │  - MultiJuicer Instances    │
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
*   **Labs:** OverTheWire Bandit (0–15), CmdChallenge (1–20)
*   **Mechanism:** External targets with static flag verification.
*   *Gating:* Passwords from Bandit 15 and final CmdChallenge unlock Sprint 2.

### Sprint 2 — Systems, Web & Forensics (LOCKED)
*   **Labs:** OverTheWire Bandit (16–34), Natas (0–15), Krypton, Leviathan
*   **Self-Hosted:** OWASP Juice Shop (100+ challenges via MultiJuicer), PCAP/Log Forensics
*   *Gating:* Requires Sprint 1 complete + 25 Juice Shop flags + 5 PCAP challenges.

### Sprint 3 — Advanced Operations (LOCKED)
*   **Labs:** OverTheWire Narnia & Behemoth (Binary Exploitation), Natas (16–34)
*   **Self-Hosted:** OWASP crAPI (Shared instance), PCCC Skilling Labs, Historical PCCC Scenarios.
*   *Destination:* Open matrix sandbox; no downstream restrictions.

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
# (Installs Ubuntu dependencies, secures runtimes, provisions K3s, and labels nodes)
ansible-playbook ansible/site.yml -i ansible/inventory.ini

# 4. Spin up Core Engine Services
# (Deploys Local Registry, MetalLB, CTFd infrastructure with pinned storage, and MultiJuicer)
./scripts/platform-up.sh

# 5. Populate curriculum flags via ctfcli
./scripts/challenges-load.sh

# 6. Audit local range status
kubectl get nodes -o wide
kubectl get pods -A
Repository Directory Structure
Plaintext
cei-labs-engine/
├── README.md
├── LICENSE                      # Apache License 2.0
├── .github/
│   └── workflows/
│       ├── build-analyst.yml    # Compiles and pushes analyst environment images
│       ├── build-kali-novnc.yml # Compiles and pushes Kali XFCE browser workstations
│       └── validate.yml         # Pre-flight YAML linter for K8s manifests & Ansible
├── ansible/
│   ├── inventory.ini            # System IPs and connection configurations
│   ├── site.yml                 # Master orchestration playbook
│   ├── group_vars/
│   │   ├── all.yml.example      # Variable template containing K3s token definitions
│   │   └── all.yml              # Git-ignored runtime secrets configuration file
│   └── roles/
│       ├── common/              # Docker, local containerd registry routing hooks
│       ├── k3s-server/          # Initialise Control Plane node
│       ├── k3s-agent/           # Join agent nodes and attach role labels
│       ├── metallb/             # Set up layer 2 bare-metal IP pool
│       └── ctfd/                # Deploy database and Helm values under Helm 4
├── core-infrastructure/
│   ├── namespaces/              # Strict network namespace boundaries
│   ├── ctfd/                    # CTFd values, Redis cache, and Node 1 hostPath specs
│   ├── multijuicer/             # MultiJuicer controller configuration values
│   ├── registry/                # Local cache configuration matching registries.yaml
│   └── ingress/                 # Traefik IngressRoute configurations
├── runtime-environments/
│   ├── analyst-ssh/
│   │   └── Dockerfile           # Minimal terminal analyst toolbox container image
│   └── kali-novnc/
│       └── Dockerfile           # Heavy browser-accessible offensive workstation image
├── scripts/
│   ├── platform-up.sh           # Sequence creation of platform assets
│   ├── platform-down.sh         # Graceful cleanup preserving database files
│   ├── challenges-load.sh       # Sync custom YAML tracks using ctfcli
│   ├── spawn-analysts.sh        # Provision isolated workspaces targeting Node 3
│   └── juice-shop-import.sh     # Convert Juice Shop vulnerabilities to CTFd values
└── curriculum-packs/
    ├── 01-pccc-prep/            # Sprint definitions, file trees, and challenge.yml schemas
    ├── 02-owasp-top-10/         # Web/API targeting definitions
    └── 03-red-team-basics/      # Forensic image configurations and raw binary payloads

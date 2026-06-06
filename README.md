# cei-labs-engine

CEI Labs is a modular, multi-tenant cyber range engineered on a lightweight K3s mini-PC cluster. Built to decouple core infrastructure from individual modules, CEI Labs functions as an evolving platform capable of orchestrating diverse capture-the-flag environments, threat hunting sandboxes, and deep network analysis pipelines.

# CEI Labs // CTF Infrastructure Engine

A self-hosted, progressive cybersecurity training platform built for the President's Cup Cybersecurity Competition (PCCC) pipeline. Cross-platform, OS-independent K3s topology supporting standalone single-host, dual-host, or high-availability three-node configurations running CTFd, MultiJuicer (OWASP Juice Shop), SSH analyst containers, Kali noVNC workstations, and self-hosted target wrappers.

## Core System Operating System Baselines

> 🚨 **Critical OS Requirement:** The CEI Labs Engine relies heavily on native Linux kernel primitives (cgroups, namespaces, and iptables routing architectures) to manage container isolation and cluster networking. **It cannot be deployed directly on bare-metal Windows or macOS.** ### 1. Single-Node Deployment Baseline

* **Native Environment:** A single physical machine running a standard Linux distribution (Kernel 5.15+).
* **Non-Linux Fallback:** If your primary workstation runs Windows or macOS, you **must** provision a Linux Virtual Machine using a local hypervisor (e.g., Proxmox VE, VirtualBox, VMware Workstation, Hyper-V, or Parallels) allocated with the minimum hardware specifications outlined below.

### 2. Multi-Node Deployment Baseline (Dual / Cluster)

* **Native Distributed Environment:** Separate physical or virtual target machines, each running an independent instance of a Linux operating system distribution. All target nodes must share a unified Local Area Network (LAN) with static IP assignments.
* **Hypervisor/Cloud Environments:** Can be fully orchestrated across virtualized infrastructure environments (e.g., Proxmox VE, VMware ESXi, AWS VPC, or Google Cloud Compute Engine) by spinning up the required count of independent Linux minimal server instances.
* **Supported Distributions:** The automation scripts handle multi-OS provisioning transparently across **Ubuntu/Debian (`apt`)**, **RHEL/Fedora/Rocky Linux (`dnf`)**, and **Arch Linux (`pacman`)**.

---

## Confirmed Dependency Versions

| Component | Version | Source / Notes |
| --- | --- | --- |
| **K3s** | v1.32.5+k3s1 | Stable — Single-server SQLite control plane |
| **Helm** | v4.2.0 | Feature release — Uses Server-Side Apply (SSA) |
| **CTFd** | v3.8.2 | Latest stable — Security patched |
| **MultiJuicer** | v10.0.0 | Latest stable — Root-level parameter schema layout |
| **OWASP Juice Shop** | v17+ | Dynamic deployment via MultiJuicer |
| **MetalLB** | latest | Bare-metal Layer 2 LoadBalancer pool |
| **Traefik** | v3.x | Managed Helm Ingress handling dedicated external TLS routing |
| **MariaDB** | 10.11 | Pinned PVC storage backing ctfd-db |
| **Redis** | 7-alpine | Banking CTFd caching layer |
| **Linux Agnostic** | Kernel 5.15+ | Supported across Debian/Ubuntu, RHEL/Fedora, and Arch distributions |

---

## Target Environment Allocation Profiles

Before initiating a deployment, update `ansible/group_vars/all.yml` to reflect your target environment resource allocations. Profiles scale based on expected concurrent training cohorts:

| Deployment Mode | Managed Host Nodes | Target vCPUs | Baseline RAM | Target Storage Size | Recommended Group Minimum |
| --- | --- | --- | --- | --- | --- |
| **single** | 1 Node (Combined) | 4 Cores | 16 GB | 100 GB SSD | Evaluation / Up to 10 Users |
| **dual** | 2 Nodes (Segregated) | 4 + 4 Cores | 16 + 16 GB | 100 + 100 GB SSD | Mid-Scale / Up to 20 Users |
| **cluster** | 3 Nodes (HA Dedicated) | 4 + 4 + 4 Cores | 32 + 16 + 16 GB | 256 + 100 + 100 GB SSD | Enterprise / Up to 30 Users |

---

## Infrastructure Layout & Cluster Architecture Topology

```
┌─────────────────────────────┐   ┌─────────────────────────────┐
│  NODE 1 — Control Plane     │   │  NODE 2 — Juice Shop Workload│
│  Minimum: 4 vCPU / 32GB RAM │   │  Minimum: 4 vCPU / 16GB RAM │
│  Baseline: Any Linux OS     │   │  Baseline: Any Linux OS     │
│                             │   │                             │
│  - K3s Server (Leader)      │   │  - K3s Agent Node           │
│  - CTFd + MariaDB (PVC)     │   │  - Node Label: role=juiceshop│
│  - Local Registry (PVC)     │   │  - MultiJuicer Instances    │
│  - Traefik / MetalLB        │   │     (Dynamic scaled pods)   │
└─────────────────────────────┘   └─────────────────────────────┘
┌─────────────────────────────┐
│  NODE 3 — Analyst + Targets │
│  Minimum: 4 vCPU / 16GB RAM │
│  Baseline: Any Linux OS     │
│                             │
│  - K3s Agent Node           │
│  - Node Label: role=analyst │
│  - SSH Analyst Containers   │
│  - Kali noVNC Workstations  │
│  - Self-Hosted PCCC Targets │
└─────────────────────────────┘

```

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

## Quick Start (OS-Independent Interactive Installer)

The range engine features a portable deployment wizard (`scripts/install.sh`) that dynamically inspects your Linux environment, detects the native package management architecture, handles host dependency injection, and configures cluster variables mid-flight.

### 1. Retrieve the Repository Workspace

```bash
git clone https://github.com/stoptalkingishh/cei-labs-engine.git
cd cei-labs-engine

```

### 2. Execute the Provisioning Pipeline

Grant execution privileges across the script utilities tree, prepare your localized case volume storage mounts, and trigger the installer:

```bash
chmod +x scripts/*.sh

# Establish forensic storage layers and user share paths
./scripts/setup-cases.sh

# Run the central interactive configuration wizard
./scripts/install.sh

```

### 3. Native Package Mapping & Fallback Architecture

The initialization pipeline intelligently maps dependency footprints depending on what ecosystem your host machine operates under:

* **Debian / Ubuntu Systems (apt):** Configures stable base repositories and patches tooling via advanced packaging utilities.
* **Red Hat / Fedora / Rocky Linux (dnf):** Pulls required enterprise utilities, scales repository mirrors, and resolves specific sysstat/moby tracking binary names.
* **Arch Linux (pacman):** Synchs database indices and provisions rolling infrastructure dependencies natively via standard pacman targets.
* **Universal Fallback Model:** If a standard package manager is absent, the execution layer abstracts dependency compilation to localized Python Pip runtime variables (`PIP_BREAK_SYSTEM_PACKAGES=1`) to anchor Ansible and jq without interrupting OS integrity.

### 4. Interactive Configuration Options

When running the wizard, you will step through the following dynamic prompts:

* **Dependency Management:** Choose Automated Install to let the engine provision your OS-specific packages, or select Manual Validation to run a binary audit against a custom pre-built environment.
* **Orchestration Complexity Profiles:**
* **Simple Mode:** Tailored for local virtual machines or rapid environment evaluations. Bypasses distributed networking blocks, locks host targets to a localized localhost array, and applies resource-light parameters.
* **Advanced Mode:** Unlocks full distributed multi-host topologies (single/dual/cluster), handles layer-2 load balancing configuration flags (MetalLB), and safely spawns an available shell editor text terminal environment (vi or nano) to let you map nodes onto your inventory space directly.


* **Security Token Customization Matrix:** Prompts you live to inject unique administrative values (CTFd application secret strings, MariaDB storage backend user passwords, and MultiJuicer control panel master keys) instead of relying on unsafe static defaults.

### 5. Curriculum Ingestion & Range Performance Metrics

Once the cluster state is active, run the following sync utilities to seed training targets and monitor host health parameters:

```bash
# Populate OWASP Juice Shop challenge schemes
./scripts/juice-shop-ctf-import.sh

# Ingest underlying static and containerized challenge modules
./scripts/challenges-load.sh

```

---

## Network Entrypoints & Ingress Service Routing Table

Once installation terminates successfully, workloads are bound to your Layer-2 LoadBalancer IP space allocation managed via MetalLB and routed under the Traefik proxy ingress.

> 💡 **Routing Reference:** If running in **Simple Mode**, `<LoadBalancer-IP>` defaults to your primary host node network interface address.

| Target Platform Application | Target Network Route Location | Underlying Protocol / Port Access Vector |
| --- | --- | --- |
| **CTFd Scoreboard Interface** | `http://<LoadBalancer-IP>/` | HTTP / TCP Port 80 (Redirected to 443) |
| **MultiJuicer Student Gateway** | `http://<LoadBalancer-IP>/balancer` | HTTP / Web Sockets Reverse Proxy |
| **OWASP crAPI Sandbox Shared Matrix** | `http://<LoadBalancer-IP>:8888/` | Direct Target Access Vector |
| **Internal Docker Registry** | `http://<LoadBalancer-IP>:5000/` | Image Storage Backend Registry Access |
| **Ephemeral Analyst SSH Terminal Node** | `ssh operator@<Host-IP>` | Port 2222 (Iterates dynamically per user) |
| **Graphical Kali Workstations (noVNC)** | `http://<LoadBalancer-IP>/workspace/` | Automated UI VNC Browser Stream |

---

## Post-Install CTFd Application Onboarding Setup

Before executing `./scripts/challenges-load.sh`, you must complete the initial manual database priming inside the CTFd web panel interface to establish base state schemas:

1. Open your web browser and navigate to `http://<LoadBalancer-IP>`.
2. **Setup Wizard Configuration:**
* Provide your target deployment name (e.g., `CEI Labs Cyber Range`).
* Create the primary root cluster administrative credential profile.


3. **Generate a Live API Access Token:**
* Authenticate into the platform using your newly created administrative profile credentials.
* Access the application menu path: **Settings** -> **Tokens**.
* Select **Generate Token**, name the identifier `CHALLENGE_LOADER`, and choose **No Expiration**.
* Copy the returned API token string immediately.


4. **Link the API Token Payload to the Local Environment:**
* Return to your terminal prompt on the deployment host.
* Open `ansible/group_vars/all.yml` and paste the token string directly into the `ctfd_api_token` configuration parameter line.


5. **Seed the Curriculum Content Matrices:**
* Execute the ingestion pipeline scripts to populate the active range targets:
```bash
./scripts/challenges-load.sh --sprint 2
./scripts/challenges-load.sh --sprint 3

```





---

## Unified Range Telemetry & Status Monitoring Dashboard

The range environment includes a production-ready logging dashboard to let operators track bare-metal infrastructure capacities, network connection endpoints, cluster deployment health, and user container usage states in real time.

To initialize the telemetry monitor console window, execute:

```bash
./scripts/status.sh

```

### Operational View Interface Selection

Upon launching the application utility script framework, you will be prompted to select an execution view style interface:

* **Option 1: Real-time Dashboard Core Interface (Interactive)**
Spawns a terminal screen displaying 5 live analytical blocks. Refreshes telemetry parameters dynamically every 8 seconds:
1. *Host Infrastructure Matrix:* Monitors real-time daemon process runs (`k3s`, `ufw`), active bare-metal memory consumption, and storage space limits inside `/opt/ctf-cases`.
2. *Network Service Access & Ingress Map:* Evaluates LoadBalancer route maps and checks dynamic Traefik IngressRoute paths across all active workspaces.
3. *Microservice Pod Health Context:* Flags scheduling issues, pod start crashes, or repository image mirror download blocks (`ImagePullBackOff`).
4. *Scoreboard Ingress Accessibility Check:* Conducts continuous curl tests to verify public routing availability for the primary student scoring panel.
5. *Ephemeral Student Analyst Workspaces:* Provides a directory map showing active user sandboxes (Ubuntu terminals and graphical Kali noVNC environments).


* **Option 2: Historical Metric Analysis Ledger (CSV Timeline View)**
Outputs a tab-separated ledger tracking past server load changes, peak student user concurrent session bursts, and resource pool history limits recorded into `status-history.csv`. Useful for sizing hardware requirements for future target exercise events.
# CEI Labs Engine

## Analyst Training Range & CTF Infrastructure Platform

CEI Labs Engine is a self-hosted analyst training range designed to support technical skill development through progressively challenging hands-on exercises.

Built on Kubernetes (K3s), CEI Labs provides a scalable platform for hosting Capture-the-Flag (CTF) events, analyst development programs, and competition preparation exercises.

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
* Custom Scenario-Based Training

These capabilities are part of the long-term roadmap and are not required for the current event deployment.

---

# Training Tracks

The current curriculum is organized around progressive learning tracks designed to accommodate multiple experience levels.

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

| Component          | Purpose                             |
| ------------------ | ----------------------------------- |
| K3s                | Lightweight Kubernetes Platform     |
| CTFd               | Competition Management Platform     |
| MultiJuicer        | Dynamic OWASP Juice Shop Deployment |
| OWASP Juice Shop   | Web Security Training Environment   |
| Kali noVNC         | Browser-Based Analyst Workstations  |
| Analyst Containers | SSH-Based Training Environments     |
| MariaDB            | Persistent Application Database     |
| Redis              | Caching and Session Management      |
| MetalLB            | Bare-Metal Load Balancing           |
| Traefik            | Ingress and Routing                 |

---

# Supported Deployment Models

CEI Labs supports multiple deployment architectures.

## Single Node

Recommended for:

* Development
* Testing
* Small Training Events

Resources:

* 4 CPU Cores
* 16 GB RAM
* 100 GB SSD

Supports approximately:

* Up to 10 concurrent users

---

## Dual Node

Recommended for:

* Small Team Exercises
* Department Training

Resources:

* 8 CPU Cores Total
* 32 GB RAM Total

Supports approximately:

* Up to 20 concurrent users

---

## Three Node Cluster

Recommended for:

* Production Events
* Competition Hosting

Resources:

* 12 CPU Cores Total
* 64 GB RAM Total

Supports approximately:

* Up to 30 concurrent users

---

# High-Level Architecture

```text
                        Internet
                            │
                            ▼
                        Traefik
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
      CTFd            MultiJuicer         Workstations
        │                   │                   │
        ▼                   ▼                   ▼
     MariaDB          Juice Shop Pods      Kali / SSH
        │
        ▼
      Redis

```

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

---

## Non-Linux Hosts

Windows and macOS users must deploy CEI Labs within a Linux virtual machine.

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

Prepare scripts:

```bash
chmod +x scripts/*.sh
```

Initialize case storage:

```bash
./scripts/setup-cases.sh
```

Launch the installation wizard:

```bash
./scripts/install.sh
```

The installer will:

* Detect your operating system
* Install required dependencies
* Configure K3s
* Configure Kubernetes services
* Configure CTFd
* Configure MultiJuicer
* Configure supporting infrastructure

---

# Initial CTFd Configuration

After deployment:

1. Open the CTFd web interface.
2. Complete the initial setup wizard.
3. Create an administrator account.
4. Generate an API token.
5. Add the token to:

```yaml
ansible/group_vars/all.yml
```

6. Load challenge content:

```bash
./scripts/challenges-load.sh
```

---

# Monitoring

Launch the platform monitoring dashboard:

```bash
./scripts/status.sh
```

Available views include:

* Infrastructure Health
* Kubernetes Status
* Service Availability
* Resource Utilization
* Workspace Activity
* Historical Usage Metrics

---

# Event Readiness Roadmap

## Phase 1 — Platform Stability

* Validate deployments
* Verify backups
* Verify recovery procedures
* Verify persistence

## Phase 2 — Training Delivery

* Deploy Bandit curriculum
* Deploy Natas curriculum
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

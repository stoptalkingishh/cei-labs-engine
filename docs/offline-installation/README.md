# Offline / air-gapped installation

`scripts/offline-install.sh` (in the repo root's `scripts/`, sibling to
`stack-up.sh`) is a fully offline installer for a Fedora machine that will
never have internet access. It assumes every RPM, container image, and
Python wheel it needs is already vendored under the same directory tree it
runs from (`rpms/`, `images/`, `wheels/`, `repos/`) — none of that vendored
content lives in this git repo (it's multiple gigabytes; see below for
where it actually lives and how to rebuild it).

Read in this order:
- **[BUNDLE-CONTENTS.md](BUNDLE-CONTENTS.md)** — what's vendored, the exact
  directory layout the installer expects, and why images get retagged
  under multiple names at load time.
- **[KNOWN-GAPS.md](KNOWN-GAPS.md)** — every place this bundle is
  deliberately incomplete or unverified, including a real dependency-closure
  bug found by testing (not by reading docs).
- **[VERIFICATION.md](VERIFICATION.md)** — an honest verified-vs-unverified
  breakdown of the installer's 9 steps. No real Fedora hardware was
  available when this was built — say so rather than claim more than was
  actually tested.

## Where the vendored content lives

The RPMs/images/wheels/repo-copies themselves are not committed here —
they were built on 2026-07-22 on an internet-connected Windows machine
(Docker Desktop, Swarm mode) and written directly to a USB drive for
physical transfer to the air-gapped target, at roughly 4.4 GB total. There
is currently no single reusable "build the bundle" script — the vendoring
was done as a sequence of ad hoc commands (`docker pull`/`docker build`/
`docker save` per image, `dnf download --resolve --alldeps` inside a
`fedora:44` container for RPMs, `pip download` for wheels), not a checked-in
tool. If you need to rebuild this bundle from scratch, `BUNDLE-CONTENTS.md`
documents exactly what was fetched/built and from where, in enough detail
to reproduce the process by hand; turning that into a single script is
worthwhile future work, not done yet.

## Running the installer

`scripts/offline-install.sh` computes its own directory as the bundle root
(`REPO_ROOT`) and expects `rpms/`, `images/`, `wheels/`, and `repos/` to be
its siblings — **not** nested under a normal `cei-labs-engine` checkout's
`scripts/` folder. When building or updating a physical bundle, copy this
script to sit next to those four vendored directories at the bundle's top
level (this is what happened on the drive built 2026-07-22: the script
lived at the drive's root alongside `rpms/`, `images/`, `wheels/`,
`repos/`). This copy in the git repo is the tracked source of truth to copy
from, not something you run in place inside a checkout.

On the air-gapped Fedora target, once the drive (or however the vendored
content gets transferred) is mounted with that layout:

```bash
sudo ./offline-install.sh
```

It logs to `install-run.log` next to itself, runs all 9 steps independently
(one failing doesn't block the rest), and prints a pass/fail summary at the
end along with the CTFd admin token/password file paths if step 8 succeeded.

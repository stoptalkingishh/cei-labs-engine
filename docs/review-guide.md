# Review guide: the nine fix PRs

Each PR is independently reviewable against `main`. Merge order is in
`docs/merge-stack.md`. This is what to actually read for each — not a summary
of the diff, but the claim being made and the thing that would prove it wrong.

Everything here has been run. Where a claim is "verified", that means it was
executed, not reasoned about.

---

## #79 — docs/correct-deploy-reference-docs (merge 1st, low risk)

**Claim:** the docs an operator follows during a deploy contained two errors.

- `docs/network-prerequisites.md` gave the orchestrator SSH port range as
  `30000–32767` (code default is `32000`) and said the orchestrator and
  bulk-workspace ranges **share** it. `config.py`, `.env.example` and the
  Ansible firewall rules all say they are deliberately **disjoint**. This is
  the document operators use as their firewall source of truth.
- `docs/local-testing-deployment.md` listed 6 of the 8 required secrets,
  omitting `credential_encryption_key.txt` — which `create_app()` requires at
  startup with no fallback, so following that page yields a crash-looping
  orchestrator.

**Check:** the two `grep`-able claims (`32000`, `8`) against `config.py` and
`docker/secrets.example/`. The `chmod`-style spot check is
`grep -c . docker/secrets.example/*.txt` = 8.

**Also fixes** the `.gitignore` hole for Traefik TLS material, and fills in ~15
missing CHANGELOG entries. The CHANGELOG says 3.8.6 now; verify against
`docker/ctfd/Dockerfile`.

---

## #83 — deps/security-bumps-and-image-pinning (merge 2nd, low risk)

**Claim:** 8 advisories cleared, and the last unpinned base image pinned.

- `cryptography` 45.0.6 → 50.0.1. Seven advisories, two memory-safety class,
  and one meaning the **bundled OpenSSL** is itself vulnerable. No 46.x stop
  resolves it (OpenSSL fix is 48.0.1, PKCS#7 fix is 50.0.0).
- `flask` 3.0.3 → 3.1.3, `requests` 2.32.3 → 2.34.2 in **both** plugins that
  pin it.

**The claim worth challenging:** that the crypto major jump is safe for
already-persisted data. Verified by generating a token under 45.0.6 and
decrypting it under 50.0.1 — the stored credential came back intact. The
orchestrator uses only `cryptography.fernet`, so none of the seven advisories
reach the code path in use.

**Check:** the `tcp-gateway` digest is real and is a multi-arch index digest, so
it survives on arm64. A fabricated digest would break the build.

**Deliberately declined:** `--generate-hashes`. Wheel hashes are
per-architecture and these build multi-arch, so it converts a hardening change
into a portability regression. Worth a separate per-arch change in CI.

---

## #86 — fix/plugin-xss-hardening-and-access-control-tests (merge 3rd, medium)

**Claim 1 — stored XSS.** Hint content went to `innerHTML` with no sanitization.
The original justification was that `markdown()` matches "the exact same
cmark-gfm pipeline CTFd uses" — but CTFd renders
`description|markdown|sanitize`, and the **sanitize step is separate and was
never applied**. cmark-gfm runs with `CMARK_OPT_UNSAFE`, so raw HTML passes
through by design.

Check: `routes.py` now calls `sanitize_html(markdown(...))`, and
`sanitize_html` delegates to `CTFd.utils.security.sanitize` when importable so
production and challenge descriptions share one policy.

**Claim 2 — the tests could not have caught an access-control regression.**
All five route suites stubbed `authed_only`/`admins_only` to identity, so
deleting `@admins_only` from an admin route kept all 100+ tests green. The
stubs now mark the function and each suite walks the real `url_map`, with a
guard that fails if a route exists the table doesn't assert on.

Verify by deleting a decorator yourself and confirming tests fail — the author
reports 2, 2, 7 and 4 failures per plugin.

Also gates the bulk `/api/solves` behind `admins_only`. Access control there was
already correct; the concern is the unthrottled bulk surface.

---

## #84 — ci/validate-yaml-noop-and-workflow-hardening (merge 4th, low risk)

**Claim:** `validate-yaml` validated **zero** files on every run while reporting
success. The guard tested path components of `os.walk`'s root string, and on
POSIX every component starts with the literal `.`, including the top-level one.
Invisible on Windows, where `os.sep` is `\`.

Verified: 603 roots, 603 skipped, 0 parsed. Now 15 files. Zero discovered is
treated as a **failure**, so the class of bug can't report success again.

**Supersedes #76**, which fixed the same bug but pruned `.github/` too — so it
skipped all seven workflow files. Proof is in #76's closing comment.

**Reviewer check worth doing:** break a workflow YAML and confirm the validator
catches it. That is the exact case #76 missed.

The widened shellcheck now covers `docker/ctfd/docker-entrypoint-wrapper.sh`,
which was never linted, and that surfaced a real `SC2155` (`export X="$(cat …)"`
masks a failed `cat`, so CTFd could start with an empty `SECRET_KEY`).

---

## #78 — fix/scripts-secret-and-flag-handling (merge 5th, medium)

**Claim:** four deploy scripts reported success while doing nothing.

Highest impact: `install.sh` wrote every secret **unconditionally**, so
re-running it to add a worker replaced the DB passwords while the live MariaDB
volume kept the old ones — the next `docker stack deploy` then restarted CTFd
into permanent auth failure with the old value recorded nowhere.

Second: the "random 32-char secret" idiom
(`head -c 64 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32`) does not do what
it reads as. `tr` deletes 62/256 of bytes, so 64 raw bytes yield ~15.5 usable
characters. Over 2000 trials it **never once reached 32**.

**This is the one confirmed on real hardware.** On the station, the shipped
idiom produced 10–20 characters over 8 runs, and every pre-existing secret on
that box was 16–22 characters — including `ctf_key`, which seeds every Juice
Shop flag.

**Known gap:** this PR is **not** on the integration branch that was deployed to
the station, so these fixes are untested on hardware. The nine-PR stack
including it passes 308 + 187 tests.

---

## #87 — fix/deploy-script-idempotency-and-ansible-secrets (merge 6th, medium)

**Claim:** `offline-install.sh` step 6 did an unconditional `rm -rf` on the
install tree. Because the bundle ships `secrets.example` rather than a
populated `docker/secrets/`, the wipe left step 7's guard true — so it rebuilt
from `CHANGE_ME` placeholders and regenerated every secret, rotating the CTFd
session key, both DB passwords and the plugin shared secret behind a running
CTFd. Exactly the rotation `patch-secrets.sh` exists to make deliberate.

**Behaviour change a reviewer should accept explicitly:** the step now merges
instead of replacing. That is the fix, but it also means a re-run no longer
self-heals a corrupted install.

Also: `stack-down.sh` wasn't idempotent (second run died before the purge, so
`uninstall.sh` aborted mid-sequence); `status.sh` died on its first diagnostic;
`spawn-workspaces.sh` had no port ceiling (a 2100-line roster created 101
services inside the orchestrator's own range, exit 0, silent).

**Severity correction the author made:** the Ansible join-token exposure is at
`-v` and above, **not** every run — `set_fact` values are not printed at
default verbosity. Worth verifying, since the original finding overstated it.

---

## #77 — fix/naming-collisions-and-flaky-race-test (merge 7th, medium)

**Claim:** `slugify` truncated to 40 bytes with no disambiguator, so two owners
could get an **identical** Docker service name and Traefik hostname. Because
`create_service` is idempotent, team B silently adopts team A's running service
and network while B's store row records it as B's.

Not participant-triggerable today (`owner_id` is a short integer) but it arms
itself the moment `owner_id` becomes non-numeric.

**⚠️ Breaking change.** Identifiers long enough to truncate now map to
*different* names. No rename migration, by design — rewriting B's name onto A's
existing container is the bug. Affected instances must be drained and
relaunched. **This needs a `CHANGELOG.md` entry from a maintainer**; the
docstring says so but doesn't write one.

Also fixes `instance_id` being 81 chars (over the 63-byte DNS label limit, so
the URL could never resolve), and de-flakes a concurrency test that used
`time.sleep(0.25)` as a synchronization primitive.

---

## #75 — fix/orchestrator-security-hardening (merge 8th, high)

Three security fixes. The one to read most carefully:

**`_decrypt_plan` accepted any undecryptable value as plaintext JSON.** The
docstring described a `startswith('{')` guard the code never performed. Anyone
able to write the `orchestrator_data` volume could substitute a plan of their
choosing — service names, networks, published ports, the `LEVEL_SECRETS` blob
holding per-team flags — and have it accepted as authoritative.

**The deliberate non-fix, which a reviewer must accept:** `GET
/instances/<owner>/<key>` still returns flag values. That response is the only
thing that populates CTFd's `TeamChallengeSecret`, which is the only thing
`PerTeamDynamicFlag.compare()` validates against. Stripping it would make every
`per_team_dynamic` flag fail closed forever, silently. Documented at the call
site; a test is named to stop a future reader "cleaning it up".

This PR is the base for the #85 conflict — see `docs/merge-stack.md`.

---

## #85 — fix/orchestrator-lifecycle-correctness (merge 9th, high)

**Claim:** the reaper could destroy a live in-flight creation and the API still
returned 201. A reservation's age alone cannot distinguish a crashed worker from
a slow launch.

**Worth challenging:** the obvious fix — reordering `sweep()` so orphan
reconciliation runs before stale reservations are released — **does not work**.
The author verified the test still passes with that reordering reverted, because
the release still deletes the row and the later sweep reaps the containers. The
real fix is refusing to abandon a reservation while anything attributable to it
is still live in Docker. Don't let a reviewer "simplify" it back to the reorder.

Also: `reboot` returned 404 for an instance whose Docker service was merely
missing, whose correct client response is to relaunch (destroying progress);
`RangeStore` lost concurrent `target_keys` updates, leaking containers; teardown
deleted the store row even when the Docker teardown failed.

**Conflicts with #75.** The resolution is pre-applied and validated; the trap is
documented in `docs/merge-stack.md`.

---

## If you only have time for three

1. **#85's reaper fix** — reject the reorder-only version if you see one.
2. **#75's deliberate `GET /instances` leak** — confirm you agree the flag
   values must stay on that one route.
3. **#77's breaking change** — get a `CHANGELOG.md` entry written before merge.

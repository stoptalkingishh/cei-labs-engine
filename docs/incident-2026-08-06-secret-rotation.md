# Incident: per-team secrets rotating mid-event (2026-08-06)

**Station:** `cei-ryzen5-61g-swarm01` (3-node swarm: swarm01 manager/orchestrator, swarm02 CTFd, swarm03 worker)
**Impact:** every team's wargame passwords silently invalidated, repeatedly, across a live event
**Scope:** 13 team accounts, 29 instances, 8 ranges, 591 per-team secrets

## Summary

Players reported that their Bandit/Krypton/Natas accounts stopped working, while their CTFd scores still showed correctly. The passwords they had written down no longer logged in anywhere.

Every destructive orchestrator path regenerated a team's flags and level passwords from scratch, because **the secret vault that was supposed to prevent exactly this had never been committed, pushed, built, or deployed.** It existed only as uncommitted working-tree changes on the station itself.

A second, independent bug (an overlay network race) then compounded it by handing players broken environments, pushing them toward the one button that deliberately rotates their secrets.

## Why it stayed hidden

Nothing errored. When the orchestrator minted new secrets, CTFd's `_persist_and_scrub_secrets()` overwrote `instance_launcher_team_secrets` with the new values, so flag validation stayed perfectly self-consistent. The `solves` table was never touched.

The result: **the scoreboard kept showing progress players could no longer reach.** Scores looking correct was actively misleading — it was the strongest signal that "the platform is fine", and it was wrong.

## Impact detail

- Every team's `bandit1..33`, `krypton1..6` and `natas0..14` passwords rotated on each destructive teardown.
- Pre-rotation secrets are **permanently unrecoverable** — they were never persisted anywhere, which is the bug itself.
- Solves, scores and rankings were never affected.
- Team 27 ("Computers are evil") rotated a second time during the fix window (see Timeline) and was the worst affected.
- Teams were manually issued their current password for the highest level they had already solved, letting them resume at their high-water mark rather than replaying from level 0.

## Timeline (EDT, 2026-08-06)

| Time | Event |
|---|---|
| 08:47 | Stack up; orchestrator container starts. All instances dated from here. |
| 09:45–12:34 | 51 `network ... not found` service-create failures (root cause 2). Clusters during teardown churn. |
| ~13:00 | Investigation begins. Oldest instance 3.9h old against a **4.0h destructive ceiling** — minutes from the next wave of rotations. |
| 13:12 | `ORCHESTRATOR_MAX_INSTANCE_LIFETIME_MINUTES` raised 240 → 10080 on the live service. Bleeding stopped; 0 reaps, all instances intact. |
| ~13:15 | Vault fix committed and image built. |
| ~13:20 | Vault backfilled from live `plan_json` (21 rows, 503 secrets) — before rollout, so no exposure window. |
| 13:23 | **Rollout reported `converged` but was a no-op** (root cause 3). Container still ran a 10-day-old image. |
| 13:26 | Rebuilt as immutable `sha-9122802`, rolled out. Vault actually live. |
| ~13:30 | 3 instances created in the gap found unvaulted; backfilled. Over-capture and missing range vault found and repaired. |
| ~14:20 | Verified: 0 divergence, 29/29 instances and 8/8 ranges vaulted. |
| after | Zero rotations against a 591-fingerprint baseline. |

## Root cause 1 — no secret vault (primary)

`reaper._sweep_expired_instances()` measures the absolute lifetime from **`created_at`, not `last_accessed`**, and calls the destructive `controller.teardown()`. At `ORCHESTRATOR_MAX_INSTANCE_LIFETIME_MINUTES=240` that destroyed every team's box 4 hours in — mid-session, while they were actively playing — and the next launch generated entirely new secrets.

The fix for this (the `instance_secret_vault` / `range_secret_vault` tables and the `existing_secrets` plumbing) was **present on the station as uncommitted changes and nowhere else.** The deployed image contained none of it:

```
grep -c instance_secret_vault /app/app/store.py   ->  0
sqlite> .tables    -- no instance_secret_vault, no range_secret_vault
```

Several previously merged PRs made level *content* deterministic given a stable `LEVEL_SECRETS` — Wargames #45 (bandit13→14 SSH keypair derived from `bandit13`'s own secret), Wargames #44 (Krypton keystream), engine #48 (`plan_range_target` consuming `secret_keys` at all). Each was necessary and each genuinely landed. **None made `LEVEL_SECRETS` itself stable across a teardown**, which is why the problem looked addressed and wasn't.

Fixed by engine PR #51.

## Root cause 2 — network vanished mid-create (compounding)

`ensure_network()` returns early when the network already exists, and cannot distinguish a healthy network from one a concurrent teardown is midway through deleting (`remove_network()` retries for up to 15s while endpoints drain).

A player relaunching the instant their box is torn down lands in that window: `ensure_network()` sees the old network and returns, deletion completes, and `services.create()` fails against a network that no longer exists.

```
APIError: 500 Server Error ... ("network chnet-22-group-krypton not found")
WARNING app.docker_client: could not remove network chnet-22-group-krypton after 15.0s: 404 ...
```

The rollback's own `remove_network()` 404ing is the tell — the network was **already gone**, not merely unconverged, which rules out a propagation delay and explains why the existing inspect-visibility wait didn't help.

51 occurrences, 17 full create-path failures. It clusters during teardown churn — exactly when players relaunch. Because destructive reaps were firing every 4h, this was largely *downstream* of root cause 1: stop the reaping and the races stop too.

**This is how a network bug becomes a password bug.** The player is left with a broken environment, and the natural response is to click **Relaunch Environment** — the one path that deliberately rotates flags and passwords via `purge_vaulted_secrets()`.

Fixed by engine PR #52.

## Root cause 3 — a "successful" deploy that deployed nothing

`docker service update --force --no-resolve-image --image ...:latest` printed `converged` while the container kept running a 10-day-old image.

The station runs the **containerd snapshotter**, so `docker build -t <repo>:latest .` exported an attestation *manifest list*. `docker run` resolved to the fresh build while `docker images` and Swarm still had `:latest` pointing at the old image ID — the tag never moved in the store Swarm reads.

This cost a full rollout cycle and could easily have been mistaken for "the fix doesn't work". Working recipe and mandatory verification step are in [credential-lifecycle.md](credential-lifecycle.md).

## Contributing factor — "Relaunch Environment" rotates by design

`create_or_get(force_relaunch=True)` is the only path that calls `purge_vaulted_secrets()`, and that is intentional: an explicit reset should issue fresh secrets. But the control is a plainly-labelled button in the challenge modal, and a player whose environment looks broken will reasonably press it.

Left unchanged during the event (deliberately — no mid-event code changes). Worth revisiting: either a confirmation step spelling out that it destroys their password chain, or making it rebuild the container while preserving secrets.

## What changed

### Repository (pull requests)

| PR | Repo | Change |
|---|---|---|
| #51 | cei-labs-engine | Secret vault: `instance_secret_vault` + `range_secret_vault`, `existing_secrets` plumbing, `.env.example` lifetime default, `docs/credential-lifecycle.md` |
| #52 | cei-labs-engine | `create_service()` retries when its own managed network vanishes mid-create |
| #61 | CEI-Labs-Wargames | `RELEASE_STATE` sourced from `CEI_AGENT_RELEASE_STATE` instead of an in-place repo edit |

Test suite went 216 → 220 passing.

### Live station (not all of this is in git)

- **`docker/.env`: `ORCHESTRATOR_MAX_INSTANCE_LIFETIME_MINUTES` 240 → 10080.** `.env` is gitignored, so **this exists only on the station.** The rationale is mirrored into the tracked `.env.example` by #51, but a rebuilt station must set it again.
- **Orchestrator runs `ghcr.io/stoptalkingishh/cei-labs-engine/orchestrator:sha-9122802`, built locally and never pushed to GHCR.** That tag will not resolve on any other machine. The matching source is engine PR #51's commit.
- **Both vaults backfilled**: 29/29 instances, 8/8 ranges, 591 secrets, via `scripts/backfill-secret-vault.py`.

## Recovery performed

Pre-rotation secrets were unrecoverable, so teams were instead issued their **current** password for the highest level they had already solved — restoring their position without granting anything unearned.

Sources: the orchestrator vault for passwords, and the CTFd admin API (`/api/v1/teams/<id>/solves`) for what each team had legitimately earned. Note the event ran in **team mode**, so `owner_id` is `team_id`, not `user_id` — joining on `user_id` silently matches nothing.

Mapping reminder: per `entrypoint.sh`, `bandit(n+1)`'s login password is level *n*'s flag, so a team whose last solve is "Bandit 5 → 6" resumes as `bandit6` using `secrets['bandit5']`.

## Open items

- **Orphaned owners `1`, `25`, `28`** — genuine 404s against the CTFd teams API (deleted teams), still holding instances, published ports and vault rows against the 30-instance cap. Needs a reconciliation pass; the orchestrator has no notion of an owner disappearing from CTFd.
- **`IMAGE_TAG` drift** — `docker/.env` says `offline`, most services run `:latest`, the orchestrator runs `sha-9122802`. A `stack deploy` today would roll the orchestrator **backwards** off the fix. Reconcile before the next deploy.
- **Relaunch-button footgun** — see above.
- **`_decrypt_plan()` failure mode** — on a wrong/rotated `credential_encryption_key` it returns the ciphertext as if it were plaintext, so `get_vaulted_secrets()` fails with a `JSONDecodeError` rather than a clear "cannot decrypt vault" error. Latent, but it turns a key-management mistake into a confusing crash.

## Lessons

1. **A green scoreboard is not a healthy platform.** Self-consistency between CTFd's expected flag and the container's actual flag is preserved *by the bug*. Player-reported "my password stopped working" was the only real signal.
2. **Uncommitted work on a live station is invisible risk.** `git status` could not distinguish an event-day toggle from an unshipped critical fix. This is also why Wargames #61 exists.
3. **Verify deploys by inspecting the artifact, not the exit code.** `converged` meant nothing here.
4. **Deploying a data-persistence fix does not retroactively protect existing data.** The vault only engages on *create*; without the backfill, every already-running team would have stayed exposed until the exact moment their secrets were destroyed.
5. **Order matters under a race**: backfill *before* rollout. Three instances created in the ~6-minute gap were unprotected and had to be caught afterwards.

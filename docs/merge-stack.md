# Merge stack for the nine open fix PRs

Computed by trial-merging all 36 branch pairs in throwaway worktrees, then
merging the branches in sequence and asserting the orchestrator suite stayed
green at every step. Only **one** pair in the entire set conflicts.

| # | PR | branch | merge |
|---|---|---|---|
| 1 | #79 | `docs/correct-deploy-reference-docs` | clean |
| 2 | #83 | `deps/security-bumps-and-image-pinning` | clean |
| 3 | #86 | `fix/plugin-xss-hardening-and-access-control-tests` | clean |
| 4 | #84 | `ci/validate-yaml-noop-and-workflow-hardening` | clean |
| 5 | #78 | `fix/scripts-secret-and-flag-handling` | clean |
| 6 | #87 | `fix/deploy-script-idempotency-and-ansible-secrets` | clean |
| 7 | #77 | `fix/naming-collisions-and-flaky-race-test` | clean |
| 8 | #75 | `fix/orchestrator-security-hardening` | clean |
| 9 | #85 | `fix/orchestrator-lifecycle-correctness` | **conflict — resolution pre-applied** |

The only ordering constraint is that #75 and #85 are adjacent. Everything else
can move freely. The stacked tree reaches **308 orchestrator + 187 plugin tests
passing**, and 15 YAML files validated (was 0).

## The one conflict: #75 ↔ #85

Three hunks, in `docker/orchestrator/app/main.py` and
`docker/orchestrator/README.md`.

**The resolution is already applied and validated** on the
`integration/all-fixes-2026-09-30` branch at `b4632ee`. It has passed the full
test suite and was deployed to a live Fedora 44 station, where each fix was
confirmed inside the running containers.

### What a reviewer should check

The hunk that matters is the `_instance_response` signature. Both PRs redefine
it, differently:

- #75: `def _instance_response(record, cfg=None, include_flag_secrets: bool = False)`
- #85: `def _instance_response(record, cfg=None)`

Taking #85's version would make #75's call site — `GET /instances/<owner>/<key>`,
which passes `include_flag_secrets=True` — fail on **every** instance-status
response. That stops CTFd's `TeamChallengeSecret` from ever being populated, and
`PerTeamDynamicFlag.compare()` returns `False` when that row is missing. So
**every `per_team_dynamic` flag in the deployment would fail closed forever,
silently, with no error anywhere.**

The resolution keeps #75's superset signature and adds #85's `_shutdown_seconds`
helper alongside it. The other two hunks (module constants, README prose) are
purely additive.

#85 is self-contained against `main` — it references nothing #75 introduced. The
order is driven purely by the shared files, so the pair can swap if preferred.

## Where reviewer attention is worth spending

Not all nine carry the same risk. Highest first:

1. **#75 / #85 — the conflict above.** The one place a bad resolution is silent.
2. **#85** — changes teardown, reboot and shutdown semantics. Reboot returning
   502 instead of 404, and teardown no longer deleting the store row on
   failure, are both behaviour changes a player or operator could notice.
3. **#87** — `offline-install.sh` now merges instead of `rm -rf`ing the install
   tree. That is a deliberate behaviour change: a re-run will no longer
   overwrite a provisioned directory, which is the fix, but it also means a
   re-run no longer self-heals a corrupted install.
4. **#86** — `sanitize_html` delegates to CTFd's own sanitizer when importable
   and falls back to a hand-rolled allowlist in the test environment. Worth
   confirming the fallback's behaviour matches CTFd's policy closely enough, or
   that production always takes the delegate path.
5. **#77** — **breaking change.** Identifiers long enough to truncate now map to
   different service/network/hostname names. No rename migration by design.
   Existing long-identifier instances must be drained and relaunched. Needs a
   `CHANGELOG.md` entry written by a maintainer.
6. **#84 / #83 / #79 / #78** — low risk, independently verified. #84 makes CI
   actually validate YAML for the first time, so it may surface pre-existing
   problems on unrelated branches.

## Known gap

`integration/all-fixes-2026-09-30` contains eight of the nine PRs. **#78 is not
on it**, so those script fixes were not exercised on the live station. The
subject matter was confirmed there independently (the `head -c 64` idiom
produced 10–20 characters on that box, and every existing secret was 16–22
characters), but the fixes themselves are untested on hardware.

The nine-PR stacked tree including #78 has been re-verified: 308 + 187 tests
passing, all six #78 script fixes present.

## Executing the merge

Merge in order 1 → 9. Positions 1–8 are plain merges. For #85, GitHub will
report a conflict; take the resolved content of the two conflicted files from
`integration/all-fixes-2026-09-30` (`b4632ee`):

- `docker/orchestrator/app/main.py`
- `docker/orchestrator/README.md`

Then confirm the tree matches by diffing against that branch — the only
expected difference is nothing.

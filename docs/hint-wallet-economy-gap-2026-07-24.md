# Hint-wallet economy gap (2026-07-24)

Reported: hints cannot be unlocked at all, right out of the gate. Investigated
in `cei-labs-engine`/`CEI-Labs-Wargames` (both at their 2026-07-24 `main`
tips: `0f47f4a` / `f4d9291`). This is not a small bug — the crediting
mechanism was never actually built, and the intended economic model
(described by the user, see "Intended design" below) is materially
different from what exists today. Notes only; nothing here has been fixed.

## Confirmed root cause: wallet is never credited

`docker/orchestrator/app/wallet.py`'s `WalletStore.unlock_hint()` spends
from a per-owner `team_balance` row. Nothing in the codebase ever calls
`WalletStore.credit_balance()` / `POST /wallet/credit` in response to a
solve:

- `docker/ctfd/plugins/hint-wallet/` has no `solve_hook.py` — checked
  `git log --all` against that exact path in `cei-labs-engine`, **zero
  commits ever touch it.** It was never committed, not deleted.
- Stale evidence that it was built and tested at some point, just never
  saved: `__pycache__/solve_hook.cpython-312.pyc`,
  `tests/__pycache__/test_solve_hook.cpython-312-pytest-9.1.1.pyc`, and
  `tests/.pytest_cache/v/cache/nodeids` listing five passing
  `test_solve_hook.py` test names (`test_credit_wallet_adds_to_existing_balance`,
  `test_credit_wallet_creates_balance_row_for_first_time_team`,
  `test_credit_wallet_credits_different_challenges_independently`,
  `test_credit_wallet_is_idempotent_per_owner_and_challenge`,
  `test_credit_wallet_rejects_non_positive_amount`). Whoever wrote this
  work lost it before it landed — pycache and pytest-cache survived, source
  didn't.
- `hint-wallet/assets/hint-wallet.js` even references
  `instance-launcher/solve_hook.py` as "the pattern CTFd's flag-submission
  hook already documents," implying a hint-wallet equivalent was planned to
  follow the same `after_request` hook on `POST /api/v1/challenges/attempt`
  that `instance-launcher/solve_hook.py` uses.

Net effect: `team_balance` starts at 0 and never moves. Every
`/api/unlock` call hits `insufficient_balance`. This matches "not able to
open any hints out the gate" exactly.

## Intended design (as described, needs confirmation before building)

What exists today: hints cost a flat integer "cost" (validated in
`wallet.py:validate_bundle` as strictly increasing per tier) spent from one
shared per-team currency pool that (per the gap above) never gets credited
by anything.

What was described as the intended behavior instead:

1. **Cost is a percentage of the challenge's own point value, not a shared
   currency spend.** For a 100-point challenge: opening hint 1 costs 10%,
   opening hint 2 costs 50% (cumulative — not +50% on top of the first
   10%), opening hint 3 costs 85%. Whatever tier you've opened, you keep
   `100% - tier_cost%` of that challenge's points when you solve it. Fully
   opening all three hints leaves 15% of the challenge's value.
   - This is architecturally different from "spend points to unlock,"
     it's "your final score on this challenge is reduced by having peeked."
     Implies scoring integration (CTFd's normal point award needs to be
     reduced at solve time based on which tier was opened), not just a
     wallet debit.
2. **Hints are only unlockable for a 2-challenge window around your current
   position, not any challenge regardless of progress.** Nothing in the
   current code gates hint availability by solve order/adjacency at all —
   `api_unlock()` / `WalletStore.unlock_hint()` will happily unlock a hint
   for challenge 40 while challenge 1 is still unsolved.

### The two examples given for the window rule don't obviously agree — flag for whoever builds this

> "if i am now completed 1-10 and am on question 11 i should be able to
> look or work on 2 questions ahead of me hints. so question 11 and
> questions 12 hints should be unlockable"

Reads as: window = `[current, current+1]` = 2 challenges total, anchored
on the next *unsolved* challenge.

> "lets say i got question 12 done and now i should have the hints for
> question 11 and question 13."

If 1–10 were already solved and 12 just got solved, 11 would presumably
already be solved too by that point (these tracks are normally strictly
sequential — each level's answer is required to reach the next) — but the
example explicitly gives hints for 11 (already behind) *and* 13 (ahead),
which doesn't match "current + next" from the first example. Possible
readings that would reconcile the two:
   - Window is `[last_solved - 1, last_solved + 1]` (one behind, one
     ahead of your most recent solve), not "current + next."
   - Or the second example assumes non-sequential solving is possible and
     just means "the window follows wherever your frontier currently is,"
     in which case the two examples aren't actually contradictory, just
     under-specified about what "current" means when solve order isn't
     strictly linear.

**Do not guess on this — confirm the exact rule before implementing.** Off
by one here directly changes what players can see mid-event.

## What a real fix needs (not attempted here)

1. A decision on the windowing rule above.
2. `docker/orchestrator/app/wallet.py` / `store.py`: replace (or
   supplement) the flat shared-currency deduct with a per-challenge
   percentage-of-value model, including how it interacts with CTFd's own
   point award at solve time.
3. Progression-window gating added to `POST /wallet/deduct` (and/or
   `GET /api/tiers/...`) keyed off `CTFd.models.Solves` for the requesting
   owner — the pieces needed for this already exist elsewhere in this
   codebase (`instance-launcher/solve_hook.py` already queries `Solves`
   filtered by `account_id` for a similar purpose and is a reasonable
   reference).
4. `hint-wallet/solve_hook.py` (or equivalent) actually written, tested,
   and committed this time — whatever crediting/scoring mechanism the
   above design lands on needs a real hook on solve, matching the pattern
   `instance-launcher/solve_hook.py` already uses (`after_request` on
   `POST /api/v1/challenges/attempt`, matched by `challenge_id`).
5. `CEI-Labs-Wargames/scripts/hint_economy.py` and the generated
   `challenge.yml`/hint-wallet manifest tier costs would need to change
   from flat integers to percentages if design item 2 is adopted as
   described.

## Why this wasn't fixed in this pass

The described intended design doesn't match what's built, guessing at the
windowing rule or the credit/scoring mechanics risks shipping something
wrong for a real event's scoring, and this is large enough (orchestrator
wallet model, CTFd scoring integration, Wargames content-generation
changes) that it belongs as its own scoped implementation, not a
same-session fix.

## Update 2026-07-25: reproduced live by an actual player on the fresh 192.168.1.173 stand-up

Confirmed firsthand, playing against the freshly wiped-and-redeployed
environment (`cei-labs-engine`/`CEI-Labs-Wargames` both on latest `main`,
including today's wargame-stages crash fix): completed the first
challenge, wallet balance still reads 0 credits afterward. This is the
`solve_hook.py`-never-existed root cause above, now confirmed against a
completely clean database rather than possibly-stale state -- rules out
"maybe it just needed a fresh DB" as an explanation.

Also re-confirmed live: the described 2-challenge unlock window doesn't
exist in any form right now -- there's no solve-order/adjacency check
anywhere in `hint-wallet/routes.py` or `orchestrator/app/wallet.py`, so
this isn't a case of the window being present but miscalibrated; it's
simply not built yet, same as the crediting mechanism.

This PR is now the tracking issue for an actual implementation attempt.
The blocker for starting that attempt is unchanged from above: the two
windowing examples in "Intended design" still don't obviously resolve to
one rule, and shipping a guess here would put wrong data in front of
players mid-event. That confirmation is needed before code changes start,
not something to resolve by guessing under time pressure.

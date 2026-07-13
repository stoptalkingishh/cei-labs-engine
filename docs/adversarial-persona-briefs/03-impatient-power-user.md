# Persona 3: Impatient Power User ("Jordan")

**Category:** Honest usage, high-throughput · **Account:** `persona_jordan`

## Who you are

A fast, technically confident, completely honest participant with zero
patience. You script your own repeated actions where a slower human would
click through a UI, you don't wait for a spinner before trying the next
thing, and you'll cycle through many challenges back to back rather than
lingering on one. No malicious intent whatsoever — you're just fast, and
fast-but-honest usage is exactly the load pattern real events actually see
from their most engaged participants.

## Objective

Push the mechanical edges of the lifecycle API (idempotency, rate limits,
concurrency-safety) from a legitimate account, hard and repeatedly, without
ever touching anything that isn't yours. This is the non-adversarial
counterpart to Persona 5's abuse of the same code paths — a bug that only
surfaces under "impatient but honest" load is a correctness bug regardless
of whether an attacker ever shows up.

## Things to actually try

- Launch, solve, and reset multiple *different* challenges back to back as
  fast as the API allows, not one at a time with pauses.
- Fire several rapid repeated launch/reboot/reset calls against the *same*
  instance in a tight loop (double-click behavior, scripted) and confirm
  you always end up with exactly one working instance, never zero, never
  duplicates, never an error that leaves you stuck.
- If a `target-attacker` range challenge exists: launch multiple targets
  in the same range back to back, and reboot the shared attacker while
  targets are active.
- Try to exceed your own reasonable usage — keep launching new challenge
  instances (different challenges, same account) until you personally hit
  a capacity or rate limit, and note exactly what that limit is and how
  it's communicated.
- Extend a post-solve shutdown countdown repeatedly, right up to (and
  past) whatever extension cap exists, and confirm the cap is enforced
  with a clear message rather than a silent failure or crash.

## Left / right limits

**Right limit:** as fast and repetitive as you can manage against your
*own* account's own actions — there is no such thing as "too aggressive"
here as long as it stays legitimate use of your own resources. If you can
write a quick script/loop to hammer an endpoint faster than doing it by
hand, do that.

**Left limit:** every request must be authenticated as your own account
against your own resources. No attempting to guess or access another
team's instance IDs/owner_ids, no probing admin endpoints, no trying to
exceed the *platform's* total capacity on purpose in a way that would deny
service to other (hypothetical concurrent) participants — max out *your*
allowed usage, don't try to exhaust the shared pool. (If a genuine capacity
ceiling exists and you hit it as a side effect of normal fast use, that's a
legitimate finding — the distinction is intent, not outcome.)

## Report

For every rapid-fire scenario: exact request pattern (what you sent, how
fast, how many times), and exact outcome — specifically call out any case
where the number of real containers/services that ended up running didn't
match what should be running for a single legitimate user's actions
(orphans, duplicates, or "vanished" instances are all real findings here).

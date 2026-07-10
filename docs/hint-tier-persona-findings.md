# Hint Tier Persona Testing: Findings

Self-tested as three fresh CTFd accounts (`persona_novice`,
`persona_intermediate`, `persona_expert` — real accounts, real teams,
zero prior solves, 5000-point testing balance each) against six
representative levels (Bandit 1 & 32, Krypton 1 & 6, Natas 1 & 14 — one
easy + one hard per track), unlocking and reading all 3 hint tiers live
through the real CTFd API for each. Full raw transcript captured before
any fixes below were applied.

## Finding 1 (real bug, fixed): several descriptions already gave away the exact technique for free

The most significant finding. Three descriptions — written during Stage
4's editorial pass, before the tiered hint system existed — embedded the
literal working technique directly in the free description text,
independent of any persona's skill level:

- **Krypton 1 → 2**: description included `*Hint: the tr command
  translates characters directly, e.g. tr "[:alpha:]"
  "N-ZA-Mn-za-m"*` — literally the complete working command. Tier 3
  (187 points) was reduced to a formality; nothing costs anything here
  in practice.
- **Natas 13 → 14**: description said "Prepend a real image header (e.g.
  `GIF89a`)" — the entire trick, for free, ahead of a 750-point tier 3.
- **Natas 14 → 15**: description said 'Inject SQL syntax (e.g. `" OR
  "1"="1`)' — the working SQLi payload, for free, ahead of a 750-point
  tier 3 on the single most expensive hint in the whole set.

This isn't a persona-specific issue — it affects every player
regardless of skill level, and it directly undermines the point of the
tier-cost model (why would anyone pay 750 points for tier 3 when the
free description already solves it?). **Fixed**: trimmed all three
descriptions to describe the mechanism/constraint without the literal
payload, moving that detail back into the paid tiers where it belongs.

Two similar-looking cases were reviewed and judged NOT to need fixing:
- **Krypton 0 → 1** ("Decode it, e.g. with the `base64 -d` command") —
  this level is deliberately trivial by design (no environment needed at
  all, matches real OverTheWire's own equivalent level), so a free
  pointer is appropriate here, not a leak.
- **Krypton 3 → 4** ("Hint: E, T, A, O, I, N are the most common letters
  in English") — general cryptanalysis knowledge, not this level's exact
  solve syntax; matches OverTheWire's own real page text for this exact
  level too. Left as-is.

A broader grep across all 59 descriptions for `e.g.`/`Hint:`/`for
example` found only these 5 candidates — the leak pattern does not
appear to be widespread beyond the 3 fixed cases, but this was a
pattern-match sweep, not a full manual re-read of all 56; worth keeping
in mind for any future description edits.

## Finding 2 (noted, not changed): terse-payload puzzles compress tier 2 and tier 3 together

**Bandit 32 → 33** (the uppercase-shell `$0` escape): tier 2 currently
names `$0` explicitly while explaining the mechanism. For most levels,
explaining the CONCEPT and revealing the literal PAYLOAD are naturally
separable — but this puzzle's entire solution is typing two characters,
`$0`. There's very little room to explain "what makes this work" without
also naming the exact token that solves it. Reviewed and judged
acceptable as a structural edge case for very short-payload puzzles
rather than a bug to fix — tier 2 still requires the player to go test
it themselves and understand *why* it works, rather than just handing
over "type `$0`" as an instruction. Flagging for awareness, not action.

## Finding 3 (noted, not changed): bare tier-1 pointers may be too sparse for the hardest levels

`` `man bash` `` (Bandit 32) and a bare SQL-injection Wikipedia link
(Natas 14) are correct per the tier-1 spec, but for these particular
levels the gap to a useful starting point is large — `man bash` is a
huge document with no obvious entry point toward "`$0` inside `bash -c`
defaults to the literal string `bash`." For simple levels (`man cat`,
`man diff`) the tier-1-to-problem connection is obvious; for the hardest
levels it may not be. Not changed, since narrowing the pointer (e.g. "`man
bash` — the `$0` special parameter") starts to blur into tier 2's job.
Noted for the user's call on whether tier 1 should be allowed slightly
more specificity on the hardest levels specifically.

## Finding 4 (test artifact, not a real issue): persona point budgets were slightly too tight

`persona_expert`'s Natas 14 tier-3 unlock failed mid-walkthrough with
"not enough points" — the six-level walkthrough's cumulative hint cost
(4253 points) came within 3 points of the 5000-point testing balance
before the most expensive single hint (750). Purely a self-imposed
testing-budget sizing issue, not a real design problem — the content
itself (natas-14 tier 3's technically-correct double-quote SQLi payload)
was already independently verified earlier via the admin account. No
action needed beyond awarding a larger balance for any future testing
pass.

## Per-persona read

- **Novice** (little to no computing background): tier 1 alone is
  reliably insufficient on its own across the board — by design, tier 1
  targets someone rusty on syntax, not someone starting from zero. This
  worked as intended everywhere sampled *except* where Finding 1's leaks
  made the whole tier chain moot. Tier 2 consistently provided enough
  conceptual footing to be a genuine "life saver" as intended.
- **Intermediate**: the tier progression read naturally end to end on
  every sampled level once Finding 1's leaks were accounted for — tier 1
  as a nudge, tier 2 as the real explanation, tier 3 as confirmation/
  completion.
- **Expert** (reviewing as a teaching tool, not needing the hints):
  content is technically accurate against this repo's own real target
  implementations (already independently verified during authoring),
  tone is non-condescending throughout, and the escalating structure is
  sound pedagogy. Finding 1 is exactly the kind of thing an expert
  reviewer catches immediately — "why does this cost 750 points when the
  answer is already visible for free above" — and was the main
  substantive issue surfaced by this pass.

# Persona 1: Baseline Participant ("Alex")

**Category:** Honest usage · **Account:** `persona_alex`

## Who you are

A participant with no special skill or malice — reasonably competent,
here to learn, following the published instructions in good faith. You are
the control group: if the happy path doesn't work cleanly for you, nothing
else in this exercise matters.

## Objective

Walk the complete real participant journey end to end, exactly once, at a
normal human pace, and report anything that didn't work exactly as the
participant-facing docs (`docs/participant-quickstart.md`,
`docs/troubleshooting-faq.md` if present) say it should.

## Journey

1. Register an account/team on CTFd (`https://ctfd.<BASE_DOMAIN>`, or hit
   it directly by IP with a `Host:` header if DNS isn't set up in the test
   environment — ask for the exact URL and any `/etc/hosts` workaround
   needed if it's not obvious).
2. Log in, read the rules/challenge list.
3. Launch at least one challenge of each available `instance_type` present
   in the deployment (`single-target`, and `target-attacker` if any range
   challenges exist).
4. Connect using the access info CTFd/the orchestrator gives back exactly
   as instructed (SSH for single-target, browser/noVNC for a range
   attacker) — no shortcuts, no reading the answer out of infrastructure
   you're not meant to have.
5. Solve at least one challenge legitimately and submit the correct flag.
6. Try the reset/reboot button on a running instance and confirm you get
   your environment back in a usable state.
7. Log out and back in; confirm your progress and running instance are
   still there.

## Left / right limits

**Right limit:** everything a real, well-behaved participant would
naturally do, including reasonable exploration of the CTFd UI and reading
every doc/hint available to you.

**Left limit:** stay on the documented path. Do not attempt to bypass,
guess, or brute-force anything — that's Persona 4's job, and mixing the two
would muddy which persona a finding belongs to. If something seems
"suspiciously easy" (e.g. an answer visible somewhere it shouldn't be),
report it as a finding here rather than pulling on the thread yourself.

## Report

For each step above: did it work as documented, first try? Exact error
messages/screenshots-in-text (copy the literal text) for anything that
didn't. Note total wall-clock time for the full journey — that's a real
data point for participant-experience planning even absent any bugs.

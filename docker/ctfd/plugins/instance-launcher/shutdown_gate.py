"""docker/ctfd/plugins/instance-launcher/shutdown_gate.py

Pure logic extracted out of solve_hook.py specifically so it's unit
testable without CTFd installed (no CTFd imports in this file at all).
"""


def gate_satisfied(gate_challenge_ids: set, solved_challenge_ids: set) -> bool:
    """A shared instance's post-solve shutdown is only due once every
    challenge gating it (its whole instance_group, or just itself if it has
    none) has been solved by the same account."""
    return gate_challenge_ids.issubset(solved_challenge_ids)

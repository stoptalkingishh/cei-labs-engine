"""docker/ctfd/plugins/hint-wallet/progression.py

Pure logic for the hint-unlock progression window (cei-labs-event#7),
extracted out of routes.py specifically so it's unit testable without CTFd
installed -- same rationale as instance-launcher/shutdown_gate.py.

Window rule: hints are only unlockable for a track's next UNSOLVED
challenge, plus the one immediately after it. Example: a player has solved
levels 1-10 of a track and is currently on 11 -> hints for level 11 AND
level 12 are both unlockable. Once level 11 is solved (now on 12), the
window shifts to 12+13. If the whole track is already solved, nothing is
unlockable (there is no "next unsolved" to anchor on).

Each track (Bandit/Krypton/Natas/Sentinel) has its own independent sequence and
window -- a player's position in one track never affects another.
"""


def unlockable_window(ordered_challenge_ids, solved_challenge_ids) -> set:
    """`ordered_challenge_ids` is this track's challenges in sequence order
    (e.g. CTFd challenge ids sorted ascending, matching creation/level
    order). `solved_challenge_ids` is the set of ids this owner has solved,
    restricted to (or at least a superset is fine for) this same track.

    Returns the set of challenge ids in this track whose hints may be
    unlocked right now: the next unsolved challenge, plus the one right
    after it (empty set if every challenge in the track is solved, or the
    track has no challenges at all).
    """
    solved_challenge_ids = set(solved_challenge_ids)
    for index, challenge_id in enumerate(ordered_challenge_ids):
        if challenge_id not in solved_challenge_ids:
            return set(ordered_challenge_ids[index:index + 2])
    return set()


def is_unlockable(challenge_id, ordered_challenge_ids, solved_challenge_ids) -> bool:
    return challenge_id in unlockable_window(ordered_challenge_ids, solved_challenge_ids)

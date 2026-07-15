"""Participant launcher action validation with no CTFd dependencies."""

VALID_ACTIONS = frozenset({None, "reboot", "relaunch", "extend"})


def is_valid_action(action) -> bool:
    return action is None or (isinstance(action, str) and action in VALID_ACTIONS)

"""Pure unit tests for progression.py -- no CTFd/Flask needed at all."""
import importlib.util
import sys
from pathlib import Path

# Loaded via importlib (not a plain import) so this test file works whether
# or not CTFd is installed -- progression.py itself has zero CTFd imports,
# but this keeps the loading convention consistent with the rest of this
# plugin's test suite.
module_path = Path(__file__).resolve().parents[1] / "progression.py"
spec = importlib.util.spec_from_file_location("hint_wallet.progression", module_path)
progression = importlib.util.module_from_spec(spec)
sys.modules["hint_wallet.progression"] = progression
spec.loader.exec_module(progression)

unlockable_window = progression.unlockable_window
is_unlockable = progression.is_unlockable


def test_fresh_player_window_is_first_two_challenges():
    ordered = [1, 2, 3, 4, 5]
    assert unlockable_window(ordered, solved_challenge_ids=set()) == {1, 2}


def test_window_shifts_by_one_as_each_challenge_is_solved():
    ordered = list(range(1, 13))
    assert unlockable_window(ordered, {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}) == {11, 12}


def test_window_shifts_the_instant_the_anchor_challenge_is_solved():
    ordered = list(range(1, 14))
    # Solving 11 (previously the anchor) shifts the window to 12+13.
    assert unlockable_window(ordered, set(range(1, 12))) == {12, 13}


def test_last_challenge_in_track_has_a_window_of_just_itself():
    ordered = [1, 2, 3]
    assert unlockable_window(ordered, {1, 2}) == {3}


def test_fully_solved_track_has_an_empty_window():
    ordered = [1, 2, 3]
    assert unlockable_window(ordered, {1, 2, 3}) == set()


def test_empty_track_has_an_empty_window():
    assert unlockable_window([], set()) == set()


def test_challenge_far_ahead_of_the_window_is_not_unlockable():
    ordered = list(range(1, 41))
    solved = {1}  # only level 1 solved -- level 40 must stay locked
    assert not is_unlockable(40, ordered, solved)
    # The window is [current, current+1] -- both 2 and 3 are unlockable
    # (level 2 is the next unsolved, level 3 is the one after it).
    assert is_unlockable(2, ordered, solved)
    assert is_unlockable(3, ordered, solved)
    assert is_unlockable(4, ordered, solved) is False


def test_solved_challenges_out_of_order_still_anchor_on_the_lowest_unsolved():
    # A player could in principle solve out of published order (e.g. found
    # a shortcut) -- the window must anchor on the lowest-index UNSOLVED
    # challenge (2, since 1 is solved and 3 was solved out of order), not
    # "most recently solved + 1". The window is still the anchor plus the
    # one after it, even though 3 is already solved.
    ordered = [1, 2, 3, 4, 5]
    assert unlockable_window(ordered, {1, 3}) == {2, 3}


def test_tracks_are_independent_of_each_other():
    bandit_ordered = [101, 102, 103]
    krypton_ordered = [201, 202, 203]
    # Fully solved Bandit, untouched Krypton -- Krypton's window must still
    # be its own first two challenges, unaffected by Bandit progress.
    bandit_solved = {101, 102, 103}
    krypton_solved = set()
    assert unlockable_window(bandit_ordered, bandit_solved) == set()
    assert unlockable_window(krypton_ordered, krypton_solved) == {201, 202}

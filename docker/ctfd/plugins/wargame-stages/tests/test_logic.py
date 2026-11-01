import ast
import importlib.util
import unittest
from datetime import datetime, timedelta
from pathlib import Path

MODULE = Path(__file__).parents[1] / "logic.py"
SPEC = importlib.util.spec_from_file_location("wargame_stage_logic", MODULE)
logic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(logic)


def test_default_stages_include_sentinel_contract():
    module = Path(__file__).parents[1] / "__init__.py"
    tree = ast.parse(module.read_text())
    default_stages = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DEFAULT_STAGES"
            for target in node.targets
        )
    )

    assert ("sentinel", "Sentinel - Security Operations", "Security Operations", 4, 22) in default_stages


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 14, 9, 0, 0)

    def test_start_is_idempotent_and_timestamp_never_resets(self):
        state, started, changed = logic.start_stage("pending", None, self.now)
        self.assertEqual((state, started, changed), ("active", self.now, True))
        later = self.now + timedelta(hours=1)
        self.assertEqual(logic.start_stage(state, started, later), ("active", self.now, False))

    def test_invalid_start_is_rejected(self):
        with self.assertRaises(logic.InvalidTransition):
            logic.start_stage("closed", None, self.now)

    def test_lock_is_idempotent(self):
        state, cutoff, changed = logic.lock_stage("active", None, self.now)
        self.assertEqual(logic.lock_stage(state, cutoff, self.now + timedelta(minutes=2)), ("locked", self.now, False))
        self.assertTrue(changed)

    def test_close_active_creates_score_cutoff(self):
        result = logic.close_stage("active", None, None, self.now)
        self.assertEqual(result, ("closed", self.now, self.now, True))


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 7, 14, 9, 0, 0)

    def event(self, account, name, points, seconds):
        return logic.SolveEvent(account, name, points, self.start + timedelta(seconds=seconds))

    def test_start_and_lock_boundaries_are_inclusive(self):
        events = [self.event(1, "A", 5, -1), self.event(1, "A", 10, 0), self.event(1, "A", 20, 60), self.event(1, "A", 50, 61)]
        rows = logic.rank_solves(events, self.start, self.start + timedelta(seconds=60))
        self.assertEqual(rows[0]["score"], 30)
        self.assertEqual(rows[0]["solve_count"], 2)

    def test_ranking_uses_score_then_elapsed_then_name(self):
        events = [self.event(2, "Zulu", 100, 30), self.event(1, "Alpha", 100, 20), self.event(3, "Bravo", 50, 1)]
        rows = logic.rank_solves(events, self.start)
        self.assertEqual([row["account_id"] for row in rows], [1, 2, 3])

    def test_overlapping_games_remain_independent(self):
        solves = [self.event(1, "Player", 10, 30), self.event(1, "Player", 20, 90)]
        first = logic.rank_solves(solves, self.start, self.start + timedelta(seconds=60))
        second = logic.rank_solves(solves, self.start + timedelta(seconds=60))
        self.assertEqual(first[0]["score"], 10)
        self.assertEqual(second[0]["score"], 20)

    def test_no_start_means_no_score(self):
        self.assertEqual(logic.rank_solves([self.event(1, "A", 10, 0)], None), [])

    def test_csv_cells_neutralize_spreadsheet_formulas(self):
        self.assertEqual(logic.safe_csv_cell("=HYPERLINK('bad')"), "'=HYPERLINK('bad')")
        self.assertEqual(logic.safe_csv_cell("ordinary-team"), "ordinary-team")


if __name__ == "__main__":
    unittest.main()

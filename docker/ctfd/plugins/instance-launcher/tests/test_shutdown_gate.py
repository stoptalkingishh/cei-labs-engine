# shutdown_gate.py has no CTFd dependency, but this plugin's directory isn't
# a valid Python package name (hyphen) — same importlib-by-path loading as
# test_orchestrator_client.py.
import importlib.util
import os

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "shutdown_gate.py")
spec = importlib.util.spec_from_file_location("shutdown_gate", MODULE_PATH)
shutdown_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shutdown_gate)
gate_satisfied = shutdown_gate.gate_satisfied


def test_solo_challenge_gate_satisfied_once_solved():
    assert gate_satisfied({1}, {1}) is True


def test_solo_challenge_gate_not_satisfied_when_unsolved():
    assert gate_satisfied({1}, set()) is False


def test_group_not_satisfied_until_all_solved():
    assert gate_satisfied({1, 2, 3}, {1, 2}) is False


def test_group_satisfied_when_all_solved():
    assert gate_satisfied({1, 2, 3}, {1, 2, 3}) is True


def test_extra_unrelated_solves_do_not_matter():
    assert gate_satisfied({1, 2}, {1, 2, 99}) is True


def test_empty_gate_is_trivially_satisfied():
    assert gate_satisfied(set(), {1, 2}) is True

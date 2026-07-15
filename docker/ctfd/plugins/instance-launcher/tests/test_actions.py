import importlib.util
from pathlib import Path


module_path = Path(__file__).resolve().parents[1] / "actions.py"
spec = importlib.util.spec_from_file_location("instance_launcher_actions", module_path)
actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actions)
VALID_ACTIONS = actions.VALID_ACTIONS
is_valid_action = actions.is_valid_action


def test_expected_actions_are_valid():
    assert VALID_ACTIONS == {None, "reboot", "relaunch", "extend"}
    assert all(is_valid_action(action) for action in VALID_ACTIONS)


def test_unknown_and_non_string_actions_are_rejected():
    for action in ("launch", "destroy", "unknown", "", 0, True, {}, []):
        assert not is_valid_action(action)

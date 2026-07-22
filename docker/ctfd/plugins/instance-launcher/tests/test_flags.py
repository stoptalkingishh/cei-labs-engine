import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace


class _Query:
    row = None

    @classmethod
    def filter_by(cls, **criteria):
        cls.criteria = criteria
        return cls

    @classmethod
    def first(cls):
        return cls.row


class _TeamChallengeSecret:
    query = _Query


ctfd = types.ModuleType("CTFd")
ctfd_plugins = types.ModuleType("CTFd.plugins")
ctfd_flags = types.ModuleType("CTFd.plugins.flags")
ctfd_flags.BaseFlag = object
ctfd_utils = types.ModuleType("CTFd.utils")
ctfd_user = types.ModuleType("CTFd.utils.user")
ctfd_user.get_current_user = lambda: SimpleNamespace(account_id=7)

sys.modules.update(
    {
        "CTFd": ctfd,
        "CTFd.plugins": ctfd_plugins,
        "CTFd.plugins.flags": ctfd_flags,
        "CTFd.utils": ctfd_utils,
        "CTFd.utils.user": ctfd_user,
    }
)

plugin_package = types.ModuleType("instance_launcher")
plugin_package.__path__ = []
models = types.ModuleType("instance_launcher.models")
models.TeamChallengeSecret = _TeamChallengeSecret
sys.modules["instance_launcher"] = plugin_package
sys.modules["instance_launcher.models"] = models

module_path = Path(__file__).resolve().parents[1] / "flags.py"
spec = importlib.util.spec_from_file_location("instance_launcher.flags", module_path)
flags = importlib.util.module_from_spec(spec)
sys.modules["instance_launcher.flags"] = flags
spec.loader.exec_module(flags)


def _challenge():
    return SimpleNamespace(challenge_id=42)


def test_alpha_flag_accepts_case_and_surrounding_whitespace_variants():
    _Query.row = SimpleNamespace(value="CipherAnswer")

    assert flags.PerTeamDynamicAlphaFlag.compare(
        _challenge(), " \tCiPhErAnSwEr\r\n"
    )
    assert _Query.criteria == {"owner_id": "7", "challenge_id": 42}


def test_alpha_flag_still_rejects_a_different_value():
    _Query.row = SimpleNamespace(value="CipherAnswer")

    assert not flags.PerTeamDynamicAlphaFlag.compare(_challenge(), "CipherAnswers")


def test_non_alpha_dynamic_flags_remain_case_and_whitespace_sensitive():
    _Query.row = SimpleNamespace(value="Exact-Token_123")

    assert flags.PerTeamDynamicFlag.compare(_challenge(), "Exact-Token_123")
    assert not flags.PerTeamDynamicFlag.compare(_challenge(), "exact-token_123")
    assert not flags.PerTeamDynamicFixedFlag.compare(
        _challenge(), " Exact-Token_123 "
    )

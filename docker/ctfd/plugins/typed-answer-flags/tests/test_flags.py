import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


ctfd = types.ModuleType("CTFd")
ctfd_plugins = types.ModuleType("CTFd.plugins")
ctfd_flags = types.ModuleType("CTFd.plugins.flags")
ctfd_flags.BaseFlag = object
ctfd_flags.FLAG_CLASSES = {}
sys.modules.update(
    {
        "CTFd": ctfd,
        "CTFd.plugins": ctfd_plugins,
        "CTFd.plugins.flags": ctfd_flags,
    }
)

module_path = Path(__file__).resolve().parents[1] / "flags.py"
spec = importlib.util.spec_from_file_location("typed_answer_flags.flags", module_path)
flags = importlib.util.module_from_spec(spec)
sys.modules["typed_answer_flags.flags"] = flags
spec.loader.exec_module(flags)


def _stored(answer_spec):
    return SimpleNamespace(data=json.dumps(answer_spec), content="typed-answer (private spec in data)")


def test_aliases_preserve_ctfgenerator_normalization():
    stored = _stored({"kind": "aliases", "answers": ["Sao-Paulo", "São Paulo"]})

    assert flags.TypedAnswerFlag.compare(stored, "  SÃO, PAULO  ")
    assert not flags.TypedAnswerFlag.compare(stored, "Rio de Janeiro")


def test_identifier_preserves_configured_prefix_and_separator_normalization():
    stored = _stored(
        {
            "kind": "identifier",
            "value": "IMO-9387421",
            "strip_prefixes": ["IMO"],
            "strip_separators": True,
        }
    )

    assert flags.TypedAnswerFlag.compare(stored, "imo: 9387-421")
    assert not flags.TypedAnswerFlag.compare(stored, "9387422")


def test_coordinate_preserves_haversine_tolerance():
    stored = _stored(
        {
            "kind": "coordinate",
            "latitude": 51.5007,
            "longitude": -0.1246,
            "tolerance_meters": 150,
        }
    )

    assert flags.TypedAnswerFlag.compare(stored, "51.5010,-0.1246")
    assert not flags.TypedAnswerFlag.compare(stored, "51.5100,-0.1246")


def test_multipart_preserves_structured_field_matching():
    stored = _stored(
        {
            "kind": "multipart",
            "fields": {
                "entity": {"kind": "aliases", "answers": ["Aster Research"]},
                "registration": {
                    "kind": "identifier",
                    "value": "AR-2048",
                    "strip_prefixes": [],
                    "strip_separators": True,
                },
            },
        }
    )

    assert flags.TypedAnswerFlag.compare(
        stored, '{ "registration": "ar 2048", "entity": "ASTER-RESEARCH" }'
    )
    assert not flags.TypedAnswerFlag.compare(
        stored, '{"registration":"AR-9999","entity":"Aster Research"}'
    )


@pytest.mark.parametrize(
    "data",
    [
        "not-json",
        "[]",
        json.dumps({"kind": "aliases", "answers": []}),
        json.dumps({"kind": "unsupported"}),
    ],
)
def test_malformed_or_unsupported_specs_fail_closed(data):
    assert not flags.TypedAnswerFlag.compare(SimpleNamespace(data=data, content="unused"), "candidate")


def test_non_string_candidate_fails_closed():
    stored = _stored({"kind": "aliases", "answers": ["expected"]})

    assert not flags.TypedAnswerFlag.compare(stored, None)


def test_candidate_is_never_logged(caplog):
    candidate = "DO-NOT-LOG-candidate-7391"
    stored = _stored({"kind": "aliases", "answers": [candidate]})

    with caplog.at_level(logging.DEBUG):
        assert flags.TypedAnswerFlag.compare(stored, candidate)
    assert candidate not in caplog.text


def test_register_adds_only_the_typed_answer_flag_class():
    ctfd_flags.FLAG_CLASSES.clear()

    flags.register(None)

    assert ctfd_flags.FLAG_CLASSES == {"typed_answer": flags.TypedAnswerFlag}

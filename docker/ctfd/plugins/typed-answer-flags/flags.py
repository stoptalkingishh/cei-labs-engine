"""CTFd custom flag type backed by CTFGenerator answer specifications."""

from __future__ import annotations

import json

from CTFd.plugins.flags import BaseFlag
from ctf_generator.domain.answers.models import AnswerSpecError, parse_answer_spec


class TypedAnswerFlag(BaseFlag):
    """Validate a submission with an immutable CTFGenerator answer spec."""

    name = "typed_answer"
    templates = {
        "create": "/plugins/typed-answer-flags/assets/flags/create.html",
        "update": "/plugins/typed-answer-flags/assets/flags/edit.html",
    }

    @staticmethod
    def compare(chal_key_obj, provided):
        if not isinstance(provided, str):
            return False
        try:
            stored = json.loads(chal_key_obj.data)
            verifier = parse_answer_spec(stored)
            return verifier.matches(provided)
        except (AnswerSpecError, json.JSONDecodeError, TypeError, ValueError, UnicodeError):
            return False


def register(app) -> None:
    from CTFd.plugins.flags import FLAG_CLASSES

    FLAG_CLASSES["typed_answer"] = TypedAnswerFlag

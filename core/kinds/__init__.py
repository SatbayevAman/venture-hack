"""Реестр видов задач сверх встроенных `equation` и `expression`.

Каждый вид — отдельный модуль с функцией
`check(statement, raw_lines, reference_len=None, reference_answer=None) -> CheckResult`.
`checker.check_problem` сначала ищет вид здесь, поэтому новая тема появляется
в журнале, портрете и карте класса без правок `pipeline`, `portrait` и `app.py`.
"""
from __future__ import annotations

from typing import Callable

KINDS: dict[str, Callable] = {}

from . import inequality  # noqa: E402 — модули видов сами импортируют checker

KINDS["inequality"] = inequality.check

from . import system  # noqa: E402

KINDS["system"] = system.check

from . import biquadratic  # noqa: E402

KINDS["biquadratic"] = biquadratic.check

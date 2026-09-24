"""Синтетическая история класса: 8 учеников × 6 домашних работ.

ВАЖНО: это синтетические данные для демо (помечены source='synthetic').
Мы генерируем только «рукописные» строки решений — как если бы их
распознала vision-модель. Все ошибки, методы и привычки в журнал записывает
та же проверка SymPy, что и в живом режиме, — портрет ничего не знает о том,
какой вариант мы задумали.
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta

from . import db, pipeline
from .tags import tag_comment_keywords

CLASS_NAME = "8 «А»"
STUDENTS = ["Айгерим", "Данияр", "Мадина", "Арман", "Жанель", "Тимур", "Алия", "Ерасыл"]


def _n(v: int) -> str:
    return f"−{abs(v)}" if v < 0 else str(v)


def _quad_statement(b: int, c: int) -> str:
    s = "x²"
    if b:
        s += f" {'−' if b < 0 else '+'} {abs(b) if abs(b) != 1 else ''}x"
    if c:
        s += f" {'−' if c < 0 else '+'} {abs(c)}"
    return s + " = 0"


def _quad_variants(b: int, c: int, r1: int, r2: int) -> dict:
    """Приведённое x² + bx + c = 0 с корнями r1 < r2."""
    D = b * b - 4 * c
    sD = int(round(D ** 0.5))
    bb = f"({_n(b)})²" if b < 0 else f"{b}²"
    cc = f"({_n(c)})" if c < 0 else f"{c}"
    four_c = 4 * c
    dline = f"D = {bb} − 4·{cc} = {b * b} {'−' if four_c > 0 else '+'} {abs(four_c)} = {D}"
    mb = -b
    ans = f"Ответ: {_n(r1)}; {_n(r2)}"
    disc = [dline, f"x₁ = ({_n(mb)} + {sD})/2 = {_n(r2)}", f"x₂ = ({_n(mb)} − {sD})/2 = {_n(r1)}", ans]
    wD = (sD + 2) ** 2
    w1, w2 = (mb + sD + 2) / 2, (mb - sD - 2) / 2
    fmt = lambda v: _n(int(v)) if float(v).is_integer() else str(v).replace(".", ",").replace("-", "−")
    disc_calc = [dline.rsplit("=", 1)[0] + f"= {wD}",
                 f"x₁ = ({_n(mb)} + {sD + 2})/2 = {fmt(w1)}",
                 f"x₂ = ({_n(mb)} − {sD + 2})/2 = {fmt(w2)}",
                 f"Ответ: {fmt(w2)}; {fmt(w1)}"]
    disc_sign = [dline, f"x₁ = ({_n(b)} + {sD})/2 = {_n(-r1)}", f"x₂ = ({_n(b)} − {sD})/2 = {_n(-r2)}",
                 f"Ответ: {_n(-r2)}; {_n(-r1)}"]
    vieta = [f"x₁ + x₂ = {_n(mb)}", f"x₁ · x₂ = {_n(c)}", f"x₁ = {_n(r1)}, x₂ = {_n(r2)}", ans]
    vieta_calc = [f"x₁ + x₂ = {_n(mb)}", f"x₁ · x₂ = {_n(c)}", f"x₁ = {_n(r1 - 1)}, x₂ = {_n(r2 + 1)}",
                  f"Ответ: {_n(r1 - 1)}; {_n(r2 + 1)}"]
    fx = lambda r: f"(x {'−' if r > 0 else '+'} {abs(r)})" if r else "x"
    factor = [f"{fx(r1)}{fx(r2)} = 0", f"x = {_n(r1)} или x = {_n(r2)}", ans]
    short = [f"x₁ = {_n(r1)}, x₂ = {_n(r2)}"]
    return {"disc": disc, "disc_calc": disc_calc, "disc_sign": disc_sign, "vieta": vieta,
            "vieta_calc": vieta_calc, "factor": factor, "short": short}


def _check_line(statement: str, root) -> str:
    """«Проверка: подставляем корень в условие»."""
    r = _n(root) if isinstance(root, int) else str(root)
    val = f"({r})" if str(r).startswith("−") else r
    s = statement
    import re
    s = re.sub(r"(\d)x", r"\1·x", s)
    s = s.replace("x", val)
    return f"Проверка: {s} ✓"


HOMEWORKS = [
    {  # ДЗ №1
        "due": "2026-09-04 23:59",
        "linear": {"statement": "3x + 5 = x − 3", "answer": "-4", "root": -4,
                   "ok": ["3x − x = −3 − 5", "2x = −8", "x = −4", "Ответ: −4"],
                   "sign": ["3x − x = −3 + 5", "2x = 2", "x = 1", "Ответ: 1"],
                   "calc": ["3x − x = −3 − 5", "2x = −6", "x = −3", "Ответ: −3"],
                   "short": ["x = −4"]},
        "quad": (-5, 6, 2, 3),
        "trap": {"statement": "x² = 5x", "answer": "0; 5", "root": 5,
                 "ok_factor": ["x² − 5x = 0", "x(x − 5) = 0", "x = 0 или x = 5", "Ответ: 0; 5"],
                 "ok_disc": ["x² − 5x = 0", "D = 25 − 0 = 25", "x₁ = (5 + 5)/2 = 5", "x₂ = (5 − 5)/2 = 0", "Ответ: 0; 5"],
                 "lost": ["x = 5", "Ответ: 5"],
                 "short": ["x₁ = 0, x₂ = 5"]},
        "simp": {"statement": "(a + 3)² − (a − 3)(a + 3)", "answer": "6a + 18",
                 "ok": ["a² + 6a + 9 − (a² − 9)", "a² + 6a + 9 − a² + 9", "6a + 18"],
                 "fsu": ["a² + 9 − (a² − 9)", "a² + 9 − a² + 9", "18"],
                 "sign": ["a² + 6a + 9 − (a² − 9)", "a² + 6a + 9 − a² − 9", "6a"],
                 "calc": ["a² + 6a + 9 − (a² − 9)", "a² + 6a + 9 − a² + 9", "6a + 16"],
                 "short": ["6a + 18"]},
    },
    {  # ДЗ №2
        "due": "2026-09-08 23:59",
        "linear": {"statement": "2(x − 3) + 4 = 3x − 1", "answer": "-1", "root": -1,
                   "ok": ["2x − 6 + 4 = 3x − 1", "2x − 3x = −1 + 6 − 4", "−x = 1", "x = −1", "Ответ: −1"],
                   "sign": ["2x + 6 + 4 = 3x − 1", "2x − 3x = −1 − 10", "−x = −11", "x = 11", "Ответ: 11"],
                   "calc": ["2x − 6 + 4 = 3x − 1", "2x − 3x = −1 + 6 − 4", "−x = 2", "x = −2", "Ответ: −2"],
                   "short": ["x = −1"]},
        "quad": (-5, -6, -1, 6),
        "trap": {"statement": "x²/(x − 2) = (3x − 2)/(x − 2)", "answer": "1", "root": 1,
                 "ok_disc": ["ОДЗ: x ≠ 2", "x² = 3x − 2", "x² − 3x + 2 = 0", "D = 9 − 8 = 1", "x₁ = (3 + 1)/2 = 2", "x₂ = (3 − 1)/2 = 1", "x = 2 не подходит по ОДЗ", "Ответ: 1"],
                 "ok_factor": ["ОДЗ: x ≠ 2", "x² − 3x + 2 = 0", "(x − 1)(x − 2) = 0", "x = 1 или x = 2", "x = 2 — посторонний корень", "Ответ: 1"],
                 "ok_vieta": ["ОДЗ: x ≠ 2", "x² − 3x + 2 = 0", "x₁ + x₂ = 3", "x₁ · x₂ = 2", "x₁ = 1, x₂ = 2", "x = 2 не подходит", "Ответ: 1"],
                 "extra": ["x² = 3x − 2", "x² − 3x + 2 = 0", "D = 9 − 8 = 1", "x₁ = (3 + 1)/2 = 2", "x₂ = (3 − 1)/2 = 1", "Ответ: 1; 2"],
                 "short": ["x = 1"]},
        "simp": {"statement": "(x − 2)² + 4x", "answer": "x² + 4",
                 "ok": ["x² − 4x + 4 + 4x", "x² + 4"],
                 "fsu": ["x² − 4 + 4x", "x² + 4x − 4"],
                 "sign": ["x² − 4x + 4 − 4x", "x² − 8x + 4"],
                 "calc": ["x² − 4x + 4 + 4x", "x² + 8"],
                 "short": ["x² + 4"]},
    },
    {  # ДЗ №3
        "due": "2026-09-11 23:59",
        "linear": {"statement": "5x − 7 = 2x + 8", "answer": "5", "root": 5,
                   "ok": ["5x − 2x = 8 + 7", "3x = 15", "x = 5", "Ответ: 5"],
                   "sign": ["5x − 2x = 8 − 7", "3x = 1", "x = 1/3", "Ответ: 1/3"],
                   "calc": ["5x − 2x = 8 + 7", "3x = 15", "x = 3", "Ответ: 3"],
                   "short": ["x = 5"]},
        "quad": (-7, 10, 2, 5),
        "trap": {"statement": "x(x − 3) = 2(x − 3)", "answer": "2; 3", "root": 3,
                 "ok_factor": ["x(x − 3) − 2(x − 3) = 0", "(x − 3)(x − 2) = 0", "x = 3 или x = 2", "Ответ: 2; 3"],
                 "ok_disc": ["x² − 3x = 2x − 6", "x² − 5x + 6 = 0", "D = 25 − 24 = 1", "x₁ = (5 + 1)/2 = 3", "x₂ = (5 − 1)/2 = 2", "Ответ: 2; 3"],
                 "lost": ["x = 2", "Ответ: 2"],
                 "short": ["x₁ = 2, x₂ = 3"]},
        "simp": {"statement": "3(x − 2) − 2(x + 1)", "answer": "x − 8",
                 "ok": ["3x − 6 − 2x − 2", "x − 8"],
                 "fsu": ["3x − 6 − 2x − 2", "x − 8"],
                 "sign": ["3x − 6 − 2x + 2", "x − 4"],
                 "calc": ["3x − 6 − 2x − 2", "x − 7"],
                 "short": ["x − 8"]},
    },
    {  # ДЗ №4
        "due": "2026-09-15 23:59",
        "linear": {"statement": "4(x + 1) = 2x − 6", "answer": "-5", "root": -5,
                   "ok": ["4x + 4 = 2x − 6", "4x − 2x = −6 − 4", "2x = −10", "x = −5", "Ответ: −5"],
                   "sign": ["4x + 4 = 2x − 6", "4x − 2x = −6 + 4", "2x = −2", "x = −1", "Ответ: −1"],
                   "calc": ["4x + 1 = 2x − 6", "4x − 2x = −6 − 1", "2x = −7", "x = −3,5", "Ответ: −3,5"],
                   "short": ["x = −5"]},
        "quad": (-2, -8, -2, 4),
        "trap": {"statement": "3x² = 12x", "answer": "0; 4", "root": 4,
                 "ok_factor": ["3x² − 12x = 0", "3x(x − 4) = 0", "x = 0 или x = 4", "Ответ: 0; 4"],
                 "ok_disc": ["3x² − 12x = 0", "D = 144 − 0 = 144", "x₁ = (12 + 12)/6 = 4", "x₂ = (12 − 12)/6 = 0", "Ответ: 0; 4"],
                 "lost": ["3x = 12", "x = 4", "Ответ: 4"],
                 "short": ["x₁ = 0, x₂ = 4"]},
        "simp": {"statement": "(2b + 1)² − 4b²", "answer": "4b + 1",
                 "ok": ["4b² + 4b + 1 − 4b²", "4b + 1"],
                 "fsu": ["4b² + 1 − 4b²", "1"],
                 "sign": ["4b² + 4b + 1 + 4b²", "8b² + 4b + 1"],
                 "calc": ["4b² + 4b + 1 − 4b²", "4b + 2"],
                 "short": ["4b + 1"]},
    },
    {  # ДЗ №5
        "due": "2026-09-18 23:59",
        "linear": {"statement": "7 − 2x = 3x + 22", "answer": "-3", "root": -3,
                   "ok": ["−2x − 3x = 22 − 7", "−5x = 15", "x = −3", "Ответ: −3"],
                   "sign": ["−2x − 3x = 22 + 7", "−5x = 29", "x = −5,8", "Ответ: −5,8"],
                   "calc": ["−2x − 3x = 22 − 7", "−5x = 16", "x = −3,2", "Ответ: −3,2"],
                   "short": ["x = −3"]},
        "quad": (-7, 12, 3, 4),
        "trap": {"statement": "x²/(x + 3) = 9/(x + 3)", "answer": "3", "root": 3,
                 "ok_disc": ["ОДЗ: x ≠ −3", "x² = 9", "x² − 9 = 0", "D = 0 + 36 = 36", "x₁ = 6/2 = 3", "x₂ = −6/2 = −3", "x = −3 не подходит", "Ответ: 3"],
                 "ok_factor": ["ОДЗ: x ≠ −3", "x² − 9 = 0", "(x − 3)(x + 3) = 0", "x = 3 или x = −3", "x = −3 не входит в ОДЗ", "Ответ: 3"],
                 "ok_vieta": ["ОДЗ: x ≠ −3", "x² − 9 = 0", "(x − 3)(x + 3) = 0", "x = 3 или x = −3", "x = −3 не входит в ОДЗ", "Ответ: 3"],
                 "extra": ["x² = 9", "x = ±3", "Ответ: 3; −3"],
                 "short": ["x = 3"]},
        "simp": {"statement": "(b + 5)(b − 5) − b(b − 2)", "answer": "2b − 25",
                 "ok": ["b² − 25 − (b² − 2b)", "b² − 25 − b² + 2b", "2b − 25"],
                 "fsu": ["b² + 25 − (b² − 2b)", "b² + 25 − b² + 2b", "2b + 25"],
                 "sign": ["b² − 25 − (b² − 2b)", "b² − 25 − b² − 2b", "−2b − 25"],
                 "calc": ["b² − 25 − (b² − 2b)", "b² − 25 − b² + 2b", "2b − 15"],
                 "short": ["2b − 25"]},
    },
    {  # ДЗ №6
        "due": "2026-09-22 23:59",
        "linear": {"statement": "6x + 1 = 2(x − 5) + 3", "answer": "-2", "root": -2,
                   "ok": ["6x + 1 = 2x − 10 + 3", "6x − 2x = −10 + 3 − 1", "4x = −8", "x = −2", "Ответ: −2"],
                   "sign": ["6x + 1 = 2x − 10 + 3", "6x − 2x = −10 + 3 + 1", "4x = −6", "x = −1,5", "Ответ: −1,5"],
                   "calc": ["6x + 1 = 2x − 10 + 3", "6x − 2x = −10 + 3 − 1", "4x = −6", "x = −1,5", "Ответ: −1,5"],
                   "short": ["x = −2"]},
        "quad": (2, -3, -3, 1),
        "trap": {"statement": "(x + 1)(x − 4) = 3(x − 4)", "answer": "2; 4", "root": 4,
                 "ok_factor": ["(x + 1)(x − 4) − 3(x − 4) = 0", "(x − 4)(x + 1 − 3) = 0", "(x − 4)(x − 2) = 0", "x = 4 или x = 2", "Ответ: 2; 4"],
                 "ok_disc": ["x² − 3x − 4 = 3x − 12", "x² − 6x + 8 = 0", "D = 36 − 32 = 4", "x₁ = (6 + 2)/2 = 4", "x₂ = (6 − 2)/2 = 2", "Ответ: 2; 4"],
                 "lost": ["x + 1 = 3", "x = 2", "Ответ: 2"],
                 "short": ["x₁ = 2, x₂ = 4"]},
        "simp": {"statement": "(y − 3)² − (y + 3)²", "answer": "−12y",
                 "ok": ["y² − 6y + 9 − (y² + 6y + 9)", "y² − 6y + 9 − y² − 6y − 9", "−12y"],
                 "fsu": ["y² − 6y + 9 − (y² + 9)", "y² − 6y + 9 − y² − 9", "−6y"],
                 "sign": ["y² − 6y + 9 − (y² + 6y + 9)", "y² − 6y + 9 − y² − 6y + 9", "−12y + 18"],
                 "calc": ["y² − 6y + 9 − (y² + 6y + 9)", "y² − 6y + 9 − y² − 6y − 9", "−10y"],
                 "short": ["−12y"]},
    },
]

# Новое ДЗ для живой проверки на демо
LIVE_HOMEWORK = {
    "due": "2026-09-25 23:59",
    "linear": {"statement": "2x − 9 = 5x + 3", "answer": "-4", "root": -4,
               "ok": ["2x − 5x = 3 + 9", "−3x = 12", "x = −4", "Ответ: −4"]},
    "quad": (-6, 5, 1, 5),
    "trap": {"statement": "x² = 7x", "answer": "0; 7", "root": 7,
             "ok_factor": ["x² − 7x = 0", "x(x − 7) = 0", "x = 0 или x = 7", "Ответ: 0; 7"],
             "lost": ["x = 7", "Ответ: 7"]},
    "simp": {"statement": "(m − 4)² + 8m", "answer": "m² + 16",
             "ok": ["m² − 8m + 16 + 8m", "m² + 16"]},
}

# Демо-работа Айгерим для живой проверки (как её распознала бы модель)
LIVE_DEMO_TEXT = {
    1: "2x − 5x = 3 + 9\n−3x = 12\nx = −4\nОтвет: −4",
    2: "D = (−6)² − 4·5 = 36 − 20 = 16\nx₁ = (6 + 4)/2 = 5\nx₂ = (6 − 4)/2 = 1\nОтвет: 1; 5",
    3: "x = 7\nОтвет: 7",
    4: "m² − 8m + 16 + 8m\nm² + 16",
}
LIVE_DEMO_COMMENT = "Снова торопится: разделила на x и потеряла корень x = 0."

# План класса: (линейное, квадратное, ловушка, упрощение, флаги) по 6 работам.
# Флаги: c — проверка ответа, L — сдано после срока.
PLAN = {
    "Айгерим": [("ok", "disc", "lost", "ok", ""), ("ok", "disc", "ok_disc", "ok", "c"),
                ("ok", "disc", "lost", "ok", ""), ("ok", "disc", "lost", "calc", ""),
                ("ok", "disc", "ok_disc", "ok", ""), ("ok", "disc", "lost", "ok", "")],
    "Данияр": [("sign", "factor", "ok_factor", "ok", "c"), ("sign", "vieta", "ok_factor", "ok", "c"),
               ("ok", "factor", "ok_factor", "sign", "c"), ("sign", "vieta", "ok_factor", "ok", "c"),
               ("ok", "vieta", "ok_factor", "ok", ""), ("sign", "factor", "ok_factor", "ok", "c")],
    "Мадина": [("ok", "vieta", "ok_factor", "ok", "c"), ("ok", "disc", "ok_vieta", "ok", "c"),
               ("ok", "factor", "ok_factor", "calc", "c"), ("ok", "vieta", "ok_factor", "ok", "c"),
               ("ok", "disc", "ok_factor", "ok", "c"), ("ok", "vieta", "ok_factor", "ok", "c")],
    "Арман": [("short", "disc", "ok_disc", "fsu", ""), ("short", "disc", "extra", "fsu", "L"),
              ("short", "disc_calc", "ok_disc", "short", ""), ("ok", "disc", "ok_disc", "fsu", "L"),
              ("short", "disc", "ok_disc", "ok", "L"), ("ok", "disc", "ok_disc", "fsu", "")],
    "Жанель": [("ok", "vieta", "ok_factor", "ok", "c"), ("ok", "vieta", "extra", "sign", ""),
               ("ok", "disc", "ok_disc", "ok", "c"), ("ok", "vieta", "ok_factor", "ok", ""),
               ("ok", "disc", "extra", "sign", "c"), ("ok", "factor", "ok_factor", "ok", "")],
    "Тимур": [("ok", "disc_calc", "ok_disc", "ok", ""), ("calc", "disc", "ok_disc", "ok", ""),
              ("ok", "disc_calc", "ok_disc", "ok", ""), ("ok", "disc", "ok_disc", "ok", "c"),
              ("ok", "disc", "ok_disc", "calc", ""), ("ok", "disc_calc", "ok_disc", "ok", "")],
    "Алия": [("short", "vieta", "ok_factor", "short", ""), ("short", "short", "ok_factor", "ok", ""),
             ("ok", "vieta", "ok_factor", "short", ""), ("short", "short", "ok_factor", "short", ""),
             ("short", "vieta", "ok_factor", "ok", ""), ("ok", "vieta", "ok_factor", "short", "")],
    "Ерасыл": [("ok", "vieta", "ok_factor", "ok", "L"), ("ok", "disc_sign", "ok_factor", "ok", ""),
               ("sign", "vieta", "lost", "ok", ""), ("ok", "vieta", "ok_factor", "ok", ""),
               ("sign", "vieta", "ok_factor", "fsu", ""), ("ok", "vieta", "lost", "ok", "L")],
}

COMMENTS = {
    ("Айгерим", 1): "Торопится, ответ не проверила.",
    ("Айгерим", 4): "Опять торопится — потеряла корень x = 0.",
    ("Айгерим", 6): "Торопится, решение обрывается на середине.",
    ("Данияр", 2): "Путает знаки при переносе слагаемых.",
    ("Данияр", 4): "Снова знак при переносе!",
    ("Мадина", 3): "Молодец, аккуратное решение.",
    ("Мадина", 5): "Отлично!",
    ("Арман", 2): "Неаккуратно, пропускает шаги. Сдал поздно.",
    ("Арман", 6): "Повторить формулы сокращённого умножения.",
    ("Жанель", 2): "ММЖ-ны жазуды ұмытты, бөгде түбір қалды.",
    ("Жанель", 5): "Жақсы, бірақ асықпа.",
    ("Тимур", 3): "Ошибки в вычислении дискриминанта.",
    ("Тимур", 6): "Торопится.",
    ("Алия", 3): "Только ответы, без решения.",
    ("Алия", 5): "Жауапты тексермейді.",
    ("Ерасыл", 3): "Не разобрался в теме, потерял корень.",
    ("Ерасыл", 6): "Сдал после срока.",
}


def _reference(hw: dict) -> dict:
    b, c, r1, r2 = hw["quad"]
    return {
        1: hw["linear"]["ok"],
        2: _quad_variants(b, c, r1, r2)["disc"],
        3: hw["trap"].get("ok_factor") or hw["trap"]["ok_disc"],
        4: hw["simp"]["ok"],
    }


def _add_assignment(conn, number: int, hw: dict) -> int:
    cur = conn.execute("INSERT INTO assignments (number, title, due_at) VALUES (?,?,?)",
                       (number, f"ДЗ №{number}", hw["due"] + ":00"))
    aid = cur.lastrowid
    b, c, r1, r2 = hw["quad"]
    ref = _reference(hw)
    rows = [
        (1, "linear", "equation", hw["linear"]["statement"], hw["linear"]["answer"]),
        (2, "quadratic", "equation", _quad_statement(b, c), f"{r1}; {r2}"),
        (3, "quadratic", "equation", hw["trap"]["statement"], hw["trap"]["answer"]),
        (4, "simplify", "expression", hw["simp"]["statement"], hw["simp"]["answer"]),
    ]
    for idx, skill, kind, st, ans in rows:
        reference = [l for l in ref[idx] if not l.lower().startswith("ответ")]
        conn.execute(
            "INSERT INTO problems (assignment_id, idx, skill, kind, statement, reference, answer) VALUES (?,?,?,?,?,?,?)",
            (aid, idx, skill, kind, st, json.dumps(reference, ensure_ascii=False), ans),
        )
    return aid


# Задания по новым темам (агент Бета) — для живой проверки, без синтетических сдач.
# Задача: (skill, kind, statement, эталонные строки, ответ). Строки «Ответ» в эталон не входят.
EXTRA_ASSIGNMENTS = [
    {"number": 8, "title": "ДЗ №8 «Неравенства»", "due": "2026-09-29 23:59", "problems": [
        ("inequality", "inequality", "2 − 3x < 11",
         ["−3x < 11 − 2", "−3x < 9", "x > −3", "Ответ: (−3; +∞)"], "(−3; +∞)"),
        ("inequality", "inequality", "x² − x − 6 ≤ 0",
         ["x² − x − 6 = 0", "D = 1 + 24 = 25", "x₁ = (1 + 5)/2 = 3", "x₂ = (1 − 5)/2 = −2", "+ − +",
          "Ответ: [−2; 3]"], "[−2; 3]"),
        ("inequality", "inequality", "(x − 3)/(x + 1) ≥ 0",
         ["ОДЗ: x ≠ −1", "x₁ = 3, x₂ = −1", "+ − +", "Ответ: (−∞; −1) ∪ [3; +∞)"], "(−∞; −1) ∪ [3; +∞)"),
    ]},
    {"number": 9, "title": "ДЗ №9 «Системы уравнений»", "due": "2026-10-02 23:59", "problems": [
        ("system", "system", "2x + y = 7; x − y = 2",
         ["y = 7 − 2x", "x − (7 − 2x) = 2", "3x − 7 = 2", "3x = 9", "x = 3", "y = 7 − 6 = 1", "Ответ: (3; 1)"], "(3; 1)"),
        ("system", "system", "x² + y² = 25; x + y = 7",
         ["y = 7 − x", "x² + (7 − x)² = 25", "2x² − 14x + 24 = 0", "x² − 7x + 12 = 0", "x₁ = 3, x₂ = 4",
          "y₁ = 4, y₂ = 3", "Ответ: (3; 4), (4; 3)"], "(3; 4), (4; 3)"),
    ]},
]

# Типичные ошибки для синтетической истории по новым темам (только при PORTRET_EXTRA_SEED=1):
# номер задания → {idx задачи: {вариант: строки}}
EXTRA_VARIANTS = {
    8: {
        1: {"flip": ["−3x < 11 − 2", "−3x < 9", "x < −3", "Ответ: (−∞; −3)"],
            "sign": ["−3x < 11 + 2", "−3x < 13", "x > −13/3", "Ответ: (−13/3; +∞)"]},
        2: {"boundary": ["x² − x − 6 = 0", "x₁ = 3, x₂ = −2", "Ответ: (−2; 3)"],
            "choice": ["x² − x − 6 = 0", "x₁ = 3, x₂ = −2", "Ответ: (−∞; −2] ∪ [3; +∞)"]},
        3: {"domain": ["x₁ = 3, x₂ = −1", "+ − +", "Ответ: (−∞; −1] ∪ [3; +∞)"],
            "mul": ["x − 3 ≥ 0", "x ≥ 3", "Ответ: [3; +∞)"]},
    },
    9: {
        1: {"subst": ["y = 7 − 2x", "x − 7 − 2x = 2", "−x = 9", "x = −9", "y = 25", "Ответ: (−9; 25)"],
            "swap": ["y = 7 − 2x", "x − (7 − 2x) = 2", "3x = 9", "x = 3", "y = 1", "Ответ: (1; 3)"]},
        2: {"lost": ["y = 7 − x", "x² + (7 − x)² = 25", "x² − 7x + 12 = 0", "x = 3", "y = 4", "Ответ: (3; 4)"],
            "fsu": ["y = 7 − x", "x² + (7 − x)² = 25", "x² + 49 + x² = 25", "2x² = −24", "Ответ: нет решений"]},
    },
}
# ученик → номер задания → варианты по задачам (None — эталонное решение)
EXTRA_PLAN = {
    "Айгерим": {8: (None, "boundary", "domain"), 9: (None, "lost")},
    "Данияр": {8: ("flip", None, None), 9: ("subst", None)},
    "Мадина": {8: (None, None, None), 9: (None, None)},
    "Арман": {8: ("sign", "choice", "domain"), 9: ("swap", "fsu")},
    "Жанель": {8: (None, None, "domain"), 9: ("subst", None)},
    "Тимур": {8: ("flip", None, "mul"), 9: (None, "lost")},
    "Алия": {8: (None, "boundary", None), 9: ("swap", None)},
    "Ерасыл": {8: ("flip", "choice", None), 9: ("subst", "lost")},
}


def _add_extra_assignment(conn, number: int, title: str, due: str, problems: list) -> int:
    cur = conn.execute("INSERT INTO assignments (number, title, due_at) VALUES (?,?,?)",
                       (number, title, due + ":00"))
    aid = cur.lastrowid
    for idx, (skill, kind, st, ref, ans) in enumerate(problems, start=1):
        reference = [l for l in ref if not l.lower().startswith("ответ")]
        conn.execute(
            "INSERT INTO problems (assignment_id, idx, skill, kind, statement, reference, answer) VALUES (?,?,?,?,?,?,?)",
            (aid, idx, skill, kind, st, json.dumps(reference, ensure_ascii=False), ans),
        )
    return aid


def _extra_synthetic(conn, sids: dict, rng: random.Random) -> None:
    """Синтетические сдачи по новым темам — только при PORTRET_EXTRA_SEED=1 (иначе меняются числа демо)."""
    for ex in EXTRA_ASSIGNMENTS:
        aid = db.q1(conn, "SELECT id FROM assignments WHERE number=?", (ex["number"],))["id"]
        pid = {p["idx"]: p["id"] for p in pipeline.problems_of(conn, aid)}
        due = datetime.strptime(ex["due"], "%Y-%m-%d %H:%M")
        for name, plan in EXTRA_PLAN.items():
            variants = plan.get(ex["number"])
            if not variants:
                continue
            answers = {}
            for idx, v in enumerate(variants, start=1):
                ref = ex["problems"][idx - 1][3]
                answers[pid[idx]] = list(EXTRA_VARIANTS[ex["number"]][idx][v] if v else ref)
            ts = due - timedelta(hours=rng.randint(3, 50), minutes=rng.randint(0, 59))
            pipeline.record_submission(conn, sids[name], aid, answers, ts.strftime("%Y-%m-%d %H:%M:%S"),
                                       photo_name=None, source="synthetic")


def build(conn, progress=None) -> None:
    db.init(conn)
    rng = random.Random(2026)
    sids = {}
    for name in STUDENTS:
        sids[name] = conn.execute("INSERT INTO students (alias, class_name) VALUES (?,?)",
                                  (name, CLASS_NAME)).lastrowid
    aids = [_add_assignment(conn, i + 1, hw) for i, hw in enumerate(HOMEWORKS)]
    _add_assignment(conn, len(HOMEWORKS) + 1, LIVE_HOMEWORK)
    conn.commit()

    total = len(STUDENTS) * len(HOMEWORKS)
    done = 0
    for hw_i, (hw, aid) in enumerate(zip(HOMEWORKS, aids)):
        probs = pipeline.problems_of(conn, aid)
        pid = {p["idx"]: p["id"] for p in probs}
        b, c, r1, r2 = hw["quad"]
        qv = _quad_variants(b, c, r1, r2)
        due = datetime.strptime(hw["due"], "%Y-%m-%d %H:%M")
        for name in STUDENTS:
            lin, quad, trap, simp, flags = PLAN[name][hw_i]
            answers = {
                pid[1]: list(hw["linear"][lin]),
                pid[2]: list(qv[quad]),
                pid[3]: list(hw["trap"].get(trap) or hw["trap"].get("ok_factor") or hw["trap"]["ok_disc"]),
                pid[4]: list(hw["simp"][simp]),
            }
            if "c" in flags:  # проверка подстановкой в линейном и квадратном
                answers[pid[1]].append(_check_line(hw["linear"]["statement"], hw["linear"]["root"]))
                answers[pid[2]].append(_check_line(_quad_statement(b, c), r2))
            if "L" in flags:
                ts = due + timedelta(hours=rng.randint(10, 40), minutes=rng.randint(0, 59))
            else:
                ts = due - timedelta(hours=rng.randint(3, 50), minutes=rng.randint(0, 59))
            sub_id, _ = pipeline.record_submission(
                conn, sids[name], aid, answers, ts.strftime("%Y-%m-%d %H:%M:%S"),
                photo_name=None, source="synthetic",
            )
            text = COMMENTS.get((name, hw_i + 1))
            if text:
                pipeline.record_comment(conn, sub_id, text, tag_comment_keywords(text), "keywords",
                                        (ts + timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S"))
            done += 1
            if progress:
                progress(done / total)
    for ex in EXTRA_ASSIGNMENTS:
        _add_extra_assignment(conn, ex["number"], ex["title"], ex["due"], ex["problems"])
    if os.environ.get("PORTRET_EXTRA_SEED") == "1":
        _extra_synthetic(conn, sids, random.Random(2027))
    conn.commit()


def live_assignment_id(conn) -> int:
    """ДЗ для живого демо — всегда №7 (len(HOMEWORKS) + 1), даже если заданий больше."""
    row = db.q1(conn, "SELECT id FROM assignments WHERE number=?", (len(HOMEWORKS) + 1,))
    return row["id"] if row else db.q1(conn, "SELECT id FROM assignments ORDER BY number DESC LIMIT 1")["id"]

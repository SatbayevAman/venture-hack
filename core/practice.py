"""Тренажёр ученика: задачи под слабое место, разбор своей ошибки, сигнал в журнал.

Здесь нет языковой модели. Задачи строятся из шаблонов с целыми параметрами,
и эталон каждой задачи прогоняется через ту же проверку `check_problem`, что и
домашние работы: задача, эталон которой не прошёл проверку, отбрасывается.

Исход каждой задачи, где была ошибка, попадает в журнал наблюдений с
source="practice" и одним из тегов-привычек:
  * self_fixed        — исправил(а) сам(а) по наводящему вопросу (ступень 1);
  * fixed_after_rule  — исправил(а) после подсказки-правила (ступень 2);
  * needs_example     — понадобился разобранный пример (ступень 3) или задача не решена.
Портрет считает только source auto и teacher, поэтому его числа не меняются.
Тренировки не пишутся в submissions: иначе изменились бы знаменатели портрета.
"""
from __future__ import annotations

import json
import math
import random
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from . import db, portrait, tags as T
from .checker import check_problem, normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_sessions (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    mode TEXT NOT NULL,              -- fix_own | weak_spot | review
    target_tag TEXT,
    target_skill TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS practice_items (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    skill TEXT NOT NULL,
    statement TEXT NOT NULL,
    reference TEXT NOT NULL,         -- JSON-список строк эталона
    answer TEXT,
    source_submission_id INTEGER,    -- для режима «разбор своей ошибки»
    source_problem_id INTEGER,
    target_tag TEXT,                 -- что тренирует задача
    requirement TEXT,                -- JSON: {"check": true} | {"min_lines": k}
    template TEXT,
    solved INTEGER NOT NULL DEFAULT 0,
    outcome TEXT,                    -- self_fixed | fixed_after_rule | needs_example
    review_id INTEGER                -- задача-повторение: строка practice_reviews
);
CREATE TABLE IF NOT EXISTS practice_attempts (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL,     -- 0 — исходная работа в режиме «разбор своей ошибки»
    lines TEXT NOT NULL,             -- JSON-список строк попытки
    first_error_tag TEXT,
    first_error_line INTEGER,
    correct INTEGER,
    hint_level INTEGER NOT NULL,     -- сколько ступеней подсказки открыто до этой попытки
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS practice_reviews (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,        -- задача, которую нужно повторить
    due_at TEXT NOT NULL,
    done_at TEXT
);
CREATE INDEX IF NOT EXISTS practice_items_session ON practice_items(session_id);
CREATE INDEX IF NOT EXISTS practice_attempts_item ON practice_attempts(item_id);
"""

OUTCOMES = ("self_fixed", "fixed_after_rule", "needs_example")
CONFIDENCE = 0.9
MIN_ITEMS = 3            # задач с исходом, чтобы делать вывод
SELF_SHARE = 0.6
EXAMPLE_SHARE = 0.5
REVIEW_DAYS = (2, 5)     # задача, решённая только с примером, возвращается через 2 и 5 дней


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


# ======================================================================
# 1. Запись чисел и выражений — как в сиде: «−», «²», «·»
# ======================================================================

def _n(v: int) -> str:
    return f"−{abs(v)}" if v < 0 else str(v)


def _expr(terms: list) -> str:
    """[(3, "x"), (−5, "")] → «3x − 5»; коэффициент 1 при букве не пишется."""
    out = []
    for coef, mono in terms:
        if coef == 0:
            continue
        a = abs(coef)
        body = (mono if a == 1 else f"{a}{mono}") if mono else str(a)
        if not out:
            out.append(("−" if coef < 0 else "") + body)
        else:
            out.append(("− " if coef < 0 else "+ ") + body)
    return " ".join(out) or "0"


def _sum(nums: list) -> str:
    """Невычисленная сумма: [−5, 12, −3] → «−5 + 12 − 3»."""
    return _expr([(v, "") for v in nums]) if any(nums) else "0"


def _xm(p: int, v: str = "x") -> str:
    """(x − p) / (x + p)"""
    return f"({v} {'−' if p > 0 else '+'} {abs(p)})" if p else v


def _roots_text(vals) -> str:
    return "; ".join(_n(v) for v in sorted(vals))


def _note(ru: str, kk: str) -> dict:
    return {"ru": "← " + ru, "kk": "← " + kk}


# ======================================================================
# 2. Шаблоны задач. Каждый: rng → dict или None (неудачные параметры)
# ======================================================================

def _nz(rng, lo, hi, exclude=()):
    return rng.choice([v for v in range(lo, hi + 1) if v != 0 and v not in exclude])


NOTE_FACTOR = _note("здесь выносим x за скобки, а не делим на x: x может оказаться равным нулю",
                    "мұнда x-ке бөлмей, x-ті жақша сыртына шығарамыз: x нөлге тең болуы мүмкін")


def t_lost_kx(rng):
    k = _nz(rng, -9, 9, (1, -1))
    return {"statement": f"x² = {_expr([(k, 'x')])}", "kind": "equation", "skill": "quadratic",
            "reference": [f"{_expr([(1, 'x²'), (-k, 'x')])} = 0", f"x{_xm(k)} = 0", f"x = 0 или x = {_n(k)}"],
            "answer": _roots_text([0, k]), "key_line": 1, "note": NOTE_FACTOR}


def t_lost_axbx(rng):
    a = rng.randint(2, 5)
    m = _nz(rng, -6, 6, (1, -1))
    return {"statement": f"{a}x² = {_expr([(a * m, 'x')])}", "kind": "equation", "skill": "quadratic",
            "reference": [f"{_expr([(a, 'x²'), (-a * m, 'x')])} = 0", f"{a}x{_xm(m)} = 0", f"x = 0 или x = {_n(m)}"],
            "answer": _roots_text([0, m]), "key_line": 1, "note": NOTE_FACTOR}


def t_lost_common(rng):
    p = rng.randint(1, 7)
    q = _nz(rng, -6, 7, (p, 1, -1))
    return {"statement": f"x{_xm(p)} = {_n(q)}{_xm(p)}", "kind": "equation", "skill": "quadratic",
            "reference": [f"x{_xm(p)} {'−' if q > 0 else '+'} {abs(q)}{_xm(p)} = 0",
                          f"{_xm(p)}{_xm(q)} = 0", f"x = {_n(p)} или x = {_n(q)}"],
            "answer": _roots_text([p, q]), "key_line": 0,
            "note": _note(f"переносим всё влево и выносим общий множитель {_xm(p)}, а не сокращаем на него",
                          f"бәрін солға шығарып, ортақ {_xm(p)} көбейткішін жақша сыртына шығарамыз, оған қысқартпаймыз")}


def t_extra_frac(rng):
    p = rng.randint(1, 6)
    r = _nz(rng, -5, 7, (p,))
    s, t = p + r, p * r
    if s == 0:
        return None
    lo, hi = sorted((p, r))
    return {"statement": f"x²/{_xm(p)} = ({_expr([(s, 'x'), (-t, '')])})/{_xm(p)}", "kind": "equation",
            "skill": "quadratic",
            "reference": [f"ОДЗ: x ≠ {_n(p)}", f"x² = {_expr([(s, 'x'), (-t, '')])}",
                          f"{_expr([(1, 'x²'), (-s, 'x'), (t, '')])} = 0", f"{_xm(lo)}{_xm(hi)} = 0",
                          f"x = {_n(lo)} или x = {_n(hi)}", f"x = {_n(p)} не подходит по ОДЗ"],
            "answer": _n(r), "key_line": 5,
            "note": _note(f"сверяем корни с ОДЗ: при x = {_n(p)} знаменатель равен нулю",
                          f"түбірлерді ММЖ-мен салыстырамыз: x = {_n(p)} болғанда бөлім нөлге тең")}


NOTE_MOVE = _note("переносим слагаемые через «=»: каждое меняет знак",
                  "қосылғыштарды «=» арқылы көшіреміз: әрқайсысының таңбасы өзгереді")


def t_sign_lin(rng):
    a, c = rng.sample(range(2, 10), 2)
    x0 = _nz(rng, -6, 6)
    b = _nz(rng, -9, 9)
    d = (a - c) * x0 + b
    if d == 0 or abs(d) > 40:
        return None
    return {"statement": f"{_expr([(a, 'x'), (b, '')])} = {_expr([(c, 'x'), (d, '')])}", "kind": "equation",
            "skill": "linear",
            "reference": [f"{a}x − {c}x = {_sum([d, -b])}", f"{_expr([(a - c, 'x')])} = {_n(d - b)}", f"x = {_n(x0)}"],
            "answer": _n(x0), "key_line": 0, "note": NOTE_MOVE}


def t_sign_brackets(rng):
    a = rng.randint(2, 6)
    c = rng.choice([v for v in range(1, 10) if v != a])
    p, b = rng.randint(1, 6), rng.randint(1, 9)
    x0 = _nz(rng, -6, 6)
    d = c * x0 - a * x0 + a * p - b          # a(x − p) + b = cx − d
    if d == 0 or abs(d) > 40:
        return None
    return {"statement": f"{a}{_xm(p)} + {b} = {_expr([(c, 'x'), (-d, '')])}", "kind": "equation", "skill": "linear",
            "reference": [f"{a}x − {a * p} + {b} = {_expr([(c, 'x'), (-d, '')])}",
                          f"{_expr([(a, 'x'), (-c, 'x')])} = {_sum([-d, a * p, -b])}",
                          f"{_expr([(a - c, 'x')])} = {_n((a - c) * x0)}", f"x = {_n(x0)}"],
            "answer": _n(x0), "key_line": 0,
            "note": _note(f"раскрываем скобки: {a}·(−{p}) = −{a * p}, знак минуса сохраняется",
                          f"жақшаны ашамыз: {a}·(−{p}) = −{a * p}, минус таңбасы сақталады")}


def _var(rng):
    return rng.choice(["a", "b", "m", "y"])


NOTE_SQ = _note("(u ± p)² = u² ± 2pu + p²: удвоенное произведение не теряем",
                "(u ± p)² = u² ± 2pu + p²: екі еселенген көбейтіндіні жоғалтпаймыз")


def t_fsu_diff(rng):
    v, p = _var(rng), rng.randint(2, 7)
    ans = _expr([(2 * p, v), (2 * p * p, "")])
    return {"statement": f"({v} + {p})² − ({v} − {p})({v} + {p})", "kind": "expression", "skill": "simplify",
            "reference": [f"{v}² + {2 * p}{v} + {p * p} − ({v}² − {p * p})",
                          f"{v}² + {2 * p}{v} + {p * p} − {v}² + {p * p}", ans],
            "answer": ans, "key_line": 0,
            "note": _note(f"({v} + {p})² = {v}² + {2 * p}{v} + {p * p}, а ({v} − {p})({v} + {p}) = {v}² − {p * p}",
                          f"({v} + {p})² = {v}² + {2 * p}{v} + {p * p}, ал ({v} − {p})({v} + {p}) = {v}² − {p * p}")}


def t_fsu_sq(rng):
    v, p = _var(rng), rng.randint(2, 7)
    s = rng.choice([1, -1])     # (v − p)² + 2pv  или  (v + p)² − 2pv
    ans = f"{v}² + {p * p}"
    return {"statement": f"({v} {'−' if s > 0 else '+'} {p})² {'+' if s > 0 else '−'} {2 * p}{v}", "kind": "expression",
            "skill": "simplify",
            "reference": [f"{_expr([(1, v + '²'), (-2 * p * s, v), (p * p, '')])} {'+' if s > 0 else '−'} {2 * p}{v}", ans],
            "answer": ans, "key_line": 0, "note": NOTE_SQ}


def t_calc_quad(rng):
    from .seed import _quad_statement, _quad_variants
    r1, r2 = sorted(rng.sample([v for v in range(-6, 9) if v != 0], 2))
    b, c = -(r1 + r2), r1 * r2
    if b == 0:
        return None
    ref = [l for l in _quad_variants(b, c, r1, r2)["disc"] if not l.startswith("Ответ")]
    return {"statement": _quad_statement(b, c), "kind": "equation", "skill": "quadratic", "reference": ref,
            "answer": _roots_text([r1, r2]), "key_line": 0,
            "note": _note("D = b² − 4ac: отрицательное b берём в скобки, считаем по шагам",
                          "D = b² − 4ac: теріс b-ні жақшаға аламыз, қадаммен есептейміз")}


NOTE_STEPS = _note("каждую скобку раскрываем в отдельной строке — так виден каждый знак",
                   "әр жақшаны жеке жолда ашамыз — әр таңба көрініп тұрады")


def t_steps_two(rng):
    a = rng.randint(3, 7)
    b = rng.randint(1, a - 1)
    p, q = rng.randint(1, 6), rng.randint(1, 6)
    x0 = _nz(rng, -5, 8)
    c = (a - b) * x0 - a * p - b * q        # a(x − p) − b(x + q) = c
    if c == 0 or abs(c) > 60:
        return None
    k = a - b
    ref = [f"{a}x − {a * p} − {_expr([(b, 'x')])} − {b * q} = {_n(c)}",
           f"{a}x − {_expr([(b, 'x')])} = {_sum([c, a * p, b * q])}"]
    if k != 1:
        ref.append(f"{k}x = {_n(k * x0)}")
    ref.append(f"x = {_n(x0)}")
    bb = "" if b == 1 else str(b)
    return {"statement": f"{a}{_xm(p)} − {bb}{_xm(-q)} = {_n(c)}", "kind": "equation", "skill": "linear",
            "reference": ref, "answer": _n(x0), "key_line": 0, "note": NOTE_STEPS}


def t_steps_both(rng):
    a, b = rng.sample(range(2, 8), 2)
    p, q = rng.randint(1, 6), rng.randint(1, 6)
    x0 = _nz(rng, -5, 8)
    c = (a - b) * x0 + a * p + b * q        # a(x + p) = b(x − q) + c
    if c == 0 or abs(c) > 60:
        return None
    k = a - b
    ref = [f"{a}x + {a * p} = {b}x − {b * q} {'+' if c > 0 else '−'} {abs(c)}",
           f"{a}x − {b}x = {_sum([-b * q, c, -a * p])}"]
    if abs(k) != 1:
        ref.append(f"{_expr([(k, 'x')])} = {_n(k * x0)}")
    elif k == -1:
        ref.append(f"−x = {_n(-x0)}")
    ref.append(f"x = {_n(x0)}")
    return {"statement": f"{a}{_xm(-p)} = {b}{_xm(q)} {'+' if c > 0 else '−'} {abs(c)}", "kind": "equation",
            "skill": "linear", "reference": ref, "answer": _n(x0), "key_line": 0, "note": NOTE_STEPS}


def _with_check(base):
    def make(rng):
        from .seed import _check_line
        it = base(rng)
        if not it:
            return None
        roots = [int(v.replace("−", "-")) for v in it["answer"].split("; ")]
        it = dict(it)
        it["reference"] = it["reference"] + [_check_line(it["statement"], r) for r in roots]
        it["requirement"] = {"check": True}
        it["key_line"] = len(it["reference"]) - 1
        it["note"] = _note("подставляем ответ в исходное уравнение: обе части равны",
                           "жауапты бастапқы теңдеуге қоямыз: екі жағы тең")
        return it
    return make


# tag → [(id шаблона, функция)]. Навык каждой задачи — внутри шаблона.
FAMILIES = {
    "lost_root": [("lost_kx", t_lost_kx), ("lost_axbx", t_lost_axbx), ("lost_common", t_lost_common)],
    "extra_root": [("extra_frac", t_extra_frac)],
    "sign": [("sign_lin", t_sign_lin), ("sign_brackets", t_sign_brackets)],
    "fsu": [("fsu_diff", t_fsu_diff), ("fsu_sq", t_fsu_sq)],
    "calc": [("calc_quad", t_calc_quad)],
    "skip_steps": [("steps_two", t_steps_two), ("steps_both", t_steps_both)],
    "check_done": [("check_lin", _with_check(t_sign_lin)), ("check_quad", _with_check(t_calc_quad))],
}
FLIP = {"<": ">", ">": "<", "≤": "≥", "≥": "≤"}


def t_ineq_flip(rng):
    """Новые темы (агент Бета): −ax + b < c → делим на −a и меняем знак неравенства."""
    a, x0, b = rng.randint(2, 6), _nz(rng, -5, 5), _nz(rng, -9, 9)
    sign = rng.choice(list(FLIP))
    c = -a * x0 + b
    skill = next((sk for sk in T.SKILLS if "ineq" in sk), "linear")
    return {"statement": f"{_expr([(-a, 'x'), (b, '')])} {sign} {_n(c)}", "kind": "inequality", "skill": skill,
            "reference": [f"−{a}x {sign} {_sum([c, -b])}", f"−{a}x {sign} {_n(c - b)}", f"x {FLIP[sign]} {_n(x0)}"],
            "answer": f"x {FLIP[sign]} {_n(x0)}", "key_line": 2,
            "note": _note(f"делим на −{a}: число отрицательное, знак неравенства меняется",
                          f"−{a}-ге бөлеміз: сан теріс, теңсіздік таңбасы ауысады")}


def register_optional_topics() -> None:
    """Шаблоны для тегов других агентов — только если тег есть в словаре. Задача попадёт в набор,
    лишь когда её эталон пройдёт check_problem (то есть когда проверка научится этому виду задач)."""
    for tag in ("ineq_flip", "boundary"):
        if tag in T.TAGS and tag not in FAMILIES:
            FAMILIES[tag] = [(tag, t_ineq_flip)]


# если у тега нет своих шаблонов (новые темы) — берём задачи того же навыка
DEFAULT_FOR_SKILL = {"linear": "sign", "quadratic": "calc", "simplify": "fsu"}
MIXED = ("lost_root", "sign", "fsu", "calc", "extra_root")
register_optional_topics()


def _requirement(tag: str, item: dict) -> dict:
    req = dict(item.get("requirement") or {})
    if tag == "skip_steps":
        req["min_lines"] = math.ceil(len(item["reference"]) / 2)
    return req


def self_check(item: dict) -> bool:
    """Эталон задачи проходит ту же проверку, что и домашние работы."""
    try:
        res = check_problem(item["kind"], item["statement"], item["reference"], len(item["reference"]), item["answer"])
    except Exception:  # noqa: BLE001 — любая ошибка разбора = негодная задача
        return False
    if res.first_error is not None or res.correct is not True:
        return False
    return accepts(item, res)[0]


def _key(statement: str) -> str:
    try:
        return normalize(statement).text.replace(" ", "")
    except Exception:  # noqa: BLE001
        return statement.replace(" ", "")


def _templates(tag: Optional[str], skill: Optional[str]) -> list:
    if tag in FAMILIES:
        fam = FAMILIES[tag]
    elif skill in DEFAULT_FOR_SKILL:
        fam = FAMILIES[DEFAULT_FOR_SKILL[skill]]
    else:
        return []
    if skill and tag != "check_done":
        same = [(tid, fn) for tid, fn in fam if _skill_of(fn) == skill]
        if same:
            return same
    return fam


_SKILL_CACHE: dict = {}


def _skill_of(fn) -> Optional[str]:
    if fn not in _SKILL_CACHE:
        rng = random.Random(0)
        it = None
        for _ in range(20):
            it = fn(rng)
            if it:
                break
        _SKILL_CACHE[fn] = it["skill"] if it else None
    return _SKILL_CACHE[fn]


def generate(tag: Optional[str], skill: Optional[str] = None, n: int = 4, seed: Optional[int] = None,
             avoid: tuple = ()) -> list[dict]:
    """n разных задач под тег; tag=None — смешанный набор на закрепление."""
    rng = random.Random(seed)
    if tag is None:
        plan = [(t, None) for t in MIXED]
    else:
        plan = [(tag, skill)]
    seen = {_key(a) for a in avoid}
    out: list[dict] = []
    tries = 0
    while len(out) < n and tries < n * 60:
        tries += 1
        t, sk = plan[len(out) % len(plan)]
        fams = _templates(t, sk)
        if not fams:
            break
        tid, fn = rng.choice(fams)
        it = fn(rng)
        if not it:
            continue
        k = _key(it["statement"])
        if k in seen:
            continue
        it = dict(it, template=tid, tag=t)
        it["requirement"] = _requirement(t, it)
        if not self_check(it):
            continue
        seen.add(k)
        out.append(it)
    return out


def worked_example(tag: str, skill: Optional[str], avoid: str, seed: Optional[int] = None) -> Optional[dict]:
    """Разобранный пример той же семьи, но не задача ученика. Ключевой шаг — item["key_line"]."""
    known = tag in FAMILIES or skill in DEFAULT_FOR_SKILL
    items = generate(tag if known else None, skill, n=1, seed=seed, avoid=(avoid,))
    return items[0] if items else None


# ======================================================================
# 3. Проверка попытки
# ======================================================================

def accepts(item: dict, res) -> tuple[bool, Optional[str]]:
    """Засчитана ли попытка. → (да/нет, причина: error | unfinished | need_check | need_steps)"""
    if res.first_error is not None:
        return False, "error"
    if res.correct is not True:
        return False, "unfinished"
    req = item.get("requirement") or {}
    if isinstance(req, str):
        req = json.loads(req or "{}")
    if req.get("check") and "check_done" not in res.habits:
        return False, "need_check"
    if req.get("min_lines") and res.solution_lines < req["min_lines"]:
        return False, "need_steps"
    return True, None


def outcome_for(hint_level: int, solved: bool) -> str:
    """Правило исхода задачи, в которой была ошибка."""
    if not solved or hint_level >= 3:
        return "needs_example"
    if hint_level == 2:
        return "fixed_after_rule"
    return "self_fixed"


# ======================================================================
# 4. Хранение: сессии, задачи, попытки
# ======================================================================

def _now(now: Optional[str]) -> str:
    return now or db.now()


def start_session(conn, student_id: int, mode: str, target_tag: Optional[str] = None,
                  target_skill: Optional[str] = None, now: Optional[str] = None) -> int:
    ensure_schema(conn)
    cur = conn.execute("INSERT INTO practice_sessions (student_id, mode, target_tag, target_skill, started_at) "
                       "VALUES (?,?,?,?,?)", (student_id, mode, target_tag, target_skill, _now(now)))
    conn.commit()
    return cur.lastrowid


def finish_session(conn, session_id: int, now: Optional[str] = None) -> None:
    conn.execute("UPDATE practice_sessions SET finished_at=? WHERE id=? AND finished_at IS NULL",
                 (_now(now), session_id))
    conn.commit()


def add_item(conn, session_id: int, item: dict, source_submission_id: Optional[int] = None,
             source_problem_id: Optional[int] = None, review_id: Optional[int] = None) -> int:
    cur = conn.execute(
        """INSERT INTO practice_items (session_id, kind, skill, statement, reference, answer, source_submission_id,
               source_problem_id, target_tag, requirement, template, review_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (session_id, item["kind"], item["skill"], item["statement"],
         json.dumps(item["reference"], ensure_ascii=False), item.get("answer"), source_submission_id,
         source_problem_id, item.get("tag"), json.dumps(item.get("requirement") or {}), item.get("template"), review_id))
    conn.commit()
    return cur.lastrowid


def get_item(conn, item_id: int) -> dict:
    ensure_schema(conn)
    r = db.q1(conn, """SELECT i.*, s.student_id, s.mode FROM practice_items i
                       JOIN practice_sessions s ON s.id = i.session_id WHERE i.id=?""", (item_id,))
    d = dict(r)
    d["reference"] = json.loads(d["reference"])
    d["requirement"] = json.loads(d["requirement"] or "{}")
    d["tag"] = d["target_tag"]
    return d


def attempts_of(conn, item_id: int) -> list[dict]:
    return [dict(r, lines=json.loads(r["lines"])) for r in
            db.q(conn, "SELECT * FROM practice_attempts WHERE item_id=? ORDER BY attempt_no", (item_id,))]


def _add_attempt(conn, item_id, attempt_no, lines, fe, correct, hint_level, now):
    conn.execute("""INSERT INTO practice_attempts (item_id, attempt_no, lines, first_error_tag, first_error_line,
                        correct, hint_level, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                 (item_id, attempt_no, json.dumps(lines, ensure_ascii=False), fe["tag"] if fe else None,
                  fe["line"] if fe else None, None if correct is None else int(correct), hint_level, _now(now)))


def check_item(item: dict, lines: list):
    return check_problem(item["kind"], item["statement"], lines, len(item["reference"]), item.get("answer"))


def submit_attempt(conn, item_id: int, lines: list, hint_level: int, now: Optional[str] = None) -> dict:
    """Проверить попытку и записать её. Если задача решена после ошибки — исход в журнал."""
    item = get_item(conn, item_id)
    lines = [l for l in lines]
    res = check_item(item, lines)
    ok, reason = accepts(item, res)
    prev = attempts_of(conn, item_id)
    no = (max(a["attempt_no"] for a in prev) + 1) if prev else 1
    _add_attempt(conn, item_id, no, lines, res.first_error, ok, hint_level, now)
    outcome = None
    if ok and not item["solved"]:
        conn.execute("UPDATE practice_items SET solved=1 WHERE id=?", (item_id,))
        if item["review_id"]:
            conn.execute("UPDATE practice_reviews SET done_at=? WHERE id=?", (_now(now), item["review_id"]))
        err = next((a for a in prev if a["first_error_tag"]), None)
        if err and not item["outcome"]:
            outcome = outcome_for(hint_level, True)
            _record_outcome(conn, item, outcome, err, hint_level, True, now)
    conn.commit()
    return {"result": res, "ok": ok, "reason": reason, "attempt_no": no, "outcome": outcome}


def abandon_item(conn, item_id: int, hint_level: int, now: Optional[str] = None) -> Optional[str]:
    """Ученик перешёл дальше, не решив задачу. Если ошибка была — исход needs_example."""
    item = get_item(conn, item_id)
    if item["solved"] or item["outcome"]:
        return None
    err = next((a for a in attempts_of(conn, item_id) if a["first_error_tag"]), None)
    if not err:
        return None
    _record_outcome(conn, item, "needs_example", err, hint_level, False, now)
    conn.commit()
    return "needs_example"


def _record_outcome(conn, item, outcome, err, hint_level, solved, now):
    ev = f"{item['statement']}: ошибка «{T.name(err['first_error_tag'], 'ru')}» в строке {err['first_error_line']}"
    if not solved:
        ev += "; задача не решена"
    db.add_observation(conn, item["student_id"], item["source_submission_id"], item["source_problem_id"],
                       err["first_error_line"], "habit", outcome, item["skill"], ev,
                       {"hint_level": hint_level, "error_tag": err["first_error_tag"], "item_id": item["id"],
                        "solved": solved},
                       source="practice", confidence=CONFIDENCE, created_at=_now(now))
    conn.execute("UPDATE practice_items SET outcome=? WHERE id=?", (outcome, item["id"]))
    if outcome == "needs_example":
        base = datetime.strptime(_now(now), "%Y-%m-%d %H:%M:%S")
        for d in REVIEW_DAYS:
            conn.execute("INSERT INTO practice_reviews (student_id, item_id, due_at) VALUES (?,?,?)",
                         (item["student_id"], item["id"], (base + timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")))


# ======================================================================
# 5. Режим «разобрать свою ошибку»
# ======================================================================

def own_errors(conn, student_id: int) -> list[dict]:
    """Сохранённые работы ученика с ошибкой, свежие сверху."""
    rows = db.q(conn, """
        SELECT at.submission_id, at.problem_id, at.first_error_line, s.submitted_at, s.source,
               a.number AS work_no, p.idx, p.statement, p.skill, p.kind,
               (SELECT o.tag FROM observations o WHERE o.submission_id = at.submission_id
                   AND o.problem_id = at.problem_id AND o.kind = 'error' AND o.source = 'auto' LIMIT 1) AS tag
        FROM attempts at JOIN submissions s ON s.id = at.submission_id
        JOIN assignments a ON a.id = s.assignment_id JOIN problems p ON p.id = at.problem_id
        WHERE s.student_id=? AND at.first_error_line IS NOT NULL
        ORDER BY s.submitted_at DESC, p.idx""", (student_id,))
    return [dict(r) for r in rows]


def original_lines(conn, submission_id: int, problem_id: int) -> list[str]:
    """Строки сохранённой работы с исходной нумерацией (пустые строки — на своих местах)."""
    rows = db.q(conn, "SELECT line_no, raw_text FROM steps WHERE submission_id=? AND problem_id=? ORDER BY line_no",
                (submission_id, problem_id))
    if not rows:
        return []
    out = [""] * max(r["line_no"] for r in rows)
    for r in rows:
        out[r["line_no"] - 1] = r["raw_text"]
    return out


def original_check(conn, submission_id: int, problem_id: int):
    """Повторная проверка сохранённой работы — даёт first_error с подробностями для наводящего вопроса."""
    p = db.q1(conn, "SELECT * FROM problems WHERE id=?", (problem_id,))
    return check_problem(p["kind"], p["statement"], original_lines(conn, submission_id, problem_id),
                         len(json.loads(p["reference"])), p["answer"])


def prefill(lines: list, first_error_line: Optional[int]) -> list[str]:
    """Строки до ошибочной — с них ученик продолжает решение."""
    if not first_error_line:
        return [l for l in lines if l.strip()]
    return [l for l in lines[: first_error_line - 1] if l.strip()]


def start_fix_own(conn, student_id: int, submission_id: int, problem_id: int, now: Optional[str] = None) -> int:
    """Сессия fix_own: задача из работы + попытка №0 — сама работа с её ошибкой."""
    ensure_schema(conn)
    p = dict(db.q1(conn, "SELECT * FROM problems WHERE id=?", (problem_id,)))
    res = original_check(conn, submission_id, problem_id)
    fe = res.first_error
    tag = fe["tag"] if fe else None
    sid = start_session(conn, student_id, "fix_own", tag, p["skill"], now)
    item = {"kind": p["kind"], "skill": p["skill"], "statement": p["statement"],
            "reference": json.loads(p["reference"]), "answer": p["answer"], "tag": tag, "template": None}
    item_id = add_item(conn, sid, item, submission_id, problem_id)
    _add_attempt(conn, item_id, 0, original_lines(conn, submission_id, problem_id), fe, False, 0, now)
    conn.commit()
    return item_id


# ======================================================================
# 6. Режим «задания под слабое место»
# ======================================================================

TARGET_TEXT = {
    "mixed": {"ru": "Всё хорошо — вот задачи на закрепление", "kk": "Бәрі жақсы — міне, бекітуге арналған есептер"},
    "low_data": {"ru": "мало данных, но стоит потренироваться", "kk": "дерек аз, бірақ жаттығып алған жөн"},
}


def usable(tag: str, skill: Optional[str] = None) -> bool:
    """Есть ли у тега шаблоны, эталон которых проходит проверку."""
    key = (tag, skill)
    if key not in _USABLE:
        _USABLE[key] = tag in FAMILIES and bool(generate(tag, skill, n=1, seed=0))
    return _USABLE[key]


_USABLE: dict = {}


def targets_for(conn, student_id: int, lang: str = "ru") -> list[dict]:
    """Что тренировать, по приоритету: рекомендации портрета → «мало данных» → смешанный набор."""
    p = portrait.build(conn, student_id, lang)
    out, seen = [], set()
    for r in p["recs"]:
        head = r["key"].split(":")[0]
        if r["tag"] not in seen and usable(r["tag"], head if head in T.SKILLS else None):
            out.append({"tag": r["tag"], "skill": head if head in T.SKILLS else None, "mode": "weak",
                        "title": r["title"], "why": r["why"]})
            seen.add(r["tag"])
    for e in p["errors"]:
        if e["low_data"] and e["tag"] not in seen and usable(e["tag"], e["skill"]):
            out.append({"tag": e["tag"], "skill": e["skill"], "mode": "low_data",
                        "title": f"{T.name(e['tag'], lang)} — {T.skill_name(e['skill'], lang).lower()}",
                        "why": f"{e['count']}/{e['total']} · {TARGET_TEXT['low_data'][lang]}"})
            seen.add(e["tag"])
    if not out:
        out.append({"tag": None, "skill": None, "mode": "mixed", "title": TARGET_TEXT["mixed"][lang], "why": ""})
    return out


def start_weak_spot(conn, student_id: int, target: dict, n: int = 4, seed: Optional[int] = None,
                    now: Optional[str] = None) -> tuple[int, list[int]]:
    n = max(3, min(5, n))
    hw = db.q(conn, "SELECT DISTINCT statement FROM problems")  # задачи ДЗ (и будущих тоже) не повторяем
    items = generate(target.get("tag"), target.get("skill"), n=n, seed=seed, avoid=tuple(r["statement"] for r in hw))
    sid = start_session(conn, student_id, "weak_spot", target.get("tag"), target.get("skill"), now)
    return sid, [add_item(conn, sid, it) for it in items]


# ======================================================================
# 7. Интервальное повторение
# ======================================================================

def reviews(conn, student_id: int, now: Optional[str] = None) -> dict:
    """{"due": [...], "later": [...]} — задачи, решённые только с примером."""
    ensure_schema(conn)
    rows = [dict(r) for r in db.q(conn, """
        SELECT r.*, i.statement, i.target_tag, i.skill FROM practice_reviews r
        JOIN practice_items i ON i.id = r.item_id
        WHERE r.student_id=? AND r.done_at IS NULL ORDER BY r.due_at""", (student_id,))]
    t = _now(now)
    return {"due": [r for r in rows if r["due_at"] <= t], "later": [r for r in rows if r["due_at"] > t]}


def start_review(conn, student_id: int, review_ids: list, now: Optional[str] = None) -> tuple[int, list[int]]:
    sid = start_session(conn, student_id, "review", now=now)
    ids = []
    for rid in review_ids:
        r = db.q1(conn, "SELECT * FROM practice_reviews WHERE id=?", (rid,))
        src = get_item(conn, r["item_id"])
        ids.append(add_item(conn, sid, src, src["source_submission_id"], src["source_problem_id"], review_id=rid))
    return sid, ids


# ======================================================================
# 8. Сводка для учителя
# ======================================================================

VERDICTS = {
    "self_check": {"ru": "Исправляет сам(а) по вопросу: правило знает, но не применяет — поможет привычка самопроверки.",
                   "kk": "Сұрақ бойынша өзі түзетеді: ережені біледі, бірақ қолданбайды — өзін-өзі тексеру әдеті көмектеседі."},
    "needs_lesson": {"ru": "Нужен разбор темы: правило пока не сложилось.",
                     "kk": "Тақырыпты талдау керек: ереже әлі қалыптаспаған."},
    "low_data": {"ru": "Мало данных для вывода: нужно не меньше 3 задач с исправленной ошибкой.",
                 "kk": "Қорытынды үшін дерек аз: қатесі түзетілген кемінде 3 есеп керек."},
    "mixed": {"ru": "Картина пока неоднозначная — мало данных для вывода.",
              "kk": "Көрініс әлі біржақты емес — қорытынды үшін дерек аз."},
}


def verdict(counts: dict) -> str:
    n = sum(counts.get(k, 0) for k in OUTCOMES)
    if n < MIN_ITEMS:
        return "low_data"
    if counts.get("self_fixed", 0) / n >= SELF_SHARE:
        return "self_check"
    if counts.get("needs_example", 0) / n >= EXAMPLE_SHARE:
        return "needs_lesson"
    return "mixed"


def summary(conn, student_id: int) -> dict:
    """Итоги тренажёра по целевым тегам. Исходы берутся из журнала (source='practice')."""
    ensure_schema(conn)
    items = [dict(r) for r in db.q(conn, """
        SELECT i.id, i.target_tag, i.solved, i.statement,
               (SELECT COUNT(*) FROM practice_attempts a WHERE a.item_id = i.id AND a.attempt_no >= 1) AS tries,
               (SELECT COUNT(*) FROM practice_attempts a WHERE a.item_id = i.id AND a.first_error_tag IS NOT NULL) AS errs
        FROM practice_items i JOIN practice_sessions s ON s.id = i.session_id
        WHERE s.student_id=?""", (student_id,))]
    tried = [i for i in items if i["tries"]]
    target_of = {i["id"]: i["target_tag"] for i in items}
    obs = db.q(conn, f"""SELECT * FROM observations WHERE student_id=? AND source='practice'
                         AND tag IN ({','.join('?' * len(OUTCOMES))}) ORDER BY created_at DESC""",
               (student_id, *OUTCOMES))
    total = Counter()
    by_tag = defaultdict(Counter)
    refs = []
    for o in obs:
        d = json.loads(o["detail"] or "{}")
        tg = target_of.get(d.get("item_id")) or d.get("error_tag") or "other"
        total[o["tag"]] += 1
        by_tag[tg][o["tag"]] += 1
        refs.append({"tag": o["tag"], "evidence": o["evidence"], "hint_level": d.get("hint_level"),
                     "created_at": o["created_at"]})
    tags_rows = []
    for tg in sorted({i["target_tag"] or "other" for i in tried} | set(by_tag), key=str):
        its = [i for i in tried if (i["target_tag"] or "other") == tg]
        c = by_tag.get(tg, Counter())
        tags_rows.append({"tag": tg, "tried": len(its), "solved": sum(1 for i in its if i["solved"]),
                          "first_try": sum(1 for i in its if i["solved"] and not i["errs"]),
                          "outcomes": {k: c.get(k, 0) for k in OUTCOMES}, "verdict": verdict(c)})
    return {
        "has_data": bool(tried or obs),
        "tried": len(tried), "solved": sum(1 for i in tried if i["solved"]),
        "first_try": sum(1 for i in tried if i["solved"] and not i["errs"]),
        "outcomes": {k: total.get(k, 0) for k in OUTCOMES}, "n_outcomes": sum(total.values()),
        "verdict": verdict(total), "by_tag": tags_rows, "refs": refs,
    }


def class_summary(conn, student_rows) -> dict:
    """Сводка по классу: {verdict: [(id, alias, summary)]} — только ученики с данными тренажёра."""
    out = defaultdict(list)
    for s in student_rows:
        sm = summary(conn, s["id"])
        if sm["has_data"]:
            out[sm["verdict"]].append((s["id"], s["alias"], sm))
    return dict(out)

"""Динамика по журналу: последовательности по работам, интервал, тренд, BKT.

Всё считается обычным кодом из тех же данных, что и портрет:
- работа «с навыком» — та, где есть задача этого навыка (как в portrait.build);
- наблюдения берутся через portrait._obs, поэтому отметки учителя
  (если они фильтруют журнал) учитываются автоматически.

Последовательность — список работ от старых к новым:
{"sub_id", "work_no", "submitted_at", "error": 0 | 1} (для привычек — "hit").
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import NamedTuple, Optional

from . import db

# ---------------------------------------------------------------- пороги

WILSON_Z = 1.2816          # 80-процентный двусторонний интервал
MIN_WORKS_INTERVAL = 3     # меньше — «мало данных»
MIN_WORKS_TREND = 4        # меньше — тренда нет
SMALL_SEQ_MAX = 5          # «короткая» последовательность: 4–5 работ
MIN_DIFF_SMALL = 1         # короткая: разница хотя бы в 1 случай…
MIN_SHARE_DIFF_SMALL = 0.30  # …и доля изменилась не меньше чем на 30 п. п.
MIN_DIFF = 2               # длинная: разница хотя бы в 2 случая

BKT_MASTERED = 0.6         # PORTRAIT_MODEL=bkt: слабое место, если P(освоено) ≤ этого
BKT_MIN_OBS = 10           # меньше наблюдений по классу — параметры по умолчанию


def _p():
    from . import portrait  # внутри функции: portrait импортирует этот модуль в class_map/build
    return portrait


# ---------------------------------------------------------------- последовательности

def _auto_errors(conn, sid: int) -> list:
    return [o for o in _p()._obs(conn, sid) if o["source"] == "auto" and o["kind"] == "error"]


def _seq(works_old_first: list, hit: set, key: str) -> list:
    return [{"sub_id": w["id"], "work_no": w["number"], "submitted_at": w["submitted_at"],
             key: int(w["id"] in hit)} for w in works_old_first]


def sequence(conn, student_id: int, skill: str, tag: Optional[str] = None) -> list:
    """Работы ученика с задачами навыка, от старых к новым; error = 1, если в работе есть
    ошибка tag по этому навыку. tag=None — любая ошибка навыка (для карты класса)."""
    p = _p()
    works = list(reversed(p._works(conn, student_id)))
    skill_subs = p._skills_per_work(conn, student_id).get(skill, set())
    hit = {o["submission_id"] for o in _auto_errors(conn, student_id)
           if o["skill"] == skill and (tag is None or o["tag"] == tag)}
    return _seq([w for w in works if w["id"] in skill_subs], hit, "error")


def habit_sequence(conn, student_id: int, habit: str) -> list:
    """Все работы ученика, от старых к новым; hit = 1, если в работе замечена привычка."""
    p = _p()
    works = list(reversed(p._works(conn, student_id)))
    hit = {o["submission_id"] for o in p._obs(conn, student_id)
           if o["source"] == "auto" and o["kind"] == "habit" and o["tag"] == habit}
    return _seq(works, hit, "hit")


def values(seq) -> list:
    """Последовательность → список 0/1 (принимает и готовый список чисел)."""
    out = []
    for x in seq:
        if isinstance(x, dict):
            x = x["error"] if "error" in x else x["hit"]
        out.append(int(x))
    return out


def cumulative(seq) -> list:
    """Накопленная доля после каждой работы."""
    v = values(seq)
    return [sum(v[:i + 1]) / (i + 1) for i in range(len(v))]


# ---------------------------------------------------------------- интервал

def wilson(e: int, n: int, z: float = WILSON_Z) -> Optional[tuple]:
    """Интервал Уилсона для доли e/n (по умолчанию 80 %). При n < 3 — None («мало данных»)."""
    if n < MIN_WORKS_INTERVAL:
        return None
    p = e / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)


# ---------------------------------------------------------------- тренд

def trend(seq) -> Optional[str]:
    """"down" — во второй половине работ случаев меньше, "up" — больше, "flat" — без заметной
    разницы, None — меньше 4 работ. При нечётной длине средняя работа не участвует."""
    v = values(seq)
    n = len(v)
    if n < MIN_WORKS_TREND:
        return None
    half = n // 2
    first, second = v[:half], v[n - half:]
    diff = sum(first) - sum(second)  # > 0 — стало меньше
    if n <= SMALL_SEQ_MAX:
        share_diff = diff / half
        if diff >= MIN_DIFF_SMALL and share_diff >= MIN_SHARE_DIFF_SMALL - 1e-9:
            return "down"
        if -diff >= MIN_DIFF_SMALL and -share_diff >= MIN_SHARE_DIFF_SMALL - 1e-9:
            return "up"
        return "flat"
    if diff >= MIN_DIFF:
        return "down"
    if -diff >= MIN_DIFF:
        return "up"
    return "flat"


# ---------------------------------------------------------------- BKT

class BKTParams(NamedTuple):
    """Bayesian Knowledge Tracing (Corbett & Anderson, 1994).
    L0 — освоен до первой работы, T — освоение за одну работу,
    S — ошибка при освоенном навыке (slip), G — верно без освоения (guess)."""
    L0: float
    T: float
    S: float
    G: float


BKT_DEFAULT = BKTParams(0.3, 0.15, 0.1, 0.2)
GRID = {
    "L0": [round(0.1 + 0.1 * i, 2) for i in range(9)],    # 0.1 … 0.9
    "T": [round(0.05 + 0.05 * i, 2) for i in range(8)],   # 0.05 … 0.4
    "S": [round(0.05 + 0.05 * i, 2) for i in range(6)],   # 0.05 … 0.3
    "G": [round(0.1 + 0.05 * i, 2) for i in range(7)],    # 0.1 … 0.4
}


def bkt_step(L: float, correct: bool, prm: BKTParams) -> float:
    """Одна работа: апостериорная P(освоен) по наблюдению, затем переход с p(T)."""
    if correct:
        post = L * (1 - prm.S) / (L * (1 - prm.S) + (1 - L) * prm.G)
    else:
        post = L * prm.S / (L * prm.S + (1 - L) * (1 - prm.G))
    return post + (1 - post) * prm.T


def bkt_trajectory(seq, params: BKTParams = BKT_DEFAULT) -> list:
    """P(навык освоен) после каждой работы. Работа «верна», если в ней нет этой ошибки."""
    prm = BKTParams(*params)
    L = prm.L0
    out = []
    for x in values(seq):
        L = bkt_step(L, not x, prm)
        out.append(L)
    return out


def fit_bkt(seqs) -> BKTParams:
    """Параметры BKT по данным класса: перебор по сетке, максимум правдоподобия.
    seqs — последовательности 0/1 (1 = ошибка). Меньше 10 наблюдений — BKT_DEFAULT."""
    return _fit_cached(tuple(tuple(values(s)) for s in seqs if len(s)))


@lru_cache(maxsize=256)
def _fit_cached(seqs: tuple) -> BKTParams:
    import numpy as np

    if sum(len(s) for s in seqs) < BKT_MIN_OBS:
        return BKT_DEFAULT
    L0, T, S, G = (a.ravel() for a in np.meshgrid(GRID["L0"], GRID["T"], GRID["S"], GRID["G"], indexing="ij"))
    ll = np.zeros_like(L0)
    for s in seqs:
        L = L0.copy()
        for err in s:
            p_ok = L * (1 - S) + (1 - L) * G
            if err:
                ll += np.log(1 - p_ok)
                post = L * S / (1 - p_ok)
            else:
                ll += np.log(p_ok)
                post = L * (1 - S) / p_ok
            L = post + (1 - post) * T
    i = int(np.argmax(ll))  # при равенстве — первая точка сетки: результат воспроизводим
    return BKTParams(float(L0[i]), float(T[i]), float(S[i]), float(G[i]))


def class_sequences(conn, skill: str, tag: str) -> list:
    """Последовательности 0/1 по паре (навык, тег) для всех учеников."""
    out = []
    for s in db.q(conn, "SELECT id FROM students ORDER BY id"):
        seq = values(sequence(conn, s["id"], skill, tag))
        if seq:
            out.append(seq)
    return out


def fit_for(conn, skill: str, tag: str) -> BKTParams:
    return fit_bkt(class_sequences(conn, skill, tag))


def mastery(conn, student_id: int, skill: str, tag: str, params: Optional[BKTParams] = None) -> dict:
    """Траектория BKT ученика по паре (навык, тег) с параметрами, подобранными по классу."""
    seq = sequence(conn, student_id, skill, tag)
    prm = params or fit_for(conn, skill, tag)
    traj = bkt_trajectory(seq, prm)
    return {"params": prm, "seq": seq, "trajectory": traj, "p": traj[-1] if traj else prm.L0}

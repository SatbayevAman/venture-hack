"""Отметки учителя на выводах журнала: «верно / неверно / другой тег».

Журнал наблюдений не меняется — отметки лежат в своей таблице и применяются
при сборке портрета (`apply`): «неверно» исключает наблюдение, «другой тег»
подменяет тег, «верно» только помечает строку. Побеждает последняя отметка.

Здесь же — оценка похожести портрета учителем (1–5 и «какие пункты неверны»).
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from . import db, tags as T

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    observation_id INTEGER NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('confirm', 'reject', 'retag')),
    new_tag TEXT,
    comment TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_obs ON reviews(observation_id);
CREATE TABLE IF NOT EXISTS portrait_ratings (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    wrong_items TEXT,              -- JSON: ключи пунктов портрета, которые учитель счёл неверными
    comment TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL
);
"""

VERDICTS = ("confirm", "reject", "retag")
DEFAULT_REVIEWER = "учитель"   # позже — из аккаунта (агент Дзета)
CHUNK = 900                     # предел параметров SQLite; до 900 наблюдений — один запрос


def ensure_schema(conn) -> None:
    # по одной инструкции через execute: executescript сделал бы COMMIT открытой транзакции
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)


# ---------------------------------------------------------------- вид тега

def obs_kind(tag: str, source: str) -> str:
    """Вид наблюдения с этим тегом — так же, как его записывает pipeline."""
    kind = T.TAGS[tag]["kind"]
    if source == "teacher" and kind in ("teacher", "method"):
        return "teacher"
    return kind


def retag_options(kind: str, source: str = "auto", exclude: Optional[str] = None) -> list[str]:
    """Теги, на которые можно заменить тег наблюдения вида kind."""
    return [code for code in T.TAGS if code != exclude and obs_kind(code, source) == kind]


# ---------------------------------------------------------------- отметки

def add(conn, observation_id: int, verdict: str, new_tag: Optional[str] = None,
        comment: Optional[str] = None, reviewer: Optional[str] = None) -> int:
    """Поставить отметку. new_tag — только для retag и только тег словаря того же вида."""
    ensure_schema(conn)
    if verdict not in VERDICTS:
        raise ValueError(f"неизвестная отметка: {verdict!r}")
    o = db.q1(conn, "SELECT id, kind, tag, source FROM observations WHERE id=?", (observation_id,))
    if o is None:
        raise ValueError(f"нет наблюдения {observation_id}")
    if verdict == "retag":
        if new_tag not in T.TAGS:
            raise ValueError(f"тега нет в словаре: {new_tag!r}")
        if obs_kind(new_tag, o["source"]) != o["kind"]:
            raise ValueError(f"тег {new_tag!r} другого вида, чем наблюдение ({o['kind']})")
        if new_tag == o["tag"]:
            raise ValueError("новый тег совпадает с исходным")
    else:
        new_tag = None
    cur = conn.execute(
        "INSERT INTO reviews (observation_id, verdict, new_tag, comment, reviewer, created_at) VALUES (?,?,?,?,?,?)",
        (observation_id, verdict, new_tag, (comment or "").strip() or None, reviewer or DEFAULT_REVIEWER, db.now()),
    )
    conn.commit()
    return cur.lastrowid


def latest(conn, ids: Iterable[int]) -> dict:
    """{observation_id: последняя отметка (dict)}; наблюдения без отметок в словарь не попадают."""
    ensure_schema(conn)
    ids = sorted({int(i) for i in ids if i is not None})
    out = {}
    for k in range(0, len(ids), CHUNK):
        part = ids[k:k + CHUNK]
        rows = db.q(conn, f"SELECT * FROM reviews WHERE observation_id IN ({','.join('?' * len(part))}) ORDER BY id",
                    part)
        for r in rows:
            out[r["observation_id"]] = dict(r)  # ORDER BY id: последняя перезаписывает прежние
    return out


def apply(conn, rows: list) -> list:
    """Применить отметки к строкам журнала (dict с полем id). Без отметок строки возвращаются как есть."""
    marks = latest(conn, (r["id"] for r in rows))
    if not marks:
        return rows
    out = []
    for r in rows:
        m = marks.get(r["id"])
        if m is None:
            out.append(r)
        elif m["verdict"] == "reject":
            continue
        elif m["verdict"] == "retag":
            out.append(dict(r, tag=m["new_tag"], orig_tag=r["tag"], reviewed="retag"))
        else:
            out.append(dict(r, reviewed="confirm"))
    return out


def for_submission(conn, submission_id: int, source: Optional[str] = None) -> list:
    """Наблюдения работы с последними отметками — для экрана проверки (ошибки и теги комментария)."""
    sql = """SELECT o.*, p.idx AS problem_idx, p.statement FROM observations o
             LEFT JOIN problems p ON p.id = o.problem_id WHERE o.submission_id=?"""
    args = [submission_id]
    if source:
        sql += " AND o.source=?"
        args.append(source)
    rows = [dict(r) for r in db.q(conn, sql + " ORDER BY p.idx, o.id", args)]
    marks = latest(conn, (r["id"] for r in rows))
    return [dict(r, review=marks.get(r["id"])) for r in rows]


def reject_errors(conn, submission_id: int, problem_ids: Iterable[int], comment: Optional[str] = None,
                  reviewer: Optional[str] = None) -> int:
    """«Это не ошибка» до записи: отклонить автоматические ошибки этих задач уже записанной работы."""
    n = 0
    for pid in problem_ids:
        for o in db.q(conn, """SELECT id FROM observations WHERE submission_id=? AND problem_id=?
                               AND kind='error' AND source='auto'""", (submission_id, pid)):
            add(conn, o["id"], "reject", comment=comment, reviewer=reviewer)
            n += 1
    return n


# ---------------------------------------------------------------- похожесть портрета

def add_rating(conn, student_id: int, score: int, wrong_items: Optional[list] = None,
               comment: Optional[str] = None, reviewer: Optional[str] = None) -> int:
    ensure_schema(conn)
    score = int(score)
    if not 1 <= score <= 5:
        raise ValueError("оценка — от 1 до 5")
    cur = conn.execute(
        "INSERT INTO portrait_ratings (student_id, score, wrong_items, comment, reviewer, created_at) VALUES (?,?,?,?,?,?)",
        (student_id, score, json.dumps(list(wrong_items or []), ensure_ascii=False),
         (comment or "").strip() or None, reviewer or DEFAULT_REVIEWER, db.now()),
    )
    conn.commit()
    return cur.lastrowid


def ratings(conn, student_id: Optional[int] = None) -> list:
    ensure_schema(conn)
    if student_id is None:
        rows = db.q(conn, "SELECT * FROM portrait_ratings ORDER BY id")
    else:
        rows = db.q(conn, "SELECT * FROM portrait_ratings WHERE student_id=? ORDER BY id", (student_id,))
    return [dict(r, wrong_items=json.loads(r["wrong_items"] or "[]")) for r in rows]


def rating_summary(conn) -> dict:
    """Среднее по последней оценке каждого ученика, число оценок и чаще всего неверные пункты."""
    rows = ratings(conn)
    last = {}
    for r in rows:
        last[r["student_id"]] = r
    wrong = {}
    for r in last.values():
        for item in r["wrong_items"]:
            wrong[item] = wrong.get(item, 0) + 1
    scores = [r["score"] for r in last.values()]
    return {
        "n": len(rows), "n_students": len(last),
        "mean": sum(scores) / len(scores) if scores else None,
        "wrong": sorted(wrong.items(), key=lambda kv: -kv[1]),
    }

"""Отметка «совет применён» и эффект до/после.

Ключ рекомендации — тот же, что в portrait.build: "<навык>:<тег>" (например,
"quadratic:lost_root") или "habit:<привычка>" ("habit:check_done").
Эффект — доля работ с ошибкой (или с привычкой) до даты применения и после неё.
Работы, сданные в день применения, считаются «после».
"""
from __future__ import annotations

from typing import Optional

from . import db, knowledge

MIN_AFTER = 3  # столько работ после совета нужно, чтобы говорить об эффекте

SCHEMA = """
CREATE TABLE IF NOT EXISTS interventions (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    rec_key TEXT NOT NULL,          -- ключ рекомендации из portrait.build
    applied_at TEXT NOT NULL,       -- дата применения, YYYY-MM-DD
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS interventions_student ON interventions(student_id, rec_key);
"""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def mark_applied(conn, student_id: int, rec_key: str, applied_at: str, note: Optional[str] = None) -> int:
    ensure_schema(conn)
    cur = conn.execute(
        "INSERT INTO interventions (student_id, rec_key, applied_at, note, created_at) VALUES (?,?,?,?,?)",
        (student_id, rec_key, str(applied_at)[:10], (note or "").strip() or None, db.now()),
    )
    conn.commit()
    return cur.lastrowid


def list_for(conn, student_id: int, rec_key: Optional[str] = None) -> list:
    """Отметки ученика, новые первыми."""
    ensure_schema(conn)
    sql = "SELECT * FROM interventions WHERE student_id=?"
    args = [student_id]
    if rec_key is not None:
        sql += " AND rec_key=?"
        args.append(rec_key)
    return [dict(r) for r in db.q(conn, sql + " ORDER BY applied_at DESC, id DESC", args)]


def latest(conn, student_id: int, rec_key: str) -> Optional[dict]:
    rows = list_for(conn, student_id, rec_key)
    return rows[0] if rows else None


def remove(conn, intervention_id: int) -> None:
    ensure_schema(conn)
    conn.execute("DELETE FROM interventions WHERE id=?", (intervention_id,))
    conn.commit()


def sequence_for(conn, student_id: int, rec_key: str) -> Optional[list]:
    """Последовательность, по которой меряется эффект; None — для ключа эффект не считается."""
    kind, _, rest = rec_key.partition(":")
    if not rest:
        return None
    if kind == "habit":
        return knowledge.habit_sequence(conn, student_id, rest)
    if kind == "method":
        return None  # «один способ для всех» — нет доли ошибок, которую можно сравнить
    return knowledge.sequence(conn, student_id, kind, rest)


def _share(e: int, n: int) -> Optional[float]:
    return e / n if n else None


def effect(conn, student_id: int, rec_key: str, applied_at: Optional[str] = None) -> Optional[dict]:
    """Доли до и после применения совета (по последней отметке, если дата не задана).
    None — совет не отмечен. {"supported": False} — для этого ключа эффект не считается."""
    if applied_at is None:
        mark = latest(conn, student_id, rec_key)
        if not mark:
            return None
        applied_at = mark["applied_at"]
    applied_at = str(applied_at)[:10]
    seq = sequence_for(conn, student_id, rec_key)
    if seq is None:
        return {"rec_key": rec_key, "applied_at": applied_at, "supported": False,
                "ready": False, "need_more": None}
    v = knowledge.values(seq)
    before = [x for s, x in zip(seq, v) if s["submitted_at"][:10] < applied_at]
    after = [x for s, x in zip(seq, v) if s["submitted_at"][:10] >= applied_at]
    e_b, n_b, e_a, n_a = sum(before), len(before), sum(after), len(after)
    ready = n_a >= MIN_AFTER
    return {
        "rec_key": rec_key, "applied_at": applied_at, "supported": True,
        "kind": "habit" if rec_key.startswith("habit:") else "error",
        "e_before": e_b, "n_before": n_b, "p_before": _share(e_b, n_b),
        "e_after": e_a, "n_after": n_a, "p_after": _share(e_a, n_a),
        "delta": (_share(e_a, n_a) - _share(e_b, n_b)) if ready and n_b else None,
        "ready": ready, "need_more": 0 if ready else MIN_AFTER - n_a,
    }

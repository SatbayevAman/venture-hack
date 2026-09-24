"""Согласие на обработку работ ученика — без Streamlit.

Живую работу нельзя записать в журнал без активного согласия. Основание:
synthetic — вымышленный ученик демо (ставится автоматически); parent_form —
бумажная форма родителя, в базе только её номер и дата, без скана.
"""
from __future__ import annotations

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS consents (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    scope TEXT NOT NULL,             -- homework_analysis
    basis TEXT NOT NULL,             -- synthetic | parent_form
    reference TEXT,                  -- номер и дата бумажной формы, без скана
    given_by INTEGER,
    given_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS consents_student ON consents(student_id);
"""

SCOPE = "homework_analysis"
BASES = ("synthetic", "parent_form")
BASIS_NAMES = {"synthetic": {"ru": "синтетический ученик", "kk": "синтетикалық оқушы"},
               "parent_form": {"ru": "бумажная форма родителя", "kk": "ата-ананың қағаз нысаны"}}
MAX_REFERENCE = 60


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def _active(conn, student_id: int, scope: str = SCOPE):
    return db.q1(conn, """SELECT * FROM consents WHERE student_id=? AND scope=? AND revoked_at IS NULL
                          ORDER BY id DESC LIMIT 1""", (student_id, scope))


def is_synthetic(conn, student_id: int) -> bool:
    """Вымышленный ученик демо: его история записана генератором (source='synthetic')."""
    return bool(db.q1(conn, "SELECT 1 FROM submissions WHERE student_id=? AND source='synthetic' LIMIT 1",
                      (student_id,)))


def ensure_synthetic(conn, student_id: int) -> None:
    """Синтетическому ученику согласие ставится автоматически при первом обращении
    (и только один раз: отозванное вручную не восстанавливается)."""
    ensure_schema(conn)
    if not is_synthetic(conn, student_id):
        return
    if db.q1(conn, "SELECT 1 FROM consents WHERE student_id=? AND scope=? AND basis='synthetic'", (student_id, SCOPE)):
        return
    conn.execute("INSERT INTO consents (student_id, scope, basis, reference, given_by, given_at) VALUES (?,?,?,?,?,?)",
                 (student_id, SCOPE, "synthetic", None, None, db.now()))
    conn.commit()


def required_ok(conn, student_id: int, scope: str = SCOPE) -> bool:
    """Есть ли активное согласие — условие записи живой работы в журнал."""
    ensure_synthetic(conn, student_id)
    return _active(conn, student_id, scope) is not None


def give(conn, student_id: int, reference: str, given_by: int | None, basis: str = "parent_form",
         scope: str = SCOPE) -> int:
    """Отметить согласие по бумажной форме. reference — только номер и дата формы."""
    ensure_schema(conn)
    if basis not in BASES:
        raise ValueError(f"unknown basis: {basis}")
    reference = (reference or "").strip()
    if basis == "parent_form" and not reference:
        raise ValueError("reference is required for parent_form")
    if len(reference) > MAX_REFERENCE:
        raise ValueError("reference is too long")
    revoke(conn, student_id, scope=scope, commit=False)  # одно активное согласие на ученика
    cid = conn.execute("INSERT INTO consents (student_id, scope, basis, reference, given_by, given_at) VALUES (?,?,?,?,?,?)",
                       (student_id, scope, basis, reference or None, given_by, db.now())).lastrowid
    conn.commit()
    return cid


def revoke(conn, student_id: int, scope: str = SCOPE, commit: bool = True) -> int:
    """Отозвать активное согласие. → сколько записей отозвано."""
    ensure_schema(conn)
    n = conn.execute("UPDATE consents SET revoked_at=? WHERE student_id=? AND scope=? AND revoked_at IS NULL",
                     (db.now(), student_id, scope)).rowcount
    if commit:
        conn.commit()
    return n


def status(conn, student_ids=None) -> list[dict]:
    """Состояние согласия по ученикам: для страницы «Управление»."""
    ensure_schema(conn)
    rows = db.q(conn, "SELECT id, alias, class_name FROM students ORDER BY class_name, id")
    out = []
    for s in rows:
        if student_ids is not None and s["id"] not in student_ids:
            continue
        ensure_synthetic(conn, s["id"])
        c = _active(conn, s["id"])
        last = c or db.q1(conn, "SELECT * FROM consents WHERE student_id=? ORDER BY id DESC LIMIT 1", (s["id"],))
        out.append({"student_id": s["id"], "alias": s["alias"], "class_name": s["class_name"],
                    "active": c is not None, "basis": last["basis"] if last else None,
                    "reference": last["reference"] if last else None,
                    "given_at": last["given_at"] if last else None,
                    "revoked_at": last["revoked_at"] if last else None})
    return out

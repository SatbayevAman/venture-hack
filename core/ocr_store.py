"""Журнал распознаваний: что прочитала модель и что учитель оставил в итоге.

Фото не хранится — только строки текста. Таблица нужна для честных цифр:
сколько строк учитель правит, сколько из них распознавание само пометило
как сомнительные (калибровка порога ocr.UNSURE на живых работах).
"""
from __future__ import annotations

import difflib
import json

from . import db
from .ocr import UNSURE, numbered

SCHEMA = """
CREATE TABLE IF NOT EXISTS ocr_lines (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL,
    problem_id INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    ocr_text TEXT NOT NULL,
    final_text TEXT,
    confidence REAL NOT NULL,
    flags TEXT,
    edited INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ocr_lines_sub ON ocr_lines(submission_id);
"""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def save(conn, submission_id: int, ocr_raw: dict, answers: dict, created_at=None) -> int:
    """Записать по строке на каждую распознанную строку. → сколько строк записано.

    line_no — номер распознанной строки в задаче (с 1). final_text — строка, которую
    учитель оставил на её месте (None, если учитель её удалил); edited = 1, если текст
    изменился. Сопоставление — по тексту с учётом порядка, как в ocr.unverified."""
    ensure_schema(conn)
    # повторная живая проверка удаляет прежнюю работу (pipeline.delete_submission), а SQLite может
    # выдать новой работе тот же id — чистим и строки этой работы, и строки удалённых работ
    conn.execute("DELETE FROM ocr_lines WHERE submission_id=? OR submission_id NOT IN (SELECT id FROM submissions)",
                 (submission_id,))
    ts = created_at or db.now()
    n = 0
    for pid, rec in (ocr_raw or {}).items():
        if not isinstance(pid, int) or pid <= 0:
            continue  # строки без задачи (ключ 0) в работу не вошли
        rec = [r for r in rec if str(r.get("text", "")).strip()]
        ans = [str(t).strip() for _, t in numbered(answers.get(pid) or [])]
        final: dict = {}
        sm = difflib.SequenceMatcher(a=[str(r["text"]).strip() for r in rec], b=ans, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            for k in range(i2 - i1):
                if op == "equal" or (op == "replace" and j1 + k < j2):
                    final[i1 + k] = ans[j1 + k]
        for k, r in enumerate(rec):
            ft = final.get(k)
            conn.execute(
                """INSERT INTO ocr_lines (submission_id, problem_id, line_no, ocr_text, final_text, confidence,
                                          flags, edited, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (submission_id, pid, k + 1, str(r["text"]), ft, float(r.get("confidence", 1.0)),
                 json.dumps(r.get("flags") or [], ensure_ascii=False),
                 int(ft is None or ft != str(r["text"]).strip()), ts),
            )
            n += 1
    conn.commit()
    return n


def stats(conn, submission_id=None) -> dict:
    """Доли по журналу распознаваний (или по одной работе):
    total, edited_share, unsure_share, edited_among_unsure, unsure_among_edited."""
    ensure_schema(conn)
    sql, args = "SELECT confidence, edited FROM ocr_lines", ()
    if submission_id is not None:
        sql, args = sql + " WHERE submission_id=?", (submission_id,)
    rows = db.q(conn, sql, args)
    total = len(rows)
    edited = sum(r["edited"] for r in rows)
    unsure = [r for r in rows if r["confidence"] < UNSURE]
    edited_unsure = sum(r["edited"] for r in unsure)
    share = lambda a, b: a / b if b else 0.0  # noqa: E731
    return {
        "total": total,
        "edited": edited,
        "unsure": len(unsure),
        "edited_share": share(edited, total),
        "unsure_share": share(len(unsure), total),
        "edited_among_unsure": share(edited_unsure, len(unsure)),
        "unsure_among_edited": share(edited_unsure, edited),  # полнота флага: правки, которые мы предсказали
    }


def delete_submission(conn, submission_id: int) -> None:
    """Для повторной живой проверки той же работы: убрать прежние строки распознавания."""
    ensure_schema(conn)
    conn.execute("DELETE FROM ocr_lines WHERE submission_id=?", (submission_id,))
    conn.commit()

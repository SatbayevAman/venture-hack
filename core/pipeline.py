"""Проверка работы → строки журнала. Это единственный путь, которым
автоматические выводы попадают в базу: и синтетическая история, и живая
проверка идут через одну и ту же функцию."""
from __future__ import annotations

import json
from typing import Optional

from . import db, tags as T
from .checker import CheckResult, check_problem

RULE_CONFIDENCE = 0.9      # правило SymPy сработало однозначно
OTHER_CONFIDENCE = 0.5     # ошибка есть, но тип не определён


def problems_of(conn, assignment_id: int) -> list:
    return db.q(conn, "SELECT * FROM problems WHERE assignment_id=? ORDER BY idx", (assignment_id,))


def run_checks(conn, assignment_id: int, answers: dict) -> dict:
    """Только проверка, без записи. answers: {problem_id: [строки]} → {problem_id: CheckResult}"""
    out = {}
    for p in problems_of(conn, assignment_id):
        lines = answers.get(p["id"]) or []
        if not any(l.strip() for l in lines):
            continue
        ref = json.loads(p["reference"])
        out[p["id"]] = check_problem(p["kind"], p["statement"], lines, len(ref), p["answer"])
    return out


def record_submission(conn, student_id: int, assignment_id: int, answers: dict,
                      submitted_at: str, photo_name: Optional[str] = None,
                      source: str = "live", results: Optional[dict] = None,
                      line_confidence: Optional[dict] = None) -> tuple[int, dict]:
    """Записать сданную работу: шаги, попытки и наблюдения. → (submission_id, results)

    line_confidence: {(problem_id, line_no): уверенность распознавания} — строки из фото,
    которые учитель не поправил (ocr.unverified). Ошибка, которая опирается на такую строку
    (ocr.error_unverified), пишется с confidence = 0.6 и detail.ocr_unverified = True."""
    from .ocr import error_unverified  # внутри функции: без циклического импорта
    ocr_unverified_confidence = 0.6
    line_confidence = line_confidence or {}
    results = results if results is not None else run_checks(conn, assignment_id, answers)
    cur = conn.execute(
        "INSERT INTO submissions (student_id, assignment_id, photo_name, submitted_at, source) VALUES (?,?,?,?,?)",
        (student_id, assignment_id, photo_name, submitted_at, source),
    )
    sub_id = cur.lastrowid
    probs = {p["id"]: p for p in problems_of(conn, assignment_id)}
    check_done_at = None

    for pid, res in results.items():
        p = probs[pid]
        for ln in res.lines:
            conn.execute(
                """INSERT INTO steps (submission_id, problem_id, line_no, raw_text, sympy_text, kind, status, note)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (sub_id, pid, ln.no, ln.raw, ln.norm, ln.kind, ln.status, ln.note),
            )
        fe = res.first_error
        conn.execute(
            "INSERT INTO attempts (submission_id, problem_id, correct, first_error_line, final) VALUES (?,?,?,?,?)",
            (sub_id, pid, None if res.correct is None else int(res.correct),
             fe["line"] if fe else None, json.dumps(res.final, ensure_ascii=False)),
        )
        if fe:
            conf = OTHER_CONFIDENCE if fe["tag"] == "other" else RULE_CONFIDENCE
            detail = fe.get("detail")
            if error_unverified(pid, res, line_confidence):
                conf = min(conf, ocr_unverified_confidence)
                detail = {**(detail or {}), "ocr_unverified": True}
            db.add_observation(
                conn, student_id, sub_id, pid, fe["line"], "error", fe["tag"], p["skill"],
                fe["evidence"], detail, "auto", conf, submitted_at,
            )
        for m in res.methods:
            first = next((l.no for l in res.lines if l.kind in ("disc", "vieta", "eq", "eqs")), None)
            db.add_observation(conn, student_id, sub_id, pid, first, "method", m, p["skill"],
                               p["statement"], {}, "auto", RULE_CONFIDENCE, submitted_at)
        for h in res.habits:
            if h == "check_done":
                if check_done_at is None:
                    ln = next((l for l in res.lines if l.kind == "check"), None)
                    check_done_at = (pid, ln.no if ln else None, ln.raw if ln else "", p["skill"])
                continue
            ln = None
            if h == "domain_noted":
                ln = next((l for l in res.lines if l.kind == "domain"), None)
            db.add_observation(conn, student_id, sub_id, pid, ln.no if ln else None, "habit", h,
                               p["skill"], ln.raw if ln else f"{res.solution_lines} стр. при эталоне {len(json.loads(p['reference']))}",
                               {}, "auto", RULE_CONFIDENCE, submitted_at)
    if check_done_at:
        pid, no, raw, skill = check_done_at
        db.add_observation(conn, student_id, sub_id, pid, no, "habit", "check_done", skill, raw, {},
                           "auto", RULE_CONFIDENCE, submitted_at)
    due = db.q1(conn, "SELECT due_at FROM assignments WHERE id=?", (assignment_id,))["due_at"]
    if submitted_at > due:
        db.add_observation(conn, student_id, sub_id, None, None, "habit", "late", None,
                           f"сдано {submitted_at[:16]}, срок {due[:16]}", {}, "auto", 1.0, submitted_at)
    conn.commit()
    return sub_id, results


def record_comment(conn, submission_id: int, text: str, tagged: list, tagger: str,
                   created_at: Optional[str] = None) -> None:
    """Комментарий учителя → наблюдения source=teacher (теги только из словаря)."""
    sub = db.q1(conn, "SELECT * FROM submissions WHERE id=?", (submission_id,))
    ts = created_at or db.now()
    conn.execute("INSERT INTO teacher_comments (submission_id, text, tagger, created_at) VALUES (?,?,?,?)",
                 (submission_id, text, tagger, ts))
    conf = 0.8 if tagger == "llm" else 0.6
    for t in tagged:
        tag = t["tag"] if t["tag"] in T.TAGS else "teacher_other"
        kind = T.TAGS[tag]["kind"]
        kind = "teacher" if kind in ("teacher", "method") else kind
        db.add_observation(conn, sub["student_id"], submission_id, None, None, kind, tag, None,
                           t.get("quote") or text, {"comment": text}, "teacher", conf, ts)
    conn.commit()


def delete_submission(conn, sub_id: int) -> None:
    """Удалить работу вместе со всеми её следами в журнале (для повторной живой проверки)."""
    for table in ("observations", "steps", "attempts", "teacher_comments"):
        conn.execute(f"DELETE FROM {table} WHERE submission_id=?", (sub_id,))
    conn.execute("DELETE FROM submissions WHERE id=?", (sub_id,))
    conn.commit()

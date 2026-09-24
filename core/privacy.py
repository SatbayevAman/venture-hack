"""Удаление данных ученика по запросу и выгрузка итогов класса — без Streamlit.

Удаление не знает заранее, какие таблицы есть в базе: оно находит их по
столбцам (student_id, submission_id, а затем ссылки observation_id,
session_id, item_id). Поэтому таблицы других направлений — тренажёр,
отметки учителя, интервенции, распознавания — покрываются автоматически.
"""
from __future__ import annotations

import csv
import io

from . import audit, db, portrait, tags as T

# столбец-ссылка → таблица, на удалённые строки которой он указывает
REF_COLUMNS = {"observation_id": "observations", "session_id": "practice_sessions", "item_id": "practice_items"}
# таблицы, где student_id не означает «данные ученика», а обрабатываются отдельно
SPECIAL = {"students", "submissions", "users"}
CSV_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


# общие с pipeline.delete_submission: таблицы и столбцы ищутся по схеме базы
_qi = db.qi
_tables = db.tables


def _in(ids) -> tuple[str, list]:
    ids = sorted(ids)
    return "(" + ",".join("?" * len(ids)) + ")", ids


def delete_student(conn, student_id: int, user=None) -> dict[str, int]:
    """Удалить все данные ученика во всех таблицах. → {таблица: удалено строк}."""
    tables = _tables(conn)
    sub_ids = {r["id"] for r in db.q(conn, "SELECT id FROM submissions WHERE student_id=?", (student_id,))}
    tracked = set(REF_COLUMNS.values())
    gone: dict[str, set] = {t: set() for t in tracked}   # id удаляемых строк в отслеживаемых таблицах
    plan: list[tuple[str, str, list]] = []                # (таблица, WHERE, параметры) — в порядке обнаружения

    def collect(table: str, where: str, params: list) -> None:
        if "id" in tables[table] and table in tracked:
            gone[table] |= {r["id"] for r in db.q(conn, f"SELECT id FROM {_qi(table)} WHERE {where}", params)}
        plan.append((table, where, params))

    # 1. строки ученика и строки его работ
    for t, cols in tables.items():
        if t in SPECIAL:
            continue
        conds, params = [], []
        if "student_id" in cols:
            conds.append("student_id=?")
            params.append(student_id)
        if "submission_id" in cols and sub_ids:
            sql, ids = _in(sub_ids)
            conds.append(f"submission_id IN {sql}")
            params += ids
        if conds:
            collect(t, " OR ".join(conds), params)

    # 2. строки, ссылающиеся на удалённые наблюдения, сессии и задания тренажёра (до неподвижной точки)
    seen: dict[tuple, set] = {}
    changed = True
    while changed:
        changed = False
        for t, cols in tables.items():
            if t in SPECIAL:
                continue
            for col, target in REF_COLUMNS.items():
                if col not in cols or target not in tables:
                    continue
                new = gone[target] - seen.get((t, col), set())
                if not new:
                    continue
                seen.setdefault((t, col), set()).update(new)
                sql, ids = _in(new)
                before = set(gone.get(t, set()))
                collect(t, f"{_qi(col)} IN {sql}", ids)
                if t in tracked and gone[t] != before:
                    changed = True

    # 3. удаляем зависимые строки первыми; проверка внешних ключей — в конце транзакции
    counts: dict[str, int] = {}
    conn.execute("PRAGMA defer_foreign_keys = ON")
    try:
        for t, where, params in reversed(plan):
            n = conn.execute(f"DELETE FROM {_qi(t)} WHERE {where}", params).rowcount
            if n:
                counts[t] = counts.get(t, 0) + n
        if sub_ids:
            sql, ids = _in(sub_ids)
            counts["submissions"] = conn.execute(f"DELETE FROM submissions WHERE id IN {sql}", ids).rowcount
        counts["students"] = conn.execute("DELETE FROM students WHERE id=?", (student_id,)).rowcount
        if "users" in tables:
            n = conn.execute("UPDATE users SET student_id=NULL, disabled=1 WHERE student_id=?", (student_id,)).rowcount
            if n:
                counts["users (unlinked)"] = n
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    # 4. в журнал действий — только факт и id
    audit.log(conn, user, "delete_student", "student", student_id)
    return counts


# ---------------------------------------------------------------- выгрузка

def _cell(v) -> str:
    """Защита от формул в Excel: текст, начинающийся с = + - @, экранируется апострофом."""
    s = "" if v is None else str(v)
    return "'" + s if s.startswith(CSV_FORMULA_PREFIX) else s


def export_columns(lang: str = "ru") -> list[str]:
    ru = lang != "kk"
    cols = ["Псевдоним" if ru else "Бүркеншік ат", "Класс" if ru else "Сынып"]
    for sk in T.SKILLS:
        name = T.SKILLS_SHORT[sk][lang]
        cols.append(f"{name}: " + ("ошибок / работ" if ru else "қате / жұмыс"))
        cols.append(f"{name}: " + ("доля, %" if ru else "үлес, %"))
    cols += ["Проверка ответа" if ru else "Жауапты тексеру",
             "Опоздания" if ru else "Кешігу",
             "Главная рекомендация" if ru else "Басты ұсыныс"]
    return cols


def export_class_csv(conn, lang: str = "ru", student_ids=None) -> bytes:
    """Итоги класса для электронного журнала: CSV в UTF-8 с BOM (открывается в Excel)."""
    rows = portrait.class_map(conn, lang, student_ids=student_ids)
    classes = {r["id"]: r["class_name"] for r in db.q(conn, "SELECT id, class_name FROM students")}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(export_columns(lang))
    for r in rows:
        line = [_cell(r["alias"]), _cell(classes.get(r["id"], ""))]
        for sk in T.SKILLS:
            c = r["cells"][sk]
            line += [f"{c['bad']} / {c['total']}" if c["total"] else "—",
                     round(c["p"] * 100) if c["total"] else ""]
        chk, n = r["check"]
        line += [f"{chk} / {n}", r["late"], _cell(r["top"] or "")]
        w.writerow(line)
    return buf.getvalue().encode("utf-8-sig")

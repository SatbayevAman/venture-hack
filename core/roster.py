"""Классы, списки учеников и задания учителя — без Streamlit (этап B агента Дзета).

* Класс — строка students.class_name; пустой класс хранится в своей таблице classes.
* Импорт списка — CSV с колонками alias,class_name: только псевдонимы, без ФИО.
* Задание сохраняется, только если эталон каждой задачи проходит ту же
  проверку SymPy, что и работы учеников: без ошибок и с верным ответом.
"""
from __future__ import annotations

import csv
import importlib
import io
import json
import re

from . import db, tags as T
from .checker import ParseError, check_problem, normalize, parse

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
"""

MAX_ALIAS = 40
MAX_CLASS = 30
MAX_IMPORT = 500
CSV_HEADER = ("alias", "class_name")
_BAD_START = ("=", "+", "-", "@")
_CONTROL = re.compile(r"[\x00-\x1f\x7f<>]")
_FULL_NAME = re.compile(r"^[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][a-zа-яёәғқңөұүһі]+\s+[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][a-zа-яёәғқңөұүһі]+")


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def _msg(ru: str, kk: str) -> dict:
    return {"ru": ru, "kk": kk}


# ---------------------------------------------------------------- классы

def list_classes(conn) -> list[str]:
    ensure_schema(conn)
    names = {r["name"] for r in db.q(conn, "SELECT name FROM classes")}
    names |= {r["class_name"] for r in db.q(conn, "SELECT DISTINCT class_name FROM students")}
    return sorted(names)


def class_error(name: str) -> dict | None:
    name = (name or "").strip()
    if not name:
        return _msg("Пустое название класса.", "Сынып атауы бос.")
    if len(name) > MAX_CLASS or _CONTROL.search(name) or name.startswith(_BAD_START):
        return _msg(f"Недопустимое название класса «{name[:MAX_CLASS]}».", f"«{name[:MAX_CLASS]}» сынып атауы жарамсыз.")
    return None


def create_class(conn, name: str) -> str:
    ensure_schema(conn)
    name = (name or "").strip()
    if class_error(name):
        raise ValueError(class_error(name)["ru"])
    conn.execute("INSERT OR IGNORE INTO classes (name, created_at) VALUES (?,?)", (name, db.now()))
    conn.commit()
    return name


def alias_error(alias: str) -> dict | None:
    alias = (alias or "").strip()
    if not alias:
        return _msg("Пустой псевдоним.", "Бүркеншік ат бос.")
    if len(alias) > MAX_ALIAS or _CONTROL.search(alias) or alias.startswith(_BAD_START):
        return _msg(f"Недопустимый псевдоним «{alias[:MAX_ALIAS]}».", f"«{alias[:MAX_ALIAS]}» бүркеншік аты жарамсыз.")
    return None


def looks_like_full_name(alias: str) -> bool:
    """«Иванов Иван» — похоже на ФИО: предупреждаем (в системе должны быть только псевдонимы)."""
    return bool(_FULL_NAME.match((alias or "").strip()))


def parse_students_csv(data: bytes | str) -> tuple[list[dict], list[dict], list[dict]]:
    """CSV → (строки, ошибки, предупреждения). Разделитель , или ; кодировка UTF-8 (с BOM или без)."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return [], [_msg("Файл не в кодировке UTF-8.", "Файл UTF-8 кодтауында емес.")], []
    else:
        text = data.lstrip("﻿")
    lines = text.splitlines()
    if not lines:
        return [], [_msg("Файл пустой.", "Файл бос.")], []
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    header = [h.strip().lower() for h in next(reader, [])]
    if tuple(header) != CSV_HEADER:
        return [], [_msg("Нужны ровно две колонки: alias,class_name (первая строка — заголовок).",
                         "Дәл екі баған керек: alias,class_name (бірінші жол — тақырып).")], []
    rows, errors, warnings, seen = [], [], [], set()
    for no, rec in enumerate(reader, start=2):
        if not any(c.strip() for c in rec):
            continue
        if len(rec) != 2:
            errors.append(_msg(f"Строка {no}: нужно 2 значения.", f"{no}-жол: 2 мән керек."))
            continue
        alias, cls = rec[0].strip(), rec[1].strip()
        err = alias_error(alias) or class_error(cls)
        if err:
            errors.append({k: f"{'Строка' if k == 'ru' else 'Жол'} {no}: {v}" for k, v in err.items()})
            continue
        if (alias.lower(), cls) in seen:
            errors.append(_msg(f"Строка {no}: дубликат «{alias}» в классе {cls}.", f"{no}-жол: {cls} сыныбында «{alias}» қайталанады."))
            continue
        seen.add((alias.lower(), cls))
        if looks_like_full_name(alias):
            warnings.append(_msg(f"Строка {no}: «{alias}» похоже на имя и фамилию — используйте псевдоним.",
                                 f"{no}-жол: «{alias}» аты-жөніне ұқсайды — бүркеншік ат қолданыңыз."))
        rows.append({"alias": alias, "class_name": cls})
    if len(rows) > MAX_IMPORT:
        errors.append(_msg(f"Слишком много строк (больше {MAX_IMPORT}).", f"Жол тым көп ({MAX_IMPORT}-ден артық)."))
    return rows, errors, warnings


def existing_duplicates(conn, rows: list[dict]) -> list[dict]:
    """Строки импорта, которые уже есть в базе (тот же псевдоним в том же классе)."""
    have = {(r["alias"].lower(), r["class_name"]) for r in db.q(conn, "SELECT alias, class_name FROM students")}
    return [r for r in rows if (r["alias"].lower(), r["class_name"]) in have]


def import_students(conn, rows: list[dict]) -> list[int]:
    """Добавить учеников (всё или ничего). Дубликаты в базе — ValueError."""
    ensure_schema(conn)
    dup = existing_duplicates(conn, rows)
    if dup:
        raise ValueError(f"already exists: {len(dup)}")
    ids = []
    try:
        for r in rows:
            conn.execute("INSERT OR IGNORE INTO classes (name, created_at) VALUES (?,?)", (r["class_name"], db.now()))
            ids.append(conn.execute("INSERT INTO students (alias, class_name) VALUES (?,?)",
                                    (r["alias"], r["class_name"])).lastrowid)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ids


def class_teachers(conn, class_name: str) -> list[int]:
    from . import auth
    auth.ensure_schema(conn)
    return [r["user_id"] for r in db.q(conn, "SELECT user_id FROM teacher_classes WHERE class_name=?", (class_name,))]


def set_class_teachers(conn, class_name: str, user_ids) -> None:
    from . import auth
    auth.ensure_schema(conn)
    conn.execute("DELETE FROM teacher_classes WHERE class_name=?", (class_name,))
    for uid in sorted(set(user_ids)):
        conn.execute("INSERT INTO teacher_classes (user_id, class_name) VALUES (?,?)", (uid, class_name))
    conn.commit()


# ---------------------------------------------------------------- задания

def problem_kinds() -> list[str]:
    """equation, expression и виды Беты из core/kinds, если модуль есть и объявляет KINDS."""
    kinds = ["equation", "expression"]
    try:
        mod = importlib.import_module("core.kinds")
        extra = getattr(mod, "KINDS", None) or []
        kinds += [k for k in extra if k not in kinds]
    except ImportError:
        pass
    return kinds


def next_number(conn) -> int:
    row = db.q1(conn, "SELECT MAX(number) m FROM assignments")
    return (row["m"] or 0) + 1


def _statement_error(kind: str, statement: str) -> dict | None:
    text = normalize(statement).text
    try:
        if kind == "equation":
            sides = [s for s in text.split("=")]
            if len(sides) != 2 or not all(s.strip() for s in sides):
                return _msg("В уравнении должен быть ровно один знак «=».", "Теңдеуде дәл бір «=» белгісі болуы керек.")
            for s in sides:
                parse(s)
        elif kind == "expression":
            if "=" in text:
                return _msg("В выражении не должно быть знака «=».", "Өрнекте «=» белгісі болмауы керек.")
            parse(text)
    except ParseError:
        return _msg("Условие не разбирается: проверьте запись.", "Шарт талданбады: жазылуын тексеріңіз.")
    return None


def validate_problem(p: dict) -> list[dict]:
    """Ошибки задачи (пустой список — можно сохранять).
    p: statement, kind, skill, reference (список строк), answer."""
    errs = []
    statement = (p.get("statement") or "").strip()
    kind, skill = p.get("kind"), p.get("skill")
    ref = [l for l in (p.get("reference") or []) if l.strip()]
    answer = (p.get("answer") or "").strip()
    if not statement:
        return [_msg("Нет условия.", "Шарт жоқ.")]
    if kind not in problem_kinds():
        errs.append(_msg(f"Неизвестный вид задачи: {kind}.", f"Есептің белгісіз түрі: {kind}."))
    if skill not in T.SKILLS:
        errs.append(_msg(f"Неизвестный навык: {skill}.", f"Белгісіз дағды: {skill}."))
    if not ref:
        errs.append(_msg("Нет эталонного решения.", "Эталон шешім жоқ."))
    if not answer:
        errs.append(_msg("Нет ответа.", "Жауап жоқ."))
    if errs:
        return errs
    if kind in ("equation", "expression"):
        e = _statement_error(kind, statement)
        if e:
            return [e]
    try:
        res = check_problem(kind, statement, ref + [f"Ответ: {answer}"], len(ref), answer)
    except Exception:  # noqa: BLE001 — любой сбой движка = задание не сохраняем
        return [_msg("Проверка не смогла разобрать эталон.", "Тексеру эталонды талдай алмады.")]
    unparsed = [l.no for l in res.lines if l.status == "unparsed"]
    if unparsed:
        errs.append(_msg(f"Эталон: не разобраны строки {', '.join(map(str, unparsed))}.",
                         f"Эталон: {', '.join(map(str, unparsed))} жолдары талданбады."))
    if res.first_error:
        fe = res.first_error
        where = "ответ" if fe["line"] > len(ref) else f"строка {fe['line']}"
        where_kk = "жауап" if fe["line"] > len(ref) else f"{fe['line']}-жол"
        errs.append(_msg(f"Эталон не прошёл проверку: {where} — {T.name(fe['tag'], 'ru').lower()} ({fe['evidence']}).",
                         f"Эталон тексеруден өтпеді: {where_kk} — {T.name(fe['tag'], 'kk').lower()} ({fe['evidence']})."))
    elif res.correct is not True:
        errs.append(_msg("Ответ не совпадает с решением условия или не упрощён до конца.",
                         "Жауап шарттың шешімімен сәйкес келмейді немесе соңына дейін ықшамдалмаған."))
    return errs


def create_assignment(conn, title: str, due_at: str, problems: list[dict], number: int | None = None) -> int:
    """Сохранить ДЗ; если хоть одна задача не прошла проверку — ValueError с ошибками в args[0]."""
    title = (title or "").strip()
    errors = []
    if not title:
        errors.append(_msg("Нет названия.", "Атауы жоқ."))
    if not problems:
        errors.append(_msg("Нет задач.", "Есеп жоқ."))
    for i, p in enumerate(problems, start=1):
        errors += [{k: f"№{i}: {v}" for k, v in e.items()} for e in validate_problem(p)]
    if errors:
        raise ValueError(errors)
    number = number or next_number(conn)
    if db.q1(conn, "SELECT 1 FROM assignments WHERE number=?", (number,)):
        raise ValueError([_msg(f"ДЗ №{number} уже есть.", f"№{number} ҮТ бар.")])
    try:
        aid = conn.execute("INSERT INTO assignments (number, title, due_at) VALUES (?,?,?)",
                           (number, title, due_at)).lastrowid
        for i, p in enumerate(problems, start=1):
            ref = [l.strip() for l in p["reference"] if l.strip()]
            conn.execute("INSERT INTO problems (assignment_id, idx, skill, kind, statement, reference, answer) VALUES (?,?,?,?,?,?,?)",
                         (aid, i, p["skill"], p["kind"], p["statement"].strip(), json.dumps(ref, ensure_ascii=False),
                          p["answer"].strip()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return aid

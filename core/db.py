"""SQLite: журнал наблюдений и всё, что вокруг него.

Центр — таблица observations: каждая проверка и каждый комментарий учителя
добавляют строки «ученик — что замечено — где доказательство». Портрет не
хранится, а каждый раз пересчитывается из этих строк.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import tags as T

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    alias TEXT NOT NULL,           -- псевдоним, не настоящее имя
    class_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    due_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS problems (
    id INTEGER PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    idx INTEGER NOT NULL,
    skill TEXT NOT NULL,           -- tags.SKILLS: linear | quadratic | simplify | inequality | system | biquadratic | fractions
    kind TEXT NOT NULL,            -- equation | expression | виды core/kinds: inequality | system | biquadratic
    statement TEXT NOT NULL,
    reference TEXT NOT NULL,       -- эталонное решение, JSON-список строк
    answer TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    photo_name TEXT,
    submitted_at TEXT NOT NULL,
    source TEXT NOT NULL           -- synthetic | live
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    problem_id INTEGER NOT NULL REFERENCES problems(id),
    correct INTEGER,               -- 1 / 0 / NULL (не удалось определить)
    first_error_line INTEGER,
    final TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    problem_id INTEGER NOT NULL REFERENCES problems(id),
    line_no INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    sympy_text TEXT,
    kind TEXT,                     -- вид строки: checker.LineResult.kind (подписи — tags.LINE_KINDS)
    status TEXT,                   -- ok | error | after | info | unparsed
    note TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    submission_id INTEGER REFERENCES submissions(id),
    problem_id INTEGER REFERENCES problems(id),
    line_no INTEGER,
    kind TEXT NOT NULL,            -- error | method | habit | teacher
    tag TEXT NOT NULL,             -- только коды из словаря tags
    skill TEXT,
    evidence TEXT,                 -- цитата: строки работы или слова учителя
    detail TEXT,                   -- JSON: подробности правила
    source TEXT NOT NULL,          -- auto | teacher | practice (исходы тренажёра; в числа портрета не входят)
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teacher_comments (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    text TEXT NOT NULL,
    tagger TEXT,                   -- llm | keywords
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    code TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name_ru TEXT, name_kk TEXT,
    advice_teacher_ru TEXT, advice_teacher_kk TEXT,
    advice_student_ru TEXT, advice_student_kk TEXT
);
CREATE INDEX IF NOT EXISTS obs_student ON observations(student_id);
CREATE INDEX IF NOT EXISTS steps_sub ON steps(submission_id, problem_id);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM tags")
    for code, t in T.TAGS.items():
        conn.execute(
            "INSERT INTO tags VALUES (?,?,?,?,?,?,?,?)",
            (code, t["kind"], t["ru"], t["kk"], t.get("teacher_ru"), t.get("teacher_kk"),
             t.get("student_ru"), t.get("student_kk")),
        )
    conn.commit()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_observation(conn, student_id, submission_id, problem_id, line_no, kind, tag, skill,
                    evidence, detail, source, confidence, created_at=None) -> None:
    if tag not in T.TAGS:  # контракт: только теги из словаря
        tag = "teacher_other" if source == "teacher" else "other"
    conn.execute(
        """INSERT INTO observations (student_id, submission_id, problem_id, line_no, kind, tag,
               skill, evidence, detail, source, confidence, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (student_id, submission_id, problem_id, line_no, kind, tag, skill, evidence,
         json.dumps(detail or {}, ensure_ascii=False), source, confidence, created_at or now()),
    )


def qi(name: str) -> str:
    """Имя таблицы или столбца из sqlite_master — в кавычках SQL-идентификатора."""
    return '"' + name.replace('"', '""') + '"'


def tables(conn) -> dict[str, list[str]]:
    """{таблица: [столбцы]} по sqlite_master и PRAGMA table_info — включая таблицы, которые модули
    направлений создают сами. Удаление работы и ученика ищет по ним столбцы-ссылки, поэтому новые
    таблицы покрываются без правок удаления."""
    names = [r["name"] for r in q(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {n: [c["name"] for c in q(conn, f"PRAGMA table_info({qi(n)})")] for n in names}


def q(conn, sql: str, params=()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def q1(conn, sql: str, params=()):
    return conn.execute(sql, params).fetchone()

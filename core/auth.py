"""Пользователи, роли и видимость данных — без Streamlit.

Роли: admin — всё; teacher — только ученики своих классов (teacher_classes);
student — только мягкая версия своего портрета (и тренажёр, если он есть).
Пароли хранятся только как PBKDF2-SHA256 с солью; в коде паролей нет.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    login TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'teacher', 'student')),
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    student_id INTEGER,              -- для роли student
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teacher_classes (
    user_id INTEGER NOT NULL,
    class_name TEXT NOT NULL,
    PRIMARY KEY (user_id, class_name)
);
"""

ROLES = ("admin", "teacher", "student")
ROLE_NAMES = {"admin": {"ru": "администратор", "kk": "әкімші"},
              "teacher": {"ru": "учитель", "kk": "мұғалім"},
              "student": {"ru": "ученик", "kk": "оқушы"}}
ITERATIONS = 200_000
MIN_PASSWORD = 8
DEMO_TEACHER = "demo_teacher"
DEMO_STUDENT = "demo_student"
STUDENT_PAGES = ("portrait", "practice")

# пауза после неудачных попыток входа: счётчик в памяти процесса
MAX_FAILURES = 5
FAILURE_WINDOW = 10 * 60
# ограничение частоты вызовов языковой модели на пользователя
RATE_LIMITS = {"llm": (30, 3600)}

_failures: dict[str, deque] = defaultdict(deque)
_calls: dict[tuple, deque] = defaultdict(deque)


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


# ---------------------------------------------------------------- пароли

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """→ (hash_hex, salt_hex). Соль — secrets.token_hex(16), если не задана."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt)[0], password_hash)


# ---------------------------------------------------------------- пользователи

def _public(row) -> dict | None:
    """Пользователь без хэша и соли — то, что живёт в сессии."""
    if row is None:
        return None
    return {k: row[k] for k in ("id", "login", "display_name", "role", "student_id", "disabled")}


def create_user(conn, login: str, display_name: str, role: str, password: str,
                student_id: int | None = None, classes=()) -> int:
    ensure_schema(conn)
    login = login.strip().lower()
    if not login or not display_name.strip():
        raise ValueError("login and display_name are required")
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    if not password:
        raise ValueError("empty password")
    if role == "student" and student_id is None:
        raise ValueError("student account needs student_id")
    h, salt = hash_password(password)
    uid = conn.execute(
        """INSERT INTO users (login, display_name, role, password_hash, salt, student_id, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (login, display_name.strip(), role, h, salt, student_id if role == "student" else None, db.now()),
    ).lastrowid
    if classes:
        set_teacher_classes(conn, uid, classes)
    conn.commit()
    return uid


def set_password(conn, user_id: int, password: str) -> None:
    if not password:
        raise ValueError("empty password")
    h, salt = hash_password(password)
    conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (h, salt, user_id))
    conn.commit()


def set_disabled(conn, user_id: int, disabled: bool) -> None:
    conn.execute("UPDATE users SET disabled=? WHERE id=?", (int(bool(disabled)), user_id))
    conn.commit()


def set_teacher_classes(conn, user_id: int, classes) -> None:
    ensure_schema(conn)
    conn.execute("DELETE FROM teacher_classes WHERE user_id=?", (user_id,))
    for c in sorted(set(classes)):
        conn.execute("INSERT INTO teacher_classes (user_id, class_name) VALUES (?,?)", (user_id, c))
    conn.commit()


def teacher_classes(conn, user_id: int) -> list[str]:
    ensure_schema(conn)
    return [r["class_name"] for r in db.q(conn, "SELECT class_name FROM teacher_classes WHERE user_id=? ORDER BY class_name",
                                          (user_id,))]


def get_user(conn, user_id: int) -> dict | None:
    ensure_schema(conn)
    return _public(db.q1(conn, "SELECT * FROM users WHERE id=?", (user_id,)))


def get_user_by_login(conn, login: str) -> dict | None:
    ensure_schema(conn)
    return _public(db.q1(conn, "SELECT * FROM users WHERE login=?", (login.strip().lower(),)))


def list_users(conn) -> list[dict]:
    ensure_schema(conn)
    out = []
    for r in db.q(conn, "SELECT * FROM users ORDER BY role, login"):
        u = _public(r)
        u["classes"] = teacher_classes(conn, r["id"])
        u["created_at"] = r["created_at"]
        out.append(u)
    return out


def authenticate(conn, login: str, password: str) -> dict | None:
    """Пользователь или None. Причину отказа наружу не отдаём: «нет такого логина»,
    «неверный пароль» и «отключён» неотличимы для вызывающего кода."""
    ensure_schema(conn)
    if not login or not password:
        return None
    row = db.q1(conn, "SELECT * FROM users WHERE login=?", (login.strip().lower(),))
    if row is None:
        hash_password(password, "00" * 16)  # то же время ответа, что и для существующего логина
        return None
    if not verify_password(password, row["password_hash"], row["salt"]) or row["disabled"]:
        return None
    return _public(row)


# ---------------------------------------------------------------- пауза после неудачных попыток

def _prune(q: deque, window: float, now: float) -> None:
    while q and now - q[0] > window:
        q.popleft()


def login_locked(key: str, now: float | None = None) -> int:
    """Сколько секунд ещё ждать (0 — можно пробовать)."""
    now = time.time() if now is None else now
    q = _failures[key.strip().lower()]
    _prune(q, FAILURE_WINDOW, now)
    if len(q) < MAX_FAILURES:
        return 0
    return max(1, int(FAILURE_WINDOW - (now - q[0])))


def register_failure(key: str, now: float | None = None) -> None:
    _failures[key.strip().lower()].append(time.time() if now is None else now)


def reset_failures(key: str) -> None:
    _failures.pop(key.strip().lower(), None)


def rate_limit(user: dict | None, action: str = "llm", now: float | None = None) -> bool:
    """True — вызов разрешён и засчитан; False — лимит на пользователя исчерпан."""
    limit, window = RATE_LIMITS.get(action, (30, 3600))
    now = time.time() if now is None else now
    q = _calls[((user or {}).get("id"), action)]
    _prune(q, window, now)
    if len(q) >= limit:
        return False
    q.append(now)
    return True


# ---------------------------------------------------------------- видимость

def dev_user() -> dict:
    """Псевдо-администратор для PORTRET_AUTH=off (только локальная разработка)."""
    return {"id": None, "login": "dev", "display_name": "Локальная разработка", "role": "admin",
            "student_id": None, "disabled": 0, "dev": True}


def visible_student_ids(conn, user: dict | None) -> set[int] | None:
    """None — все ученики (администратор); иначе множество id."""
    if not user:
        return set()
    if user["role"] == "admin":
        return None
    if user["role"] == "student":
        return {user["student_id"]} if user.get("student_id") is not None else set()
    ensure_schema(conn)
    rows = db.q(conn, """SELECT s.id FROM students s JOIN teacher_classes tc ON tc.class_name = s.class_name
                         WHERE tc.user_id=?""", (user["id"],))
    return {r["id"] for r in rows}


def allowed_pages(user: dict | None, pages: list) -> list:
    if not user:
        return []
    if user["role"] == "student":
        return [p for p in pages if p in STUDENT_PAGES]
    return list(pages)


def can_see(conn, user: dict | None, student_id: int) -> bool:
    ids = visible_student_ids(conn, user)
    return ids is None or student_id in ids


# ---------------------------------------------------------------- демо и администратор

def _env_password() -> str:
    return os.environ.get("PORTRET_DEMO_PASSWORD") or secrets.token_urlsafe(18)


def ensure_demo_users(conn) -> None:
    """Демо-учитель (синтетический класс) и демо-ученик (Айгерим); администратор — из окружения.
    Паролей в коде нет: PORTRET_DEMO_PASSWORD или случайный (демо-вход — кнопкой)."""
    from . import seed  # seed тянет pipeline и SymPy — импортируем лениво

    ensure_schema(conn)
    if not get_user_by_login(conn, DEMO_TEACHER):
        create_user(conn, DEMO_TEACHER, "Демо-учитель", "teacher", _env_password(), classes=[seed.CLASS_NAME])
    if not get_user_by_login(conn, DEMO_STUDENT):
        st = db.q1(conn, "SELECT id FROM students WHERE alias=? AND class_name=? ORDER BY id LIMIT 1",
                   (seed.STUDENTS[0], seed.CLASS_NAME))
        if st:
            create_user(conn, DEMO_STUDENT, "Демо-ученик", "student", _env_password(), student_id=st["id"])
    login, password = os.environ.get("PORTRET_ADMIN_LOGIN", ""), os.environ.get("PORTRET_ADMIN_PASSWORD", "")
    if login and password:
        admin = get_user_by_login(conn, login)
        if not admin:
            create_user(conn, login, "Администратор", "admin", password)
        elif admin["role"] == "admin":
            row = db.q1(conn, "SELECT password_hash, salt FROM users WHERE id=?", (admin["id"],))
            if not verify_password(password, row["password_hash"], row["salt"]):
                set_password(conn, admin["id"], password)  # пароль сменили в окружении


def is_demo(user: dict | None) -> bool:
    return bool(user) and user.get("login") in (DEMO_TEACHER, DEMO_STUDENT)

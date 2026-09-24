"""Журнал действий — кто, что и над каким объектом сделал.

В записи только идентификаторы и код действия из фиксированного списка:
ни имён, ни логинов, ни текста работ, ни паролей. Свободного текста в
таблице нет вовсе — поэтому персональные данные в неё попасть не могут.
"""
from __future__ import annotations

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id INTEGER,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_at ON audit_log(at);
"""

ACTIONS = {
    "login": {"ru": "вход", "kk": "кіру"},
    "login_failed": {"ru": "неудачный вход", "kk": "сәтсіз кіру"},
    "logout": {"ru": "выход", "kk": "шығу"},
    "session_timeout": {"ru": "сессия истекла", "kk": "сессия мерзімі өтті"},
    "view_portrait": {"ru": "просмотр портрета", "kk": "портретті қарау"},
    "record_submission": {"ru": "запись работы", "kk": "жұмысты жазу"},
    "record_refused": {"ru": "запись отклонена: нет согласия", "kk": "жазу қабылданбады: келісім жоқ"},
    "export_csv": {"ru": "выгрузка CSV", "kk": "CSV түсіру"},
    "export_quality": {"ru": "выгрузка со страницы «Качество»", "kk": "«Сапа» бетінен түсіру"},
    "delete_student": {"ru": "удаление данных ученика", "kk": "оқушы деректерін жою"},
    "consent_given": {"ru": "согласие отмечено", "kk": "келісім белгіленді"},
    "consent_revoked": {"ru": "согласие отозвано", "kk": "келісім қайтарылды"},
    "user_created": {"ru": "создан пользователь", "kk": "пайдаланушы құрылды"},
    "user_disabled": {"ru": "пользователь отключён", "kk": "пайдаланушы өшірілді"},
    "user_enabled": {"ru": "пользователь включён", "kk": "пайдаланушы қосылды"},
    "password_changed": {"ru": "пароль изменён", "kk": "құпиясөз өзгертілді"},
    "classes_assigned": {"ru": "классы учителя изменены", "kk": "мұғалім сыныптары өзгертілді"},
    "class_created": {"ru": "создан класс", "kk": "сынып құрылды"},
    "students_imported": {"ru": "импорт списка учеников", "kk": "оқушылар тізімін импорттау"},
    "assignment_created": {"ru": "создано задание", "kk": "тапсырма құрылды"},
}
TARGETS = ("student", "submission", "user", "class", "assignment", "export")


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def _uid(user) -> int | None:
    if user is None:
        return None
    if isinstance(user, int):
        return user
    return user.get("id")


def log(conn, user, action: str, target_type: str | None = None, target_id: int | None = None) -> int:
    """Записать действие. Только коды и числа: иначе ValueError."""
    ensure_schema(conn)
    if action not in ACTIONS:
        raise ValueError(f"unknown audit action: {action}")
    if target_type is not None and target_type not in TARGETS:
        raise ValueError(f"unknown audit target: {target_type}")
    if target_id is not None:
        target_id = int(target_id)
    rid = conn.execute("INSERT INTO audit_log (user_id, action, target_type, target_id, at) VALUES (?,?,?,?,?)",
                       (_uid(user), action, target_type, target_id, db.now())).lastrowid
    conn.commit()
    return rid


def recent(conn, limit: int = 200) -> list[dict]:
    ensure_schema(conn)
    return [dict(r) for r in db.q(conn, "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),))]


def action_name(action: str, lang: str = "ru") -> str:
    return ACTIONS.get(action, {}).get(lang, action)

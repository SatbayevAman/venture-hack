"""Экран входа и сессия пользователя.

gate(conn, L) вызывается в app.py до построения меню разделов: без входа
рисует форму и останавливает скрипт (st.stop), поэтому незалогиненный
пользователь не видит ни меню, ни данных.
"""
from __future__ import annotations

import os
import time

import streamlit as st

from core import audit, auth

SESSION_TIMEOUT = 60 * 60  # неактивность, секунды
KEEP_ON_LOGOUT = ("lang",)


def auth_off() -> bool:
    """PORTRET_AUTH=off — только для локальной разработки, никогда на публичной ссылке."""
    return os.environ.get("PORTRET_AUTH", "on").strip().lower() == "off"


def demo_enabled() -> bool:
    return os.environ.get("PORTRET_DEMO", "1").strip() != "0"


def _lang(L) -> str:
    return L("ru", "kk")


def _clear_session() -> None:
    """Выход: убираем всё, что видел прежний пользователь (работы, портреты, ключ модели)."""
    ss = st.session_state
    for k in list(ss.keys()):
        if k not in KEEP_ON_LOGOUT:
            del ss[k]


def _start(conn, user: dict) -> None:
    _clear_session()
    st.session_state["user"] = user
    st.session_state["user_seen_at"] = time.time()
    audit.log(conn, user, "login", "user", user["id"])


def logout(conn, user: dict, action: str = "logout") -> None:
    audit.log(conn, user, action, "user", user.get("id"))
    _clear_session()


def _current(conn) -> dict | None:
    ss = st.session_state
    user = ss.get("user")
    if not user:
        return None
    now = time.time()
    if now - ss.get("user_seen_at", 0) > SESSION_TIMEOUT:
        logout(conn, user, "session_timeout")
        ss["login_notice"] = "timeout"
        return None
    fresh = auth.get_user(conn, user["id"])  # отключили, удалили или сбросили демо — сессия кончается
    if not fresh or fresh["disabled"] or fresh["role"] != user["role"] or fresh["student_id"] != user["student_id"]:
        _clear_session()
        return None
    ss["user"] = fresh
    ss["user_seen_at"] = now
    return fresh


def _sidebar(conn, user: dict, L) -> None:
    lang = _lang(L)
    role = auth.ROLE_NAMES[user["role"]][lang]
    extra = ""
    if user["role"] == "teacher":
        extra = ", ".join(auth.teacher_classes(conn, user["id"]))
    elif user["role"] == "student" and user.get("student_id") is not None:
        extra = L("своя страница", "өз бетім")
    with st.sidebar:
        st.markdown(f"**{user['display_name']}**" + (f" · {extra}" if extra else ""))
        st.caption(role + (L(" · демо, данные синтетические", " · демо, деректер синтетикалық") if auth.is_demo(user) else ""))
        if st.button(L("Выйти", "Шығу"), use_container_width=True, key="logout_btn"):
            logout(conn, user)
            st.rerun()


def _demo_login(conn, login: str, L) -> None:
    user = auth.get_user_by_login(conn, login)
    if not user or user["disabled"]:
        st.error(L("Демо-вход недоступен.", "Демо-кіру қолжетімсіз."))
        return
    _start(conn, user)
    st.rerun()


def _try_login(conn, login: str, password: str, L) -> None:
    key = (login or "").strip().lower()
    wait = auth.login_locked(key) if key else 0
    if wait:
        st.error(L(f"Слишком много неудачных попыток. Попробуйте через {wait // 60 + 1} мин.",
                   f"Сәтсіз әрекет тым көп. {wait // 60 + 1} минуттан кейін қайталаңыз."))
        return
    user = auth.authenticate(conn, login, password)
    if user is None:
        if key:
            auth.register_failure(key)
        audit.log(conn, None, "login_failed")  # без логина: он может оказаться именем
        st.error(L("Неверный логин или пароль.", "Логин немесе құпиясөз қате."))  # одинаково для всех причин
        return
    auth.reset_failures(key)
    _start(conn, user)
    st.rerun()


def _form(conn, L) -> None:
    ss = st.session_state
    st.title(L("📐 Портрет ученика — вход", "📐 Оқушы портреті — кіру"))
    st.caption(L("Автопроверка домашних работ по алгебре и портрет ученика «где слаб и как работать».",
                 "Алгебрадан үй жұмысын автоматты тексеру және «қай жері әлсіз, қалай жұмыс істеу керек» портреті."))
    if ss.pop("login_notice", None) == "timeout":
        st.info(L("Сессия завершена: больше 60 минут без действий. Войдите снова.",
                  "Сессия аяқталды: 60 минуттан астам әрекетсіз. Қайта кіріңіз."))
    col_form, col_demo = st.columns([3, 2], gap="large")
    with col_form:
        with st.form("login_form"):
            login = st.text_input(L("Логин", "Логин"), autocomplete="username")
            password = st.text_input(L("Пароль", "Құпиясөз"), type="password", autocomplete="current-password")
            submitted = st.form_submit_button(L("Войти", "Кіру"), type="primary", use_container_width=True)
        if submitted:
            _try_login(conn, login, password, L)
    with col_demo:
        if demo_enabled():
            st.markdown("**" + L("Демо для жюри", "Қазылар алқасына арналған демо") + "**")
            st.markdown('<span class="badge b-syn">' + L("синтетический класс, настоящих данных нет",
                                                         "синтетикалық сынып, нақты деректер жоқ") + "</span>",
                        unsafe_allow_html=True)
            st.write("")
            if st.button(L("Войти как демо-учитель", "Демо-мұғалім ретінде кіру"), type="primary", use_container_width=True):
                _demo_login(conn, auth.DEMO_TEACHER, L)
            if st.button(L("Войти как демо-ученик", "Демо-оқушы ретінде кіру"), use_container_width=True):
                _demo_login(conn, auth.DEMO_STUDENT, L)
            st.caption(L("Демо-учитель видит только синтетический класс 8 «А». Демо-ученик — только свой портрет.",
                         "Демо-мұғалім тек синтетикалық 8 «А» сыныбын көреді. Демо-оқушы — тек өз портретін."))


def gate(conn, L) -> dict:
    """Текущий пользователь. Без входа рисует форму и вызывает st.stop()."""
    if auth_off():
        with st.sidebar:
            st.caption(L("⚠️ Вход отключён (PORTRET_AUTH=off) — только для локальной разработки.",
                         "⚠️ Кіру өшірілген (PORTRET_AUTH=off) — тек жергілікті әзірлеу үшін."))
        return auth.dev_user()
    auth.ensure_demo_users(conn)
    user = _current(conn)
    if user:
        _sidebar(conn, user, L)
        return user
    _form(conn, L)
    st.stop()
    raise RuntimeError("unreachable")  # st.stop() прерывает скрипт

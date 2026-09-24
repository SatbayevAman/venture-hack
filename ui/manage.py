"""Страница «🛡️ Управление».

Учитель: выгрузка итогов своих классов в CSV, согласия своих учеников,
классы и задания. Администратор — дополнительно: пользователи и классы
учителей, журнал действий, удаление данных ученика.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import audit, auth, consent, db, privacy


def _visible_students(conn, user) -> list:
    ids = auth.visible_student_ids(conn, user)
    rows = db.q(conn, "SELECT * FROM students ORDER BY class_name, id")
    return [r for r in rows if ids is None or r["id"] in ids]


def _forget_student(student_id: int) -> None:
    """После удаления ученика убрать его из состояния страниц (выбор, проверка, дифф)."""
    ss = st.session_state
    for k in list(ss.keys()):
        if k in ("student_id", "chk_student") and ss.get(k) == student_id:
            del ss[k]
        elif k.startswith(("w_student_id_", "w_chk_student_")) and ss.get(k) == student_id:
            del ss[k]
    for k in ("check", "diff"):
        if isinstance(ss.get(k), dict) and ss[k].get("sid") == student_id:
            del ss[k]


# ---------------------------------------------------------------- выгрузка

def _log_export(conn, user):
    audit.log(conn, user, "export_csv", "export", None)


def tab_export(conn, user, L, lang):
    st.markdown("#### " + L("Итоги класса для электронного журнала", "Электрондық журналға арналған сынып қорытындысы"))
    st.caption(L("CSV: псевдоним; по каждому навыку «ошибок / работ» и доля; проверка ответа; опоздания; "
                 "главная рекомендация. Кодировка UTF-8 с BOM — файл открывается в Excel.",
                 "CSV: бүркеншік ат; әр дағды бойынша «қате / жұмыс» және үлесі; жауапты тексеру; кешігу; "
                 "басты ұсыныс. UTF-8 (BOM) кодтауы — файл Excel-де ашылады."))
    studs = _visible_students(conn, user)
    if not studs:
        st.info(L("Нет учеников в ваших классах.", "Сыныптарыңызда оқушы жоқ."))
        return
    classes = sorted({s["class_name"] for s in studs})
    cls = st.selectbox(L("Класс", "Сынып"), classes, key="exp_class")
    ids = {s["id"] for s in studs if s["class_name"] == cls}
    if st.button(L("Подготовить CSV", "CSV дайындау"), key="exp_prepare"):
        with st.spinner(L("Считаю портреты…", "Портреттерді есептеп жатырмын…")):
            st.session_state["export_csv"] = (cls, lang, privacy.export_class_csv(conn, lang, ids))
    ready = st.session_state.get("export_csv")
    if ready and ready[0] == cls and ready[1] == lang:
        data = ready[2]
        st.download_button(L("⬇️ Скачать CSV", "⬇️ CSV жүктеу"), data, file_name="portret_class_summary.csv",
                           mime="text/csv", type="primary", on_click=_log_export, args=(conn, user))
        st.caption(L(f"Строк: {data.decode('utf-8-sig').count(chr(10)) - 1}. Скачивание записывается в журнал действий.",
                     f"Жол саны: {data.decode('utf-8-sig').count(chr(10)) - 1}. Жүктеу әрекеттер журналына жазылады."))


# ---------------------------------------------------------------- согласия

def tab_consents(conn, user, L, lang):
    st.markdown("#### " + L("Согласие на анализ работ", "Жұмыстарды талдауға келісім"))
    st.caption(L("Живую работу нельзя записать в журнал без активного согласия. Вымышленные ученики демо отмечены "
                 "автоматически. Для настоящих — номер и дата бумажной формы родителя; скан не храним.",
                 "Белсенді келісімсіз тірі жұмысты журналға жазуға болмайды. Демодағы ойдан шығарылған оқушылар "
                 "автоматты түрде белгіленген. Нақты оқушыларға — ата-ана қағаз нысанының нөмірі мен күні; скан сақталмайды."))
    ids = auth.visible_student_ids(conn, user)
    rows = consent.status(conn, ids)
    if not rows:
        st.info(L("Нет учеников в ваших классах.", "Сыныптарыңызда оқушы жоқ."))
        return
    st.dataframe(pd.DataFrame([{
        L("Ученик", "Оқушы"): r["alias"], L("Класс", "Сынып"): r["class_name"],
        L("Согласие", "Келісім"): ("✅ " + L("есть", "бар")) if r["active"] else ("⛔ " + L("нет", "жоқ")),
        L("Основание", "Негіз"): consent.BASIS_NAMES[r["basis"]][lang] if r["basis"] else "",
        L("Форма", "Нысан"): r["reference"] or "",
        L("Дата", "Күні"): (r["revoked_at"] or r["given_at"] or "")[:16],
    } for r in rows]), hide_index=True, use_container_width=True)

    alias = {r["student_id"]: f"{r['alias']} · {r['class_name']}" for r in rows}
    c = st.columns([2, 2, 1, 1])
    sid = c[0].selectbox(L("Ученик", "Оқушы"), list(alias), format_func=lambda i: alias[i], key="cons_student")
    synthetic = consent.is_synthetic(conn, sid)
    ref = c[1].text_input(L("Номер и дата формы", "Нысанның нөмірі мен күні"), key="cons_ref",
                          placeholder=L("№ 12 от 01.09.2026", "№ 12, 01.09.2026"), max_chars=consent.MAX_REFERENCE,
                          disabled=synthetic,
                          help=L("Только реквизиты бумажной формы — без имён и без скана.",
                                 "Тек қағаз нысанының деректемелері — атсыз және сканссыз."))
    c[2].write("")
    c[3].write("")
    if c[2].button(L("Отметить", "Белгілеу"), key="cons_give", use_container_width=True):
        if not auth.can_see(conn, user, sid):
            st.error(L("Нет доступа.", "Қолжетімділік жоқ."))
            return
        try:
            consent.give(conn, sid, ref, user.get("id"), basis="synthetic" if synthetic else "parent_form")
        except ValueError:
            st.error(L("Укажите номер и дату бумажной формы (до 60 символов).",
                       "Қағаз нысанының нөмірі мен күнін көрсетіңіз (60 таңбаға дейін)."))
            return
        audit.log(conn, user, "consent_given", "student", sid)
        st.rerun()
    if c[3].button(L("Отозвать", "Қайтару"), key="cons_revoke", use_container_width=True):
        if auth.can_see(conn, user, sid) and consent.revoke(conn, sid):
            audit.log(conn, user, "consent_revoked", "student", sid)
        st.rerun()
    if synthetic:
        st.caption(L("Вымышленный ученик: согласие не требует формы. Отзовите его, чтобы показать, что запись без согласия "
                     "блокируется.", "Ойдан шығарылған оқушы: келісімге нысан керек емес. Келісімсіз жазу бұғатталатынын "
                     "көрсету үшін оны қайтарыңыз."))


# ---------------------------------------------------------------- администратор

def tab_users(conn, user, L, lang):
    st.markdown("#### " + L("Пользователи и классы учителей", "Пайдаланушылар және мұғалім сыныптары"))
    users = auth.list_users(conn)
    alias = {r["id"]: f"{r['alias']} · {r['class_name']}" for r in db.q(conn, "SELECT * FROM students ORDER BY class_name, id")}
    st.dataframe(pd.DataFrame([{
        L("Логин", "Логин"): u["login"], L("Имя в системе", "Жүйедегі аты"): u["display_name"],
        L("Роль", "Рөлі"): auth.ROLE_NAMES[u["role"]][lang],
        L("Классы", "Сыныптар"): ", ".join(u["classes"]),
        L("Ученик", "Оқушы"): alias.get(u["student_id"], "") if u["student_id"] else "",
        L("Активен", "Белсенді"): "—" if u["disabled"] else "✓",
    } for u in users]), hide_index=True, use_container_width=True)

    from core import roster  # классы: из таблицы classes и из списков учеников
    classes = roster.list_classes(conn)

    with st.expander(L("➕ Новый пользователь", "➕ Жаңа пайдаланушы")):
        with st.form("new_user", clear_on_submit=True):
            c = st.columns(3)
            login = c[0].text_input(L("Логин", "Логин"))
            name = c[1].text_input(L("Имя в системе", "Жүйедегі аты"), help=L("Для учеников — псевдоним.", "Оқушыларға — бүркеншік ат."))
            role = c[2].selectbox(L("Роль", "Рөлі"), list(auth.ROLES), index=1,
                                  format_func=lambda r: auth.ROLE_NAMES[r][lang])
            pw = st.text_input(L(f"Пароль (не короче {auth.MIN_PASSWORD} символов)", f"Құпиясөз (кемінде {auth.MIN_PASSWORD} таңба)"),
                               type="password")
            c = st.columns(2)
            cls = c[0].multiselect(L("Классы (для учителя)", "Сыныптар (мұғалімге)"), classes)
            sid = c[1].selectbox(L("Ученик (для роли «ученик»)", "Оқушы («оқушы» рөлі үшін)"), [None] + list(alias),
                                 format_func=lambda i: "—" if i is None else alias[i])
            if st.form_submit_button(L("Создать", "Құру"), type="primary"):
                if len(pw) < auth.MIN_PASSWORD:
                    st.error(L("Пароль слишком короткий.", "Құпиясөз тым қысқа."))
                elif role == "student" and sid is None:
                    st.error(L("Для роли «ученик» выберите ученика.", "«Оқушы» рөлі үшін оқушыны таңдаңыз."))
                elif not login.strip() or not name.strip():
                    st.error(L("Укажите логин и имя.", "Логин мен атты көрсетіңіз."))
                elif auth.get_user_by_login(conn, login):
                    st.error(L("Такой логин уже есть.", "Мұндай логин бар."))
                else:
                    uid = auth.create_user(conn, login, name, role, pw, student_id=sid,
                                           classes=cls if role == "teacher" else ())
                    audit.log(conn, user, "user_created", "user", uid)
                    st.success(L("Пользователь создан.", "Пайдаланушы құрылды."))

    if not users:
        return
    ulabel = {u["id"]: f"{u['login']} · {auth.ROLE_NAMES[u['role']][lang]}" for u in users}
    uid = st.selectbox(L("Изменить пользователя", "Пайдаланушыны өзгерту"), list(ulabel), format_func=lambda i: ulabel[i],
                       key="mu_user")
    u = next(x for x in users if x["id"] == uid)
    if u["role"] == "teacher":
        new_cls = st.multiselect(L("Классы учителя", "Мұғалім сыныптары"), sorted(set(classes) | set(u["classes"])),
                                 default=u["classes"], key=f"mu_cls_{uid}")
        if st.button(L("Сохранить классы", "Сыныптарды сақтау"), key="mu_save_cls"):
            auth.set_teacher_classes(conn, uid, new_cls)
            audit.log(conn, user, "classes_assigned", "user", uid)
            st.rerun()
    c = st.columns(2)
    pw = c[0].text_input(L("Новый пароль", "Жаңа құпиясөз"), type="password", key=f"mu_pw_{uid}")
    if c[0].button(L("Сменить пароль", "Құпиясөзді ауыстыру"), key="mu_pw_btn"):
        if len(pw) < auth.MIN_PASSWORD:
            st.error(L("Пароль слишком короткий.", "Құпиясөз тым қысқа."))
        else:
            auth.set_password(conn, uid, pw)
            audit.log(conn, user, "password_changed", "user", uid)
            st.success(L("Пароль изменён.", "Құпиясөз өзгертілді."))
    c[1].write("")
    if uid == user.get("id"):
        c[1].caption(L("Себя отключить нельзя.", "Өзіңізді өшіруге болмайды."))
    elif u["disabled"]:
        if c[1].button(L("Включить", "Қосу"), key="mu_enable"):
            auth.set_disabled(conn, uid, False)
            audit.log(conn, user, "user_enabled", "user", uid)
            st.rerun()
    elif c[1].button(L("Отключить", "Өшіру"), key="mu_disable"):
        auth.set_disabled(conn, uid, True)
        audit.log(conn, user, "user_disabled", "user", uid)
        st.rerun()


def tab_audit(conn, user, L, lang):
    st.markdown("#### " + L("Журнал действий — последние 200 записей", "Әрекеттер журналы — соңғы 200 жазба"))
    st.caption(L("В записи только коды действий и идентификаторы: без имён, без текста работ, без паролей и ключей.",
                 "Жазбада тек әрекет кодтары мен идентификаторлар: атсыз, жұмыс мәтінінсіз, құпиясөз бен кілтсіз."))
    logins = {u["id"]: u["login"] for u in auth.list_users(conn)}
    rows = audit.recent(conn, 200)
    st.dataframe(pd.DataFrame([{
        L("Время", "Уақыт"): r["at"], L("Пользователь", "Пайдаланушы"): str(logins.get(r["user_id"], r["user_id"] or "—")),
        L("Действие", "Әрекет"): audit.action_name(r["action"], lang),
        L("Объект", "Нысан"): r["target_type"] or "", "id": "" if r["target_id"] is None else str(r["target_id"]),
    } for r in rows]), hide_index=True, use_container_width=True, height=420)


def tab_delete(conn, user, L, lang):
    st.markdown("#### " + L("Удаление всех данных ученика", "Оқушының барлық деректерін жою"))
    st.caption(L("По запросу родителя или ученика: работы, строки решений, журнал наблюдений, комментарии, согласия и "
                 "данные других модулей (тренажёр, отметки, интервенции). Действие необратимо; в журнал действий "
                 "попадает только факт удаления и id.",
                 "Ата-ананың немесе оқушының сұрауы бойынша: жұмыстар, шешім жолдары, бақылау журналы, пікірлер, "
                 "келісімдер және басқа модульдердің деректері. Әрекет қайтымсыз; әрекеттер журналына тек жою фактісі мен id жазылады."))
    studs = _visible_students(conn, user)
    if not studs:
        st.info(L("Учеников нет.", "Оқушы жоқ."))
        return
    alias = {s["id"]: s for s in studs}
    sid = st.selectbox(L("Ученик", "Оқушы"), list(alias), format_func=lambda i: f"{alias[i]['alias']} · {alias[i]['class_name']}",
                       key="del_student")
    typed = st.text_input(L("Для подтверждения введите псевдоним ученика", "Растау үшін оқушының бүркеншік атын енгізіңіз"),
                          key=f"del_confirm_{sid}")
    ok = typed.strip() == alias[sid]["alias"]
    if st.button(L("🗑️ Удалить данные ученика", "🗑️ Оқушы деректерін жою"), disabled=not ok, type="primary", key="del_btn"):
        counts = privacy.delete_student(conn, sid, user)
        _forget_student(sid)
        st.session_state["del_done"] = counts
        st.rerun()
    done = st.session_state.pop("del_done", None)
    if done is not None:
        st.success(L("Удалено: ", "Жойылды: ") + ", ".join(f"{t} — {n}" for t, n in done.items()))


# ---------------------------------------------------------------- страница

def render(conn, user, L, lang):
    st.title(L("🛡️ Управление", "🛡️ Басқару"))
    role = user["role"]
    if role not in ("teacher", "admin"):
        st.error(L("Нет доступа.", "Қолжетімділік жоқ."))
        return
    tabs = {"export": L("Выгрузка итогов", "Қорытындыны түсіру"), "consents": L("Согласия", "Келісімдер"),
            "assignments": L("Задания", "Тапсырмалар")}
    if role == "admin":
        tabs.update({"classes": L("Классы", "Сыныптар"), "users": L("Пользователи", "Пайдаланушылар"),
                     "audit": L("Журнал действий", "Әрекеттер журналы"), "delete": L("Удаление данных", "Деректерді жою")})
    for key, tab in zip(tabs, st.tabs(list(tabs.values()))):
        with tab:
            if key == "export":
                tab_export(conn, user, L, lang)
            elif key == "consents":
                tab_consents(conn, user, L, lang)
            elif key == "assignments":
                from ui import assignments
                assignments.render(conn, user, L, lang)
            elif key == "classes":
                from ui import classes
                classes.render(conn, user, L, lang)
            elif key == "users":
                tab_users(conn, user, L, lang)
            elif key == "audit":
                tab_audit(conn, user, L, lang)
            elif key == "delete":
                tab_delete(conn, user, L, lang)

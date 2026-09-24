"""Вкладка «Классы» страницы «Управление» (администратор): создать класс,
импортировать список учеников из CSV, назначить учителей классу."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import audit, auth, db, roster


def render(conn, user, L, lang):
    if user["role"] != "admin":
        st.error(L("Нет доступа.", "Қолжетімділік жоқ."))
        return
    classes = roster.list_classes(conn)
    counts = {r["class_name"]: r["n"] for r in db.q(conn, "SELECT class_name, COUNT(*) n FROM students GROUP BY class_name")}
    teachers = [u for u in auth.list_users(conn) if u["role"] == "teacher"]
    st.markdown("#### " + L("Классы", "Сыныптар"))
    st.dataframe(pd.DataFrame([{
        L("Класс", "Сынып"): c, L("Учеников", "Оқушы"): counts.get(c, 0),
        L("Учителя", "Мұғалімдер"): ", ".join(u["display_name"] for u in teachers if c in u["classes"]),
    } for c in classes]), hide_index=True, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**" + L("Создать класс", "Сынып құру") + "**")
        with st.form("new_class", clear_on_submit=True):
            name = st.text_input(L("Название, например 9 «Б»", "Атауы, мысалы 9 «Б»"), max_chars=roster.MAX_CLASS)
            if st.form_submit_button(L("Создать", "Құру")):
                err = roster.class_error(name)
                if err:
                    st.error(err[lang])
                elif name.strip() in classes:
                    st.warning(L("Такой класс уже есть.", "Мұндай сынып бар."))
                else:
                    roster.create_class(conn, name)
                    audit.log(conn, user, "class_created", "class", None)
                    st.rerun()
    with c2:
        st.markdown("**" + L("Учителя класса", "Сынып мұғалімдері") + "**")
        if not classes or not teachers:
            st.caption(L("Нужны класс и хотя бы один учитель.", "Сынып және кемінде бір мұғалім керек."))
        else:
            cls = st.selectbox(L("Класс", "Сынып"), classes, key="cls_teach_class")
            label = {u["id"]: f"{u['display_name']} ({u['login']})" for u in teachers}
            chosen = st.multiselect(L("Учителя", "Мұғалімдер"), list(label), format_func=lambda i: label[i],
                                    default=[i for i in roster.class_teachers(conn, cls) if i in label],
                                    key=f"cls_teach_{cls}")
            if st.button(L("Сохранить", "Сақтау"), key="cls_teach_save"):
                before = set(roster.class_teachers(conn, cls))
                roster.set_class_teachers(conn, cls, chosen)
                for uid in before ^ set(chosen):
                    audit.log(conn, user, "classes_assigned", "user", uid)
                st.rerun()

    st.markdown("#### " + L("Импорт списка учеников (CSV)", "Оқушылар тізімін импорттау (CSV)"))
    st.warning(L("В системе хранятся только псевдонимы. Не загружайте ФИО: соответствие «псевдоним — ученик» "
                 "держите у себя, вне системы.",
                 "Жүйеде тек бүркеншік аттар сақталады. Аты-жөнін жүктемеңіз: «бүркеншік ат — оқушы» сәйкестігін "
                 "жүйеден тыс, өзіңізде сақтаңыз."))
    st.caption(L("Формат: первая строка alias,class_name; далее по ученику в строке. Разделитель — запятая или "
                 "точка с запятой, кодировка UTF-8.",
                 "Пішімі: бірінші жол alias,class_name; әрі қарай әр жолда бір оқушы. Бөлгіш — үтір немесе "
                 "нүктелі үтір, кодтауы UTF-8."))
    st.code("alias,class_name\nЛис,9 «Б»\nСокол,9 «Б»", language="text")
    _done(L)
    up = st.file_uploader(L("Файл CSV", "CSV файлы"), type=["csv"], key=f"roster_csv_{st.session_state.get('roster_ver', 0)}")
    if up is None:
        return
    rows, errors, warnings = roster.parse_students_csv(up.getvalue())
    dup = roster.existing_duplicates(conn, rows)
    for e in errors:
        st.error(e[lang])
    for d in dup:
        st.error(L(f"«{d['alias']}» уже есть в классе {d['class_name']}.", f"«{d['alias']}» {d['class_name']} сыныбында бар."))
    for w in warnings:
        st.warning(w[lang])
    if rows:
        st.dataframe(pd.DataFrame(rows).rename(columns={"alias": L("Псевдоним", "Бүркеншік ат"),
                                                        "class_name": L("Класс", "Сынып")}),
                     hide_index=True, use_container_width=True)
    ok = bool(rows) and not errors and not dup
    if st.button(L(f"Импортировать {len(rows)}", f"{len(rows)} импорттау"), disabled=not ok, type="primary",
                 key="roster_import"):
        ids = roster.import_students(conn, rows)
        audit.log(conn, user, "students_imported")
        st.session_state["roster_ver"] = st.session_state.get("roster_ver", 0) + 1  # очистить загрузчик
        st.session_state["roster_done"] = len(ids)
        st.rerun()


def _done(L):
    n = st.session_state.pop("roster_done", None)
    if n is not None:
        st.success(L(f"Добавлено учеников: {n}. Отметьте согласия на вкладке «Согласия».",
                     f"Қосылған оқушылар: {n}. Келісімдерді «Келісімдер» қойындысында белгілеңіз."))

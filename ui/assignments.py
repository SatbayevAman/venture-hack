"""Вкладка «Задания» страницы «Управление»: учитель создаёт ДЗ.

Эталон каждой задачи перед сохранением проходит ту же проверку SymPy, что
и работы учеников: без ошибок и с верным ответом. Иначе задание не
сохраняется, и учитель видит, в какой строке эталона проблема.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

from core import audit, db, roster
from core import tags as T

MAX_PROBLEMS = 8


def render(conn, user, L, lang):
    if user["role"] not in ("teacher", "admin"):
        st.error(L("Нет доступа.", "Қолжетімділік жоқ."))
        return
    ss = st.session_state
    rows = db.q(conn, """SELECT a.number, a.title, a.due_at, COUNT(p.id) n FROM assignments a
                         LEFT JOIN problems p ON p.assignment_id = a.id GROUP BY a.id ORDER BY a.number DESC""")
    st.markdown("#### " + L("Задания", "Тапсырмалар"))
    st.dataframe(pd.DataFrame([{"№": r["number"], L("Название", "Атауы"): r["title"],
                                L("Срок", "Мерзімі"): r["due_at"][:16], L("Задач", "Есеп"): r["n"]} for r in rows]),
                 hide_index=True, use_container_width=True, height=min(250, 38 + 35 * len(rows)))
    done = ss.pop("asg_done", None)
    if done:
        st.success(L(f"ДЗ №{done} сохранено: все эталоны прошли проверку.", f"№{done} ҮТ сақталды: барлық эталон тексеруден өтті."))

    number = roster.next_number(conn)
    ver = ss.get("asg_ver", 0)
    st.markdown("#### " + L(f"Новое задание — ДЗ №{number}", f"Жаңа тапсырма — №{number} ҮТ"))
    st.caption(L("Перед сохранением эталон каждой задачи проходит ту же проверку SymPy, что и работы учеников. "
                 "Задание видно во всех классах.",
                 "Сақтамас бұрын әр есептің эталоны оқушылардың жұмыстары сияқты SymPy тексеруінен өтеді. "
                 "Тапсырма барлық сыныпқа көрінеді."))
    c = st.columns([3, 2, 1, 1])
    title = c[0].text_input(L("Название", "Атауы"), value=L(f"ДЗ №{number}", f"№{number} ҮТ"), key=f"asg_title_{ver}")
    due_d = c[1].date_input(L("Срок", "Мерзімі"), value=date.today() + timedelta(days=7), key=f"asg_due_{ver}")
    due_t = c[2].time_input(L("до", "дейін"), value=time(23, 59), key=f"asg_time_{ver}")
    n = c[3].number_input(L("Задач", "Есеп"), min_value=1, max_value=MAX_PROBLEMS, value=2, key=f"asg_n_{ver}")
    kinds = roster.problem_kinds()
    kind_names = {k: v[lang] for k, v in T.KIND_NAMES.items()}
    problems = []
    for i in range(1, int(n) + 1):
        with st.container(border=True):
            st.markdown(f"**№{i}**")
            a = st.columns([3, 1, 2])
            statement = a[0].text_input(L("Условие", "Шарты"), key=f"asg_{ver}_st_{i}",
                                        placeholder="x² − 5x + 6 = 0")
            kind = a[1].selectbox(L("Вид", "Түрі"), kinds, key=f"asg_{ver}_kind_{i}",
                                  format_func=lambda k: kind_names.get(k, k))
            skill = a[2].selectbox(L("Навык", "Дағды"), list(T.SKILLS), key=f"asg_{ver}_skill_{i}",
                                   format_func=lambda s: T.skill_name(s, lang))
            b = st.columns([3, 1])
            ref = b[0].text_area(L("Эталонное решение — по строке на шаг, без строки «Ответ»",
                                   "Эталон шешім — әр қадам бір жолда, «Жауап» жолынсыз"),
                                 key=f"asg_{ver}_ref_{i}", height=110,
                                 placeholder="D = 25 − 24 = 1\nx₁ = (5 + 1)/2 = 3\nx₂ = (5 − 1)/2 = 2")
            answer = b[1].text_input(L("Ответ", "Жауап"), key=f"asg_{ver}_ans_{i}", placeholder="2; 3",
                                     help=L("Для уравнения — корни через «;».", "Теңдеу үшін — түбірлер «;» арқылы."))
            problems.append({"statement": statement, "kind": kind, "skill": skill,
                             "reference": ref.splitlines(), "answer": answer})
    if st.button(L("Проверить эталоны и сохранить", "Эталондарды тексеріп, сақтау"), type="primary", key="asg_save"):
        due = datetime.combine(due_d, due_t).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with st.spinner(L("Проверяю эталоны…", "Эталондарды тексеріп жатырмын…")):
                aid = roster.create_assignment(conn, title, due, problems, number=number)
        except ValueError as ex:
            errs = ex.args[0] if ex.args and isinstance(ex.args[0], list) else [{"ru": str(ex), "kk": str(ex)}]
            st.error(L("Задание не сохранено:", "Тапсырма сақталмады:") + "\n\n" + "\n".join(f"- {e[lang]}" for e in errs))
            return
        audit.log(conn, user, "assignment_created", "assignment", aid)
        ss["asg_ver"] = ver + 1  # чистая форма для следующего задания
        ss["asg_done"] = number
        st.rerun()

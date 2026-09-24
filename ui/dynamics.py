"""Интерфейс динамики: раздел «6. Динамика» в портрете, отметка «совет применён»,
стрелки и блок «Динамика класса» на карте класса. Логика — в core/knowledge.py
и core/interventions.py; здесь только показ."""
from __future__ import annotations

import html
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from core import interventions as I
from core import knowledge as K
from core import tags as T

ARROW = {"down": "▼", "up": "▲"}

STYLE = """
<style>
.dyn-strip {display:flex; flex-wrap:wrap; gap:4px; margin: 6px 0 8px 0;}
.dyn-cell {min-width: 2.6em; text-align:center; border-radius: 6px; padding: 2px 4px; font-size: .78rem; line-height: 1.25;}
.dyn-cell b {display:block; font-size: .95rem;}
.dyn-bad  {background: rgba(239,68,68,.16);}
.dyn-good {background: rgba(16,185,129,.14);}
.dyn-none {background: rgba(128,128,128,.10);}
.dyn-after {outline: 2px solid #8b5cf6; outline-offset: -2px;}
.dyn-line {font-size: .92rem; margin: 2px 0;}
.dyn-t-good {color: #059669; font-weight: 600;}
.dyn-t-warn {color: #d97706; font-weight: 600;}
.dyn-applied {font-size: .85rem; border-left: 3px solid #8b5cf6; padding: 3px 10px; margin: 4px 0;
              background: rgba(139,92,246,.08); border-radius: 6px;}
</style>
"""


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _pct(x: float) -> str:
    return f"{round(x * 100)} %"


def _date_short(d: str) -> str:
    """"2026-09-20" → "20.09"."""
    return f"{d[8:10]}.{d[5:7]}" if len(d) >= 10 else d


def _works_left(n: int, L) -> str:
    if n % 10 == 1 and n % 100 != 11:
        ru = f"{n} работу"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        ru = f"{n} работы"
    else:
        ru = f"{n} работ"
    return L(f"ждём ещё {ru}", f"тағы {n} жұмыс күтеміз")


def effect_text(eff: dict, L) -> str:
    """«эффект: ждём ещё 2 работы» или «до: 4/6 (67 %) → после: 1/3 (33 %)»."""
    if not eff.get("supported"):
        return L("эффект для этого совета не считается", "бұл кеңестің әсері есептелмейді")
    if not eff["ready"]:
        return L("эффект: ", "әсері: ") + _works_left(eff["need_more"], L)
    before = (f"{eff['e_before']}/{eff['n_before']} ({_pct(eff['p_before'])})" if eff["n_before"]
              else L("работ не было", "жұмыс болған жоқ"))
    after = f"{eff['e_after']}/{eff['n_after']} ({_pct(eff['p_after'])})"
    return L(f"до: {before} → после: {after}", f"дейін: {before} → кейін: {after}")


# ---------------------------------------------------------------- «совет применён»

def rec_controls(conn, student_id: int, rec: dict, L, lang: str, key: str) -> None:
    """Под рекомендацией: форма «Совет применён» или отметка с эффектом до/после."""
    mark = I.latest(conn, student_id, rec["key"])
    if mark:
        eff = I.effect(conn, student_id, rec["key"])
        note = f" · «{_esc(mark['note'])}»" if mark.get("note") else ""
        c1, c2 = st.columns([6, 1])
        c1.markdown(f'<div class="dyn-applied">✅ {L("Применён", "Қолданылды")} {_date_short(mark["applied_at"])}'
                    f' · {_esc(effect_text(eff, L))}{note}</div>', unsafe_allow_html=True)
        if c2.button(L("Отменить", "Болдырмау"), key=f"dyn_undo_{key}",
                     help=L("Убрать отметку «совет применён»", "«Кеңес қолданылды» белгісін алып тастау")):
            I.remove(conn, mark["id"])
            st.rerun()
        return
    with st.popover(L("✓ Совет применён", "✓ Кеңес қолданылды")):
        with st.form(key=f"dyn_form_{key}", border=False):
            d = st.date_input(L("Когда", "Қашан"), value=date.today(), format="DD.MM.YYYY")
            note = st.text_input(L("Заметка (необязательно)", "Жазба (міндетті емес)"),
                                 placeholder=L("например: разобрали на уроке разложение на множители",
                                               "мысалы: сабақта көбейткіштерге жіктеуді талдадық"))
            st.caption(L("Эффект появится, когда после этой даты накопится 3 работы.",
                         "Осы күннен кейін 3 жұмыс жиналғанда әсері көрінеді."))
            if st.form_submit_button(L("Отметить", "Белгілеу")):
                I.mark_applied(conn, student_id, rec["key"], d.isoformat(), note)
                st.rerun()


# ---------------------------------------------------------------- «6. Динамика»

def _strip(seq: list, key: str, hit_mark: str, miss_mark: str, hit_cls: str, miss_cls: str,
           applied_at: str | None, L) -> str:
    cells = []
    for s in seq:
        hit = s[key]
        after = " dyn-after" if applied_at and s["submitted_at"][:10] >= applied_at else ""
        cls = hit_cls if hit else miss_cls
        cells.append(f'<span class="dyn-cell {cls}{after}" title="{_esc(s["submitted_at"][:16])}">'
                     f'{L("ДЗ", "ҮТ")} №{s["work_no"]}<b>{hit_mark if hit else miss_mark}</b></span>')
    return '<div class="dyn-strip">' + "".join(cells) + "</div>"


def _trend_words(tr: str | None, habit: bool, L) -> str:
    if tr is None:
        return L("тренд: мало работ (нужно 4)", "үрдіс: жұмыс аз (4 керек)")
    if habit:
        words = {"down": L("▼ проверяет реже, чем в начале", "▼ басындағыдан сирек тексереді"),
                 "up": L("▲ проверяет чаще, чем в начале", "▲ басындағыдан жиі тексереді"),
                 "flat": L("без изменений", "өзгеріс жоқ")}
        cls = {"down": "dyn-t-warn", "up": "dyn-t-good"}  # проверяет чаще — хорошо
    else:
        words = {"down": L("▼ ошибок меньше, чем в начале", "▼ басындағыдан қате аз"),
                 "up": L("▲ ошибок больше, чем в начале", "▲ басындағыдан қате көп"),
                 "flat": L("без изменений", "өзгеріс жоқ")}
        cls = {"down": "dyn-t-good", "up": "dyn-t-warn"}  # ▼ — хорошо, не красным
    c = cls.get(tr)
    return f'<span class="{c}">{words[tr]}</span>' if c else words[tr]


def _share_line(seq: list, habit: bool, L) -> str:
    v = K.values(seq)
    e, n = sum(v), len(v)
    head = L("доля работ с проверкой", "тексерілген жұмыс үлесі") if habit else L("доля ошибок", "қате үлесі")
    if not n:
        return head + ": —"
    w = K.wilson(e, n)
    base = f"{head}: {e}/{n} = {_pct(e / n)}"
    if w is None:
        return base + " · " + L("мало данных для интервала", "аралыққа дерек аз")
    return base + L(f" (80 % интервал: {_pct(w[0])}–{_pct(w[1])})", f" (80 % аралық: {_pct(w[0])}–{_pct(w[1])})")


def _chart(seq: list, traj: list | None, habit: bool, L):
    labels, seen = [], {}
    for s in seq:  # повторная сдача того же ДЗ — отдельная точка: «№7», «№7·2»
        seen[s["work_no"]] = seen.get(s["work_no"], 0) + 1
        labels.append(f"№{s['work_no']}" + (f"·{seen[s['work_no']]}" if seen[s["work_no"]] > 1 else ""))
    share_name = L("доля с проверкой", "тексеру үлесі") if habit else L("доля ошибок", "қате үлесі")
    rows = [{"work": lb, "i": i, "series": share_name, "value": v}
            for i, (lb, v) in enumerate(zip(labels, K.cumulative(seq)))]
    if traj:
        name = L("P(освоен)", "P(меңгерді)")
        rows += [{"work": lb, "i": i, "series": name, "value": v} for i, (lb, v) in enumerate(zip(labels, traj))]
    df = pd.DataFrame(rows)
    domain = [share_name] + ([L("P(освоен)", "P(меңгерді)")] if traj else [])
    return (alt.Chart(df).mark_line(point=True)
            .encode(x=alt.X("work:N", sort=labels, title=None, axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("value:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", tickCount=5),
                            title=None),
                    color=alt.Color("series:N", title=None, legend=alt.Legend(orient="top", direction="horizontal"),
                                    scale=alt.Scale(domain=domain, range=["#64748b", "#10b981"][:len(domain)])),
                    tooltip=["work", "series", alt.Tooltip("value:Q", format=".0%")])
            .properties(height=200))


def _card(title: str, badges: str, seq: list, habit: bool, traj: list | None, applied: dict | None,
          eff: dict | None, L) -> None:
    box = st.container(border=True)
    left, right = box.columns([3, 2])
    with left:
        st.markdown(f"**{_esc(title)}** {badges}", unsafe_allow_html=True)
        if habit:
            strip = _strip(seq, "hit", "✓", "—", "dyn-good", "dyn-none", applied and applied["applied_at"], L)
        else:
            strip = _strip(seq, "error", "✗", "✓", "dyn-bad", "dyn-good", applied and applied["applied_at"], L)
        lines = [strip,
                 f'<div class="dyn-line">{_esc(_share_line(seq, habit, L))}</div>',
                 f'<div class="dyn-line">{_trend_words(K.trend(seq), habit, L)}</div>']
        if traj:
            lines.append(f'<div class="dyn-line">{L("Вероятность, что навык освоен", "Дағдыны меңгеру ықтималдығы")}: '
                         f'{traj[0]:.2f} → {traj[-1]:.2f} '
                         f'<span class="muted">({L("после ДЗ", "ҮТ")} №{seq[0]["work_no"]} → '
                         f'№{seq[-1]["work_no"]}{L("", " кейін")})</span></div>')
        if applied:
            lines.append(f'<div class="dyn-applied">✅ {L("Совет применён", "Кеңес қолданылды")} '
                         f'{_date_short(applied["applied_at"])} · {_esc(effect_text(eff, L))}</div>')
        st.markdown("".join(lines), unsafe_allow_html=True)
    with right:
        if len(seq) >= 2:
            st.altair_chart(_chart(seq, traj, habit, L), use_container_width=True, height=250)


def render_section(conn, p: dict, L, lang: str) -> None:
    """Раздел «6. Динамика»: по каждой ошибке ученика (слабые места первыми) и по проверке ответа —
    полоска ✓✗ по работам, доля с интервалом Уилсона, тренд, вероятность освоения (BKT), график."""
    st.markdown(STYLE, unsafe_allow_html=True)
    st.header(L("6. Динамика", "6. Динамика"))
    st.caption(L("Работы от старых к новым. Интервал — 80 % по Уилсону: где, скорее всего, настоящая доля. "
                 "Тренд — сравнение первой и второй половины работ, только при 4 работах и больше. "
                 "Вероятность освоения — модель BKT (Corbett & Anderson): после каждой работы без этой ошибки "
                 "она растёт, после работы с ошибкой — падает; параметры подобраны по данным всего класса. "
                 "Фиолетовая рамка — работы после отметки «совет применён».",
                 "Жұмыстар ескіден жаңаға қарай. Аралық — Уилсон бойынша 80 %: нақты үлес шамамен осы жерде. "
                 "Үрдіс — жұмыстардың бірінші және екінші жартысын салыстыру, тек 4 және одан көп жұмыста. "
                 "Меңгеру ықтималдығы — BKT моделі (Corbett & Anderson): осы қатесіз әр жұмыстан кейін өседі, "
                 "қатесі бар жұмыстан кейін төмендейді; параметрлер бүкіл сынып деректері бойынша таңдалған. "
                 "Күлгін жиек — «кеңес қолданылды» белгісінен кейінгі жұмыстар."))
    sid = p["student"]["id"]
    if not p["errors"]:
        st.info(L("Ошибок в журнале нет — показываем только привычку проверять ответ.",
                  "Журналда қате жоқ — тек жауапты тексеру әдетін көрсетеміз."))
    for e in p["errors"]:
        seq = K.sequence(conn, sid, e["skill"], e["tag"])
        if not seq:
            continue
        traj = K.bkt_trajectory(seq, K.fit_for(conn, e["skill"], e["tag"]))
        key = f"{e['skill']}:{e['tag']}"
        applied = I.latest(conn, sid, key)
        eff = I.effect(conn, sid, key) if applied else None
        badges = ('<span class="badge b-weak">' + L("слабое место", "әлсіз тұс") + "</span>") if e["weak"] else (
            '<span class="badge b-low">' + L("мало данных", "дерек аз") + "</span>" if e["low_data"] else "")
        _card(f"{T.name(e['tag'], lang)} · {T.skill_name(e['skill'], lang).lower()}", badges, seq, False,
              traj, applied, eff, L)
    seq = K.habit_sequence(conn, sid, "check_done")
    if seq:
        applied = I.latest(conn, sid, "habit:check_done")
        eff = I.effect(conn, sid, "habit:check_done") if applied else None
        _card(T.name("check_done", lang), "", seq, True, None, applied, eff, L)


# ---------------------------------------------------------------- карта класса

def cell_text(cell: dict) -> str:
    """«4/6 ▼» — ячейка карты класса со стрелкой тренда."""
    if not cell["total"]:
        return "—"
    arrow = ARROW.get(cell.get("trend"))
    return f"{cell['bad']}/{cell['total']}" + (f" {arrow}" if arrow else "")


def legend(L) -> None:
    st.caption(L("▼ — ошибок стало меньше, ▲ — больше (сравнение первой и второй половины работ; "
                 "только при 4 работах и больше).",
                 "▼ — қате азайды, ▲ — көбейді (жұмыстардың бірінші және екінші жартысын салыстыру; "
                 "тек 4 және одан көп жұмыста)."))


def class_block(rows: list, L, lang: str) -> None:
    """«Динамика класса»: по каждому навыку — доля учеников с трендом ▼ и ▲."""
    st.subheader(L("Динамика класса", "Сынып динамикасы"))
    st.caption(L("Сколько учеников по каждому навыку ошибаются реже (▼) или чаще (▲), чем в начале. "
                 "В знаменателе — ученики, у кого по навыку 4 работы и больше.",
                 "Әр дағды бойынша қанша оқушы басындағыдан сирек (▼) немесе жиі (▲) қателеседі. "
                 "Бөлімінде — дағды бойынша 4 және одан көп жұмысы бар оқушылар."))
    out = []
    for sk in T.SKILLS:
        trends = [r["cells"][sk].get("trend") for r in rows if sk in r["cells"]]
        n = sum(1 for t in trends if t is not None)
        down, up = trends.count("down"), trends.count("up")
        fmt = (lambda k: f"{k}/{n} ({_pct(k / n)})") if n else (lambda k: "—")
        out.append({L("Навык", "Дағды"): T.skill_name(sk, lang),
                    L("▼ ошибок меньше", "▼ қате азайды"): fmt(down),
                    L("▲ ошибок больше", "▲ қате көбейді"): fmt(up),
                    L("без изменений", "өзгеріс жоқ"): fmt(n - down - up)})
    st.dataframe(pd.DataFrame(out), hide_index=True, use_container_width=True)

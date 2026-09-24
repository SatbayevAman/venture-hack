"""Портрет ученика — MVP (VENTUREHACK 2026, трек «Образование»).

Запуск:  streamlit run app.py
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core import audit, auth, consent, db, llm, pipeline, portrait, seed
from core import ocr, ocr_store
from core import tags as T
from core.tags import question_for, tag_comment_keywords
from ui import dynamics as dynamics_view
from ui import login as login_view, manage as manage_view
from ui import practice as practice_view
from ui import review as review_view

ROOT = Path(__file__).parent
DB_PATH = os.environ.get("PORTRET_DB", str(ROOT / "data" / "portret.db"))

st.set_page_config(page_title="Портрет ученика", page_icon="📐", layout="wide")
ss = st.session_state

# ------------------------------------------------------------------ стили

st.markdown("""
<style>
.block-container {padding-top: 2.2rem; max-width: 1200px;}
.card {border: 1px solid rgba(128,128,128,.28); border-radius: 12px; padding: 14px 16px; margin: 0 0 12px 0;}
.card h4 {margin: 0 0 4px 0; font-size: 1.05rem;}
.muted {opacity: .72; font-size: .9rem;}
.badge {display:inline-block; padding: 1px 9px; border-radius: 999px; font-size: .76rem; font-weight: 600;
        margin: 0 4px 0 0; vertical-align: middle; white-space: nowrap;}
.b-weak {background:#fde2e1; color:#9b1c1c;}
.b-conf {background:#dbeafe; color:#1e40af;}
.b-low  {background:#ececec; color:#4b5563;}
.b-ok   {background:#dcfce7; color:#166534;}
.b-syn  {background:#fef3c7; color:#92400e;}
.b-live {background:#ede9fe; color:#5b21b6;}
.lines {margin: 6px 0 4px 0;}
.ln {font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .92rem;
     padding: 4px 10px; border-radius: 6px; margin: 3px 0; display: flex; gap: 10px; align-items: baseline;}
.ln .no {opacity: .55; min-width: 1.6em; text-align: right;}
.ln .st {min-width: 1.2em; font-weight: 700;}
.ln .nt {opacity: .75; font-family: inherit; font-size: .8rem; margin-left: auto;}
.l-ok {background: rgba(16,185,129,.10);}
.l-error {background: rgba(239,68,68,.18); border-left: 4px solid #ef4444;}
.l-after {background: rgba(245,158,11,.10);}
.l-info {background: rgba(128,128,128,.07);}
.l-unparsed {background: rgba(147,51,234,.12);}
.l-stmt {background: transparent; opacity: .8; font-style: italic;}
.q {border-left: 4px solid #3b82f6; padding: 10px 14px; background: rgba(59,130,246,.09); border-radius: 8px; margin: 8px 0;}
.diff {border-left: 4px solid #8b5cf6; padding: 10px 14px; background: rgba(139,92,246,.09); border-radius: 8px; margin: 8px 0 14px 0;}
.adv {border-radius: 10px; padding: 10px 12px; background: rgba(128,128,128,.07); height: 100%;}
.adv b {display:block; margin-bottom: 4px;}
.ref {font-size: .82rem; opacity: .8; margin-top: 6px;}
.bar {height: 8px; border-radius: 4px; background: rgba(128,128,128,.18); overflow: hidden; margin: 3px 0 8px 0;}
.bar > div {height: 100%; background: #3b82f6;}
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ база

@st.cache_resource(show_spinner=False)
def get_conn():
    conn = db.connect(DB_PATH)
    has = db.q1(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='students'")
    if not has or not db.q1(conn, "SELECT 1 FROM students LIMIT 1"):
        seed.build(conn)
    else:
        db.init(conn)
    return conn


with st.spinner("Первый запуск: прогоняю синтетическую историю класса через проверку…"):
    conn = get_conn()


def reset_demo():
    get_conn().close()
    st.cache_resource.clear()
    try:
        os.remove(DB_PATH)
    except FileNotFoundError:
        pass
    for k in list(ss.keys()):
        if k not in ("lang",):
            del ss[k]


# ------------------------------------------------------------------ язык и навигация

if "lang" not in ss:
    ss["lang"] = "ru"
PAGES = ["class", "portrait", "check", "log", "quality", "manage", "practice", "about"]

lang = ss["lang"]  # значение радиокнопки уже в session_state до её отрисовки

with st.sidebar:
    st.markdown("### 📐 " + ("Оқушы портреті" if lang == "kk" else "Портрет ученика"))
    st.caption("MVP · VENTUREHACK 2026 · " + ("«Білім беру» трегі" if lang == "kk" else "трек «Образование»"))
    st.radio("Язык / Тіл", ["ru", "kk"], key="lang", horizontal=True,
             format_func=lambda x: {"ru": "Русский", "kk": "Қазақша"}[x])


def L(ru: str, kk: str) -> str:
    return kk if lang == "kk" else ru


def sticky(name: str, default):
    """Ключ виджета зависит от языка (подписи меняются), а значение общее для обоих языков.
    Программно выставить значение: ss[f"set_{name}"] = … и st.rerun()."""
    wkey = f"w_{name}_{lang}"
    if f"set_{name}" in ss:
        ss[name] = ss.pop(f"set_{name}")
        ss[wkey] = ss[name]
    if wkey not in ss or ss.get(name) is not None and ss[wkey] != ss[name] and ss.get("_last_lang") != lang:
        ss[wkey] = ss.get(name, default)
    return wkey


def remember(name: str, value):
    ss[name] = value
    return value


user = login_view.gate(conn, L)          # без входа рисует форму и вызывает st.stop()
PAGES = auth.allowed_pages(user, PAGES)
is_admin = user["role"] == "admin"
REVIEWER = str(user["id"]) if user.get("id") is not None else None  # отметки учителя — с id пользователя (Дзета B5)

PAGE_NAMES = {
    "class": L("🏫 Карта класса", "🏫 Сынып картасы"),
    "portrait": L("👤 Портрет ученика", "👤 Оқушы портреті"),
    "check": L("📷 Проверка работы", "📷 Жұмысты тексеру"),
    "log": L("📒 Журнал наблюдений", "📒 Бақылау журналы"),
    "quality": L("📈 Качество", "📈 Сапа"),
    "manage": L("🛡️ Управление", "🛡️ Басқару"),
    "practice": L("🧩 Тренажёр", "🧩 Жаттықтырғыш"),
    "about": L("⚙️ Как это работает", "⚙️ Бұл қалай жұмыс істейді"),
}

if ss.get("page") not in PAGES:  # раздел недоступен этой роли
    ss.pop("page", None)
    for _k in [k for k in ss if str(k).startswith("w_page_")]:
        del ss[_k]

with st.sidebar:
    page = remember("page", st.radio(L("Раздел", "Бөлім"), PAGES, key=sticky("page", PAGES[0]),
                                     format_func=lambda p: PAGE_NAMES[p], label_visibility="collapsed"))
    st.divider()

    # --- языковая модель
    try:
        secrets = dict(st.secrets)
    except Exception:  # noqa: BLE001 — secrets.toml может отсутствовать
        secrets = {}
    env_cfg = llm.config_from_env(secrets)
    if "llm_cfg" not in ss:
        ss["llm_cfg"] = env_cfg
    cfg: llm.LLMConfig = ss["llm_cfg"]
    with st.expander(L("🤖 Языковая модель", "🤖 Тілдік модель") + (" ✅" if cfg.ready else " ⚪")):
        st.caption(L("Нужна только для распознавания фото, разметки комментариев и формулировок. "
                     "Без неё: строки вводятся текстом, комментарии размечаются по словарю.",
                     "Тек фотоны тану, пікірлерді белгілеу және тұжырымдау үшін керек. "
                     "Онсыз: жолдар мәтінмен енгізіледі, пікірлер сөздік бойынша белгіленеді."))
        if not is_admin:  # ключ живёт только на сервере (st.secrets / окружение); учитель его не видит
            st.markdown(L("Статус: ", "Күйі: ") + (L("подключена", "қосылған") if cfg.ready else L("не подключена", "қосылмаған")))
        else:
            prov = st.selectbox(L("Провайдер", "Провайдер"), ["anthropic", "openai"],
                                index=0 if cfg.provider != "openai" else 1,
                                help="openai = любой OpenAI-совместимый API (OpenAI, Gemini, OpenRouter)")
            # значение ключа в браузер не отправляем: пустое поле = оставить ключ сервера
            key = st.text_input("API key", value="", type="password",
                                placeholder=L("задан на сервере", "серверде берілген") if cfg.api_key else "")
            model = st.text_input(L("Модель", "Модель"), value=cfg.model or llm.DEFAULT_MODELS[prov])
            base = st.text_input("Base URL", value=cfg.base_url, disabled=prov != "openai")
            if st.button(L("Применить", "Қолдану"), use_container_width=True):
                ss["llm_cfg"] = llm.LLMConfig(prov, key.strip() or cfg.api_key, model.strip(), base.strip())
                st.rerun()
            st.caption(L("Сейчас: ", "Қазір: ") + cfg.label())

    st.divider()
    st.markdown('<span class="badge b-syn">' + L("данные синтетические", "синтетикалық деректер") + "</span>",
                unsafe_allow_html=True)
    st.caption(L("8 вымышленных учеников × 6 работ. Строки решений сгенерированы, но все выводы "
                 "в журнал записала настоящая проверка SymPy.",
                 "8 ойдан шығарылған оқушы × 6 жұмыс. Шешім жолдары генерацияланған, бірақ "
                 "журналдағы барлық қорытындыны нақты SymPy тексеруі жазды."))
    if is_admin and st.button(L("↺ Сбросить демо-данные", "↺ Демоны қайта бастау"), use_container_width=True):
        reset_demo()
        st.rerun()


# ------------------------------------------------------------------ общие помощники

def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{esc(text)}</span>'


STATUS_ICON = {"ok": "✓", "error": "✗", "after": "→", "info": "·", "unparsed": "?"}


def status_note(kind: str, status: str, note: str) -> str:
    if status == "after":
        return L("верно относительно ошибочной строки", "қате жолға қатысты дұрыс")
    if status == "unparsed":
        return L("не разобрано — поправьте текст", "танылмады — мәтінді түзетіңіз")
    out = T.LINE_KINDS.get(kind, {}).get(lang, "")
    if note and note.startswith("✓"):
        out = (out + " ✓").strip()
    elif note and note.startswith("✗"):
        out = (out + " ✗").strip()
    return out


def render_lines(lines, statement: str | None = None, highlight: int | None = None) -> str:
    """lines: список dict/объектов с no, raw, status, kind, note."""
    out = ['<div class="lines">']
    if statement:
        out.append(f'<div class="ln l-stmt"><span class="no">0</span><span class="st"></span>'
                   f'<span>{esc(statement)}</span><span class="nt">{L("условие", "шарт")}</span></div>')
    for ln in lines:
        if hasattr(ln, "raw"):  # результат проверки (LineResult)
            no, raw, stt, kind, note = ln.no, ln.raw, ln.status, ln.kind, ln.note
        else:  # строка таблицы steps
            no, raw, stt, kind, note = ln["line_no"], ln["raw_text"], ln["status"], ln["kind"], ln["note"]
        cls = f"l-{stt}" if stt in ("ok", "error", "after", "info", "unparsed") else "l-info"
        mark = " ◀" if highlight and no == highlight else ""
        out.append(f'<div class="ln {cls}"><span class="no">{no}</span><span class="st">{STATUS_ICON.get(stt, "")}</span>'
                   f'<span>{esc(raw)}{mark}</span><span class="nt">{esc(status_note(kind, stt, note or ""))}</span></div>')
    out.append("</div>")
    return "".join(out)


def steps_of(sub_id: int, problem_id: int) -> list:
    return db.q(conn, "SELECT * FROM steps WHERE submission_id=? AND problem_id=? ORDER BY line_no",
                (sub_id, problem_id))


def ref_label(r: dict) -> str:
    parts = [f"{L('ДЗ', 'ҮТ')} №{r['work_no']}"] if r.get("work_no") else []
    if r.get("problem_idx"):
        parts.append(f"{L('задача', 'есеп')} {r['problem_idx']}")
    if r.get("line_no"):
        parts.append(f"{L('строка', 'жол')} {r['line_no']}")
    return " · ".join(parts)


def show_ref_work(r: dict):
    """Показать строки той задачи, на которую ссылается вывод портрета."""
    if not r.get("sub_id") or not r.get("problem_idx"):
        st.markdown(f"- {esc(ref_label(r))}: <code>{esc(r.get('evidence'))}</code>", unsafe_allow_html=True)
        return
    prob = db.q1(conn, """SELECT p.* FROM problems p JOIN submissions s ON s.assignment_id = p.assignment_id
                          WHERE s.id=? AND p.idx=?""", (r["sub_id"], r["problem_idx"]))
    st.markdown(f"**{esc(ref_label(r))}**", unsafe_allow_html=True)
    st.markdown(render_lines(steps_of(r["sub_id"], prob["id"]), prob["statement"], r.get("line_no")),
                unsafe_allow_html=True)


def students():
    ids = auth.visible_student_ids(conn, user)
    return [s for s in db.q(conn, "SELECT * FROM students ORDER BY id") if ids is None or s["id"] in ids]


def visible_filter(col: str) -> tuple[str, list]:
    """SQL-условие видимости « AND col IN (…)» для всех, кроме администратора."""
    ids = auth.visible_student_ids(conn, user)
    if ids is None:
        return "", []
    return f" AND {col} IN ({','.join('?' * len(ids)) or 'NULL'})", sorted(ids)


def no_students() -> bool:
    if students():
        return False
    st.info(L("В ваших классах пока нет учеников. Классы назначает администратор.",
              "Сыныптарыңызда әзірге оқушы жоқ. Сыныптарды әкімші тағайындайды."))
    return True


def llm_allowed(ru: str, kk: str) -> bool:
    """Лимит обращений к модели на пользователя (auth.rate_limit, Дзета B4). При отказе — предупреждение, вызова нет."""
    if auth.rate_limit(user, "llm"):
        return True
    n = auth.RATE_LIMITS["llm"][0]
    st.warning(L(f"Лимит обращений к модели: {n} в час. ", f"Модельге жүгіну шегі: сағатына {n}. ") + L(ru, kk))
    return False


def go(page_key: str, student_id: int | None = None):
    ss["set_page"] = page_key
    if student_id is not None:
        ss["set_student_id"] = student_id
    st.rerun()


def heat(p: float) -> str:
    """0 → зелёный, ≥ 0.6 → красный."""
    x = min(max(p / 0.6, 0), 1)
    hue = int(135 - 135 * x)
    return f"background-color: hsl({hue}, 70%, 86%); color: #1f2937;"


# ------------------------------------------------------------------ страницы

def page_class():
    if no_students():
        return
    classes = sorted({s["class_name"] for s in students()})
    cls = classes[0]
    if len(classes) > 1:
        cls = remember("class_name", st.selectbox(L("Класс", "Сынып"), classes, key=sticky("class_name", classes[0])))
    class_ids = {s["id"] for s in students() if s["class_name"] == cls}
    st.title(L(f"Карта класса {cls}", f"{cls} сыныбының картасы"))
    st.caption(L("Доля работ с ошибками по каждому навыку; свежие работы весят больше. "
                 "Нажмите на строку, чтобы открыть портрет ученика.",
                 "Әр дағды бойынша қатесі бар жұмыстардың үлесі; жаңа жұмыстардың салмағы көбірек. "
                 "Оқушы портретін ашу үшін жолды басыңыз."))
    vis, args = f" AND student_id IN ({','.join('?' * len(class_ids))})", sorted(class_ids)
    n_sub = db.q1(conn, "SELECT COUNT(*) c FROM submissions WHERE 1=1" + vis, args)["c"]
    n_obs = db.q1(conn, "SELECT COUNT(*) c FROM observations WHERE 1=1" + vis, args)["c"]
    n_com = db.q1(conn, "SELECT COUNT(*) c FROM teacher_comments tc JOIN submissions s ON s.id = tc.submission_id WHERE 1=1"
                  + vis.replace("student_id", "s.student_id"), args)["c"]
    rows = portrait.class_map(conn, lang, student_ids=class_ids)
    c = st.columns(4)
    c[0].metric(L("Учеников", "Оқушы"), len(rows))
    c[1].metric(L("Работ проверено", "Тексерілген жұмыс"), n_sub)
    c[2].metric(L("Наблюдений в журнале", "Журналдағы бақылау"), n_obs)
    c[3].metric(L("Комментариев учителя", "Мұғалім пікірі"), n_com)

    skills = [s for s in T.SKILLS if any(r["cells"][s]["total"] for r in rows)] or list(T.SKILLS)
    disp, colors = [], []
    for r in rows:
        d = {L("Ученик", "Оқушы"): r["alias"]}
        col = {L("Ученик", "Оқушы"): ""}
        for sk in skills:
            cell = r["cells"][sk]
            name = T.SKILLS_SHORT[sk][lang]
            d[name] = dynamics_view.cell_text(cell)  # «4/6 ▼»: стрелка тренда, если он есть
            col[name] = heat(cell["p"]) if cell["total"] else ""
        chk, n = r["check"]
        d[L("Проверка ответа", "Жауапты тексеру")] = f"{chk}/{n}"
        col[L("Проверка ответа", "Жауапты тексеру")] = heat(0.6 * (1 - chk / n)) if n else ""
        d[L("Опоздания", "Кешігу")] = r["late"]
        col[L("Опоздания", "Кешігу")] = heat(0.3 * r["late"]) if r["late"] else ""
        main = r["top"] if r["top"] else (
            L("наблюдаем: ", "бақылаймыз: ") + r["watch"] + L(" · мало данных", " · дерек аз") if r["watch"] else
            L("устойчивых проблем нет", "тұрақты қиындық жоқ"))
        d[L("Главное", "Басты")] = main
        col[L("Главное", "Басты")] = ""
        disp.append(d)
        colors.append(col)
    df = pd.DataFrame(disp)
    cdf = pd.DataFrame(colors)
    styler = df.style.apply(lambda _: cdf, axis=None)
    key = f"classmap_{ss.get('cm_ver', 0)}"
    ev = st.dataframe(styler, hide_index=True, use_container_width=True, on_select="rerun",
                      selection_mode="single-row", key=key,
                      column_config={L("Главное", "Басты"): st.column_config.TextColumn(width="large")})
    sel = getattr(getattr(ev, "selection", None), "rows", None) if ev is not None else None
    if sel:
        ss["cm_ver"] = ss.get("cm_ver", 0) + 1
        go("portrait", rows[sel[0]]["id"])
    st.caption(L("Цвет: зелёный — ошибок почти нет, красный — ошибка в 60 % и более свежих работ. "
                 "«Проверка ответа» окрашена тем краснее, чем реже ученик проверяет.",
                 "Түс: жасыл — қате жоқтың қасы, қызыл — жаңа жұмыстардың 60 %-ынан көбінде қате. "
                 "«Жауапты тексеру» оқушы неғұрлым сирек тексерсе, соғұрлым қызыл."))
    dynamics_view.legend(L)
    dynamics_view.class_block(rows, L, lang)

    # что повторить со всем классом
    common = db.q(conn, """SELECT tag, skill, COUNT(DISTINCT student_id) n, COUNT(*) c FROM observations
                           WHERE source='auto' AND kind='error' AND tag != 'other'""" + vis + """
                           GROUP BY tag, skill HAVING n >= 2 ORDER BY c DESC, n DESC""", args)
    if common:
        st.subheader(L("Что повторить со всем классом", "Бүкіл сыныппен не қайталау керек"))
        st.caption(L("Ошибки, которые встречаются у двух и более учеников, по числу случаев.",
                     "Екі және одан көп оқушыда кездесетін қателер, жағдай саны бойынша."))
        items = [f"**{T.name(r['tag'], lang)}** · {T.skill_name(r['skill'], lang).lower()} — "
                 + L(f"{r['c']} случаев у {r['n']} учеников", f"{r['n']} оқушыда {r['c']} жағдай") for r in common[:4]]
        st.markdown("\n".join(f"- {i}" for i in items))


def page_portrait():
    if no_students():
        return
    studs = students()
    alias = {s["id"]: s["alias"] for s in studs}
    if user["role"] == "student":  # ученик — только свой портрет и только мягкая версия, без выбора
        sid, audience = user["student_id"], "student"
    else:
        if ss.get("student_id") not in alias:
            ss["student_id"] = studs[0]["id"]
        top = st.columns([2, 2, 3])
        sid = remember("student_id", top[0].selectbox(L("Ученик", "Оқушы"), list(alias), key=sticky("student_id", studs[0]["id"]),
                                                      format_func=lambda i: alias[i]))
        audience = top[1].radio(L("Версия", "Нұсқа"), ["teacher", "student"], horizontal=True, key=sticky("audience", "teacher"),
                                format_func=lambda a: {"teacher": L("для учителя", "мұғалімге"),
                                                       "student": L("для ученика", "оқушыға")}[a])
        remember("audience", audience)
    if ss.get("_audit_view") != (sid, audience):  # одна строка аудита на просмотр, а не на каждую перерисовку
        audit.log(conn, user, "view_portrait", "student", sid)
        ss["_audit_view"] = (sid, audience)
    p = portrait.build(conn, sid, lang)
    st.title(L(f"Портрет: {p['student']['alias']}", f"Портрет: {p['student']['alias']}"))
    live = badge(L("есть живая проверка", "тірі тексеру бар"), "b-live") if p["has_live"] else ""
    st.markdown(f'<span class="muted">{esc(p["student"]["class_name"])} · '
                + L(f"{p['n_works']} работ · {p['n_obs']} наблюдений в журнале",
                    f"{p['n_works']} жұмыс · журналда {p['n_obs']} бақылау")
                + f"</span> {badge(L('синтетические данные', 'синтетикалық деректер'), 'b-syn')}{live}",
                unsafe_allow_html=True)

    d = ss.get("diff")
    if d and d.get("sid") == sid and d.get("lines"):
        st.markdown('<div class="diff"><b>' + L("Портрет обновился после новой работы:", "Жаңа жұмыстан кейін портрет жаңарды:")
                    + "</b><br>" + "<br>".join(esc(x) for x in d["lines"]) + "</div>", unsafe_allow_html=True)

    if audience == "student":
        portrait_student(p)
    else:
        portrait_teacher(p)


def portrait_teacher(p: dict):
    weak = [e for e in p["errors"] if e["weak"]]
    c = st.columns(4)
    c[0].metric(L("Слабых мест", "Әлсіз тұс"), len(weak))
    c[1].metric(L("Проверяет ответ", "Жауапты тексереді"), f"{p['habits']['check_done']['count']}/{p['n_works']}")
    c[2].metric(L("Сдано после срока", "Кеш тапсырылды"), p["habits"]["late"]["count"])
    c[3].metric(L("Комментариев учителя", "Мұғалім пікірі"), sum(t["count"] for t in p["teacher"]))

    # 1. Где слаб
    st.header(L("1. Где слаб(а)", "1. Әлсіз тұстары"))
    if not weak:
        st.info(L("Устойчивых слабых мест не найдено (порог: не меньше 3 случаев и не меньше 40 % работ).",
                  "Тұрақты әлсіз тұс табылған жоқ (шегі: кемінде 3 жағдай және жұмыстардың кемінде 40 %-ы)."))
    for e in weak:
        t = T.name(e["tag"], lang)
        badges = badge(L("слабое место", "әлсіз тұс"), "b-weak")
        if e["confirmed"]:
            badges += badge(L("подтверждено дважды: работы + учитель", "екі рет расталды: жұмыс + мұғалім"), "b-conf")
        badges += review_view.precision_badge(conn, e["tag"], L, auth.visible_student_ids(conn, user))
        det = f" ({esc(e['detail'])})" if e["detail"] else ""
        st.markdown(f'<div class="card"><h4>{esc(t)} · {esc(T.skill_name(e["skill"], lang).lower())} {badges}</h4>'
                    f'{esc(portrait.share_text(e["count"], e["total"], lang))}{det}. '
                    f'<span class="muted">{L("Взвешенная доля", "Салмақталған үлес")}: {round(e["p"] * 100)} %</span></div>',
                    unsafe_allow_html=True)
        with st.expander(L(f"Доказательства ({len(e['refs'])}): работа, строка", f"Дәлелдер ({len(e['refs'])}): жұмыс, жол")):
            for r in e["refs"]:
                show_ref_work(r)
                review_view.controls(conn, r.get("obs_id"), L, key=f"rv_{p['student']['id']}_{r.get('obs_id')}",
                                     reviewer=REVIEWER)
    low = [e for e in p["errors"] if not e["weak"]]
    if low:
        st.markdown("**" + L("Наблюдаем, но выводов пока нет", "Бақылап жүрміз, әзірге қорытынды жоқ") + "**")
        for e in low:
            tag_b = badge(L("мало данных", "дерек аз"), "b-low") if e["low_data"] else badge(L("редко", "сирек"), "b-low")
            refs = ", ".join(ref_label(r) for r in e["refs"][:3])
            st.markdown(f'- {esc(T.name(e["tag"], lang))} · {esc(T.skill_name(e["skill"], lang).lower())} — '
                        f'{e["count"]}/{e["total"]} {tag_b} <span class="muted">{esc(refs)}</span>', unsafe_allow_html=True)

    # 2. Как решает
    st.header(L("2. Как решает", "2. Қалай шығарады"))
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**" + L("Квадратные уравнения: какими способами", "Квадрат теңдеулер: қандай тәсілмен") + "**")
        for m in p["methods"]:
            share = m["count"] / m["total"] if m["total"] else 0
            st.markdown(f'{esc(T.name(m["tag"], lang))} — {esc(portrait.share_text(m["count"], m["total"], lang))}'
                        f'<div class="bar"><div style="width:{share * 100:.0f}%"></div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown("**" + L("Привычки", "Әдеттер") + "**")
        for h in ("check_done", "skip_steps", "domain_noted", "late"):
            v = p["habits"][h]
            share = v["count"] / v["total"] if v["total"] else 0
            st.markdown(f'{esc(T.name(h, lang))} — {esc(portrait.share_text(v["count"], v["total"], lang))}'
                        f'<div class="bar"><div style="width:{share * 100:.0f}%; background:#94a3b8"></div></div>',
                        unsafe_allow_html=True)
    if p["strengths"]:
        st.markdown("**" + L("Получается стабильно:", "Тұрақты шығады:") + "** " + "; ".join(
            f"{T.skill_name(s['skill'], lang)} — " + L(f"без ошибок в {s['clean']} из {s['total']}",
                                                       f"{s['clean']}/{s['total']} жұмыста қатесіз")
            for s in p["strengths"]))

    # 3. Что пишет учитель
    practice_view.render_portrait_block(conn, p["student"]["id"], L, lang)
    st.header(L("3. Что пишет учитель", "3. Мұғалім не жазады"))
    if not p["teacher"]:
        st.caption(L("Комментариев пока нет.", "Әзірге пікір жоқ."))
    for t in p["teacher"]:
        b = badge(L("подтверждено работами", "жұмыстармен расталды"), "b-conf") if t["confirmed"] else ""
        quotes = "; ".join(f"«{esc(q['text'])}» ({L('ДЗ', 'ҮТ')} №{q['work_no']})" for q in t["quotes"][:4])
        st.markdown(f'- **«{esc(T.name(t["tag"], lang))}»** — {esc(portrait.comments_text(t["count"], lang))} {b}'
                    f'<br><span class="muted">{quotes}</span>', unsafe_allow_html=True)

    # 4. Как работать
    st.header(L("4. Как работать", "4. Қалай жұмыс істеу керек"))
    recs = p["recs"]
    if not recs:
        st.info(L("Специальных рекомендаций нет — продолжать в том же темпе.",
                  "Арнайы ұсыныс жоқ — осы қарқынмен жалғастыру."))
    pers = ss.get("personalized", {}).get((p["student"]["id"], lang))
    cfg = ss["llm_cfg"]
    if recs and cfg.ready and not pers:
        if st.button(L("✨ Привязать советы к работам (ИИ)", "✨ Кеңестерді жұмыстарға байланыстыру (ЖИ)")) and llm_allowed(
                "Попробуйте позже — пока показаны советы из словаря.",
                "Кейінірек қайталаңыз — әзірге сөздіктегі кеңестер көрсетілді."):
            try:
                with st.spinner(L("Формулирую…", "Тұжырымдап жатырмын…")):
                    adv = llm.personalize(cfg, recs[:3], p["student"]["alias"], lang)
                ss.setdefault("personalized", {})[(p["student"]["id"], lang)] = adv
                st.rerun()
            except llm.LLMError as ex:
                st.warning(L("Не получилось, показаны советы из словаря: ", "Болмады, сөздіктегі кеңестер көрсетілді: ") + str(ex))
    for i, r in enumerate(recs[:3]):
        b = badge(L("подтверждено дважды", "екі рет расталды"), "b-conf") if r["confirmed"] else ""
        st.markdown(f'<div class="card"><h4>{i + 1}. {esc(r["title"])} {b}</h4>'
                    f'<span class="muted">{esc(r["why"])}</span></div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        teacher_text = pers[i] if pers and i < len(pers) else r["teacher"]
        src = (L(" · сформулировано ИИ из совета словаря, ссылки проверены кодом",
                 " · ЖИ сөздік кеңесінен тұжырымдады, сілтемелер кодпен тексерілді") if pers else "")
        c1.markdown(f'<div class="adv"><b>{L("Учителю", "Мұғалімге")}</b>{esc(teacher_text)}'
                    f'<div class="ref">{esc(src)}</div></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="adv"><b>{L("Ученику", "Оқушыға")}</b>{esc(r["student"])}</div>',
                    unsafe_allow_html=True)
        if r["refs"]:
            st.markdown('<div class="ref">' + L("Опора: ", "Негіз: ") + esc("; ".join(ref_label(x) for x in r["refs"]))
                        + "</div>", unsafe_allow_html=True)
        dynamics_view.rec_controls(conn, p["student"]["id"], r, L, lang, key=f"{p['student']['id']}_{r['key']}")
        st.write("")
    if len(recs) > 3:
        with st.expander(L(f"Ещё {len(recs) - 3}", f"Тағы {len(recs) - 3}")):
            for r in recs[3:]:
                st.markdown(f"**{r['title']}** — {r['why']}  \n{L('Учителю', 'Мұғалімге')}: {r['teacher']}  \n"
                            f"{L('Ученику', 'Оқушыға')}: {r['student']}")

    # 5. Работы
    st.header(L("5. Работы", "5. Жұмыстар"))
    for w in p["works"]:
        atts = db.q(conn, """SELECT at.*, p.idx, p.statement, p.id AS pid FROM attempts at
                             JOIN problems p ON p.id = at.problem_id WHERE at.submission_id=? ORDER BY p.idx""", (w["id"],))
        marks = " ".join(("✅" if a["correct"] == 1 else "❌" if a["first_error_line"] else "➖") for a in atts)
        late = db.q1(conn, "SELECT 1 FROM observations WHERE submission_id=? AND tag='late'", (w["id"],))
        src = "🟣 " if w["source"] == "live" else ""
        title = f"{src}{L('ДЗ', 'ҮТ')} №{w['number']} · {w['submitted_at'][:16]} · {marks}" + (L(" · после срока", " · мерзімнен кеш") if late else "")
        with st.expander(title):
            for a in atts:
                st.markdown(f"**№{a['idx']}.** {esc(a['statement'])}", unsafe_allow_html=True)
                st.markdown(render_lines(steps_of(w["id"], a["pid"]), highlight=a["first_error_line"]),
                            unsafe_allow_html=True)
            com = db.q(conn, "SELECT text FROM teacher_comments WHERE submission_id=?", (w["id"],))
            for cmt in com:
                st.markdown(f"💬 *{esc(cmt['text'])}*")

    dynamics_view.render_section(conn, p, L, lang)
    review_view.rating_form(conn, p, L, reviewer=REVIEWER)


def portrait_student(p: dict):
    name = p["student"]["alias"]
    st.subheader(L(f"Привет, {name}! Вот что видно по твоим {p['n_works']} работам.",
                   f"Сәлем, {name}! Міне, {p['n_works']} жұмысыңнан көрінетіні."))
    st.markdown("#### " + L("Что у тебя получается", "Саған не жақсы шығады"))
    good = [f"{T.skill_name(s['skill'], lang)} — " + L(f"без ошибок в {s['clean']} работах из {s['total']}",
                                                       f"{s['clean']}/{s['total']} жұмыста қатесіз")
            for s in p["strengths"]]
    used = [m for m in p["methods"] if m["count"]]
    if used:
        good.append(L("Уверенно пользуешься: ", "Сенімді қолданасың: ") + ", ".join(T.name(m["tag"], lang).lower() for m in used))
    if p["habits"]["check_done"]["count"] >= max(1, p["n_works"] // 2):
        good.append(L("Часто проверяешь ответ — это отличная привычка", "Жауапты жиі тексересің — бұл тамаша әдет"))
    st.markdown("\n".join(f"- ✅ {g}" for g in good) or "—")

    st.markdown("#### " + L("На что обратить внимание", "Неге назар аудару керек"))
    if not p["recs"]:
        st.success(L("Всё идёт хорошо — продолжай!", "Бәрі жақсы — осылай жалғастыр!"))
    for i, r in enumerate(p["recs"][:3]):
        ex = next((x for x in r["refs"] if x.get("evidence") and x.get("problem_idx")), None)
        example = (f'<div class="ref">{L("Например", "Мысалы")}, {esc(ref_label(ex))}: '
                   f'<code>{esc(ex["evidence"])}</code></div>') if ex else ""
        st.markdown(f'<div class="card"><h4>{i + 1}. {esc(r["title"])}</h4>{esc(r["student"])}{example}</div>',
                    unsafe_allow_html=True)
    if st.button(L("🧩 Потренироваться", "🧩 Жаттығу"), type="primary"):
        go("practice", p["student"]["id"])
    st.caption(L("Это не оценка, а подсказка, над чем поработать. Каждый пункт опирается на твои работы.",
                 "Бұл баға емес, неге көңіл бөлу керегі туралы кеңес. Әр тармақ сенің жұмыстарыңа негізделген."))


def page_check():
    st.title(L("Проверка работы", "Жұмысты тексеру"))
    st.caption(L("Фото или текст → строки → проверка SymPy → первая неверная строка и наводящий вопрос → запись в журнал.",
                 "Фото немесе мәтін → жолдар → SymPy тексеруі → алғашқы қате жол және бағыттаушы сұрақ → журналға жазу."))
    if no_students():
        return
    studs = students()
    alias = {s["id"]: s["alias"] for s in studs}
    asg = db.q(conn, "SELECT * FROM assignments ORDER BY number DESC")
    c = st.columns(2)
    sid = remember("chk_student", c[0].selectbox(L("Ученик", "Оқушы"), list(alias), format_func=lambda i: alias[i],
                                                 key=sticky("chk_student", studs[0]["id"])))
    aid = remember("chk_asg", c[1].selectbox(L("Задание", "Тапсырма"), [a["id"] for a in asg], key=sticky("chk_asg", seed.live_assignment_id(conn)),
                         format_func=lambda i: next(f"{L('ДЗ', 'ҮТ')} №{a['number']} ({L('срок', 'мерзімі')} {a['due_at'][:10]})"
                                                    for a in asg if a["id"] == i)))
    probs = pipeline.problems_of(conn, aid)
    live_aid = seed.live_assignment_id(conn)

    tab_photo, tab_text = st.tabs([L("📷 Фото тетради", "📷 Дәптер фотосы"), L("⌨️ Ввести текстом", "⌨️ Мәтінмен енгізу")])
    cfg = ss["llm_cfg"]
    with tab_photo:
        types = ", ".join(t for t in llm.PHOTO_TYPES if t not in ("jpeg", "heif"))
        up = st.file_uploader(L(f"Фото решения ({types})", f"Шешім фотосы ({types})"), type=llm.PHOTO_TYPES,
                              key=f"photo_{aid}")
        if not cfg.ready:
            st.info(L("Для распознавания почерка подключите языковую модель в боковой панели. "
                      "Запасной путь — вкладка «Ввести текстом»: журнал и портрет работают так же.",
                      "Қолжазбаны тану үшін бүйір панельде тілдік модельді қосыңыз. "
                      "Балама жол — «Мәтінмен енгізу» қойындысы: журнал мен портрет дәл солай жұмыс істейді."))
        c_btn, c_tgl = st.columns([1, 2])
        passes = 2 if c_tgl.toggle(L("Два прочтения (точнее, дороже)", "Екі рет оқу (дәлірек, қымбатырақ)"),
                                   key="ocr_two_passes", value=False,
                                   help=L("Фото читается дважды разными запросами; строки, где прочтения разошлись, "
                                          "помечаются ⚠️ и показывают оба варианта.",
                                          "Фото екі түрлі сұраумен екі рет оқылады; оқулар сәйкес келмеген жолдар "
                                          "⚠️ белгіленіп, екі нұсқасы да көрсетіледі.")) else 1
        if c_btn.button(L("Распознать строки", "Жолдарды тану"), disabled=not (cfg.ready and up is not None),
                        type="primary") and llm_allowed("Попробуйте позже или введите строки текстом.",
                                                        "Кейінірек қайталаңыз немесе жолдарды мәтінмен енгізіңіз."):
            try:
                t0 = datetime.now()
                with st.spinner(L("Распознаю почерк…", "Қолжазбаны танып жатырмын…")):
                    det = llm.recognize_detailed(cfg, up.getvalue(), [(p["idx"], p["statement"]) for p in probs], passes=passes)

                def _prep(lines, kind=None):  # kind — вид задачи: строки неравенств и систем разбирает модуль вида
                    out = []
                    for l in lines:
                        l = {**l, "text": ocr.clean_text(l["text"])}
                        if l.get("alternatives"):
                            l["alternatives"] = [ocr.clean_text(a) for a in l["alternatives"]]
                        out.append(l)
                    return ocr.score_lines(out, kind)
                ss["ocr_view"] = {p["id"]: _prep(det.get(p["idx"], []), p["kind"]) for p in probs}
                ss["ocr_raw"] = {pid: list(ls) for pid, ls in ss["ocr_view"].items()}
                ss["ocr_unassigned"] = _prep(det.get(llm.UNASSIGNED, []))
                ss["ocr_aid"], ss["ocr_run"] = aid, ss.get("ocr_run", 0) + 1
                ss["ocr_secs"] = (datetime.now() - t0).total_seconds()
                ss["photo_name"] = up.name
                ss.pop("ocr_accepted", None)
            except llm.LLMError as ex:
                st.error(str(ex))

        raw = ss.get("ocr_view") if ss.get("ocr_aid") == aid else None  # как прочитала модель — для таблиц

        def _cell(v) -> str:  # ячейка таблицы → строка (пустые и удалённые ячейки — None/NaN)
            return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
        flag_name = {"illegible": L("неразборчиво", "анық оқылмайды"), "unparsable": L("не разбирается", "талданбайды"),
                     "disagree": L("прочтения разошлись", "оқулар сәйкес келмейді")}
        if raw is None and up is not None:
            st.image(llm.prepare_image(up.getvalue())[0], width=420)
        if raw is not None:
            c_img, c_tab = st.columns([1, 1.35])
            with c_img:
                if up is not None:
                    st.image(llm.prepare_image(up.getvalue())[0], use_container_width=True)
                else:
                    st.caption(L("Фото не сохраняется. Загрузите его снова, чтобы сверять строки рядом с ним.",
                                 "Фото сақталмайды. Жолдарды қасында салыстыру үшін оны қайта жүктеңіз."))
            with c_tab:
                all_lines = [l for ls in raw.values() for l in ls] + list(ss.get("ocr_unassigned") or [])
                n_bad = sum(ocr.needs_review(l) for l in all_lines)
                few = n_bad % 10 in (2, 3, 4) and n_bad % 100 not in (12, 13, 14)
                one = n_bad % 10 == 1 and n_bad % 100 != 11
                msg = L(f"{n_bad} {'строка' if one else 'строки' if few else 'строк'} из {len(all_lines)} "
                        f"{'требует' if one else 'требуют'} проверки",
                        f"{len(all_lines)} жолдың {n_bad}-і тексеруді қажет етеді")
                (st.warning if n_bad else st.success)(("⚠️ " if n_bad else "✅ ") + msg
                                                      + f" · {ss.get('ocr_secs', 0):.0f} {L('с', 'с')}")
                run = ss.get("ocr_run", 0)
                edited = {}
                for p in probs:
                    lines = raw.get(p["id"], [])
                    two = any(l.get("alternatives") for l in lines)
                    df = pd.DataFrame([{
                        "no": i, "text": l["text"], "conf": round(l["confidence"] * 100),
                        "flag": ("⚠️ " + flag_name[l["flags"][0]]) if ocr.needs_review(l) and l["flags"] else "",
                        **({"alt": (l.get("alternatives") or ["", ""])[1] if l.get("alternatives") else "",
                            "take": False} if two else {}),
                    } for i, l in enumerate(lines, 1)], columns=["no", "text", "conf", "flag"] + (["alt", "take"] if two else []))
                    st.markdown(f"**№{p['idx']}.** {esc(p['statement'])}")
                    cols = {"no": st.column_config.NumberColumn("№", disabled=True, width="small"),
                            "text": st.column_config.TextColumn(L("Строка", "Жол")),
                            "conf": st.column_config.NumberColumn(L("Уверенность", "Сенімділік"), format="%d %%",
                                                                  disabled=True, width="small"),
                            "flag": st.column_config.TextColumn(L("Флаг", "Белгі"), disabled=True)}
                    if two:
                        cols["alt"] = st.column_config.TextColumn(L("Второе прочтение", "Екінші оқу"), disabled=True)
                        cols["take"] = st.column_config.CheckboxColumn(L("Взять его", "Соны алу"), width="small")
                    order = ["no", "text"] + (["alt", "take"] if two else []) + ["conf", "flag"]
                    edited[p["id"]] = st.data_editor(df, column_config=cols, column_order=order, hide_index=True,
                                                     num_rows="dynamic",
                                                     use_container_width=True, key=f"ocr_ed_{aid}_{p['id']}_{run}")
                loose = ss.get("ocr_unassigned") or []
                loose_df = None
                if loose:
                    st.markdown("**" + L("Строки без задачи", "Есепке жатпайтын жолдар") + "**")
                    st.caption(L("Модель не поняла, к какой задаче относятся эти строки. Выберите задачу или оставьте «—».",
                                 "Модель бұл жолдардың қай есепке жататынын анықтамады. Есепті таңдаңыз немесе «—» қалдырыңыз."))
                    opts = ["—"] + [f"№{p['idx']}" for p in probs]
                    loose_df = st.data_editor(
                        pd.DataFrame([{"text": l["text"], "conf": round(l["confidence"] * 100), "to": "—"} for l in loose]),
                        column_config={"text": st.column_config.TextColumn(L("Строка", "Жол"), width="large"),
                                       "conf": st.column_config.NumberColumn(L("Уверенность", "Сенімділік"), format="%d %%",
                                                                             disabled=True, width="small"),
                                       "to": st.column_config.SelectboxColumn(L("Задача", "Есеп"), options=opts, required=True)},
                        hide_index=True, use_container_width=True, key=f"ocr_loose_{aid}_{run}")
                if st.button(L("Принять строки", "Жолдарды қабылдау"), type="primary", use_container_width=True):
                    new_raw = {pid: list(ls) for pid, ls in raw.items()}
                    for p in probs:
                        texts = []
                        for _, row in edited[p["id"]].iterrows():
                            take = row.get("take")
                            t = _cell(row.get("alt") if pd.notna(take) and bool(take) else row.get("text"))
                            if t:
                                texts.append(t)
                        if loose_df is not None:
                            for (_, row), l in zip(loose_df.iterrows(), loose):
                                if row["to"] == f"№{p['idx']}" and _cell(row["text"]):
                                    texts.append(_cell(row["text"]))
                                    new_raw[p["id"]].append(l)  # строка из распознавания — для учёта правок
                        ss[f"lines_{aid}_{p['id']}"] = "\n".join(texts)
                    ss["ocr_raw"] = new_raw  # + строки без задачи, которые учитель отнёс к задаче
                    ss["ocr_accepted"] = True
                    st.rerun()
                if ss.get("ocr_accepted"):
                    es = ocr.edit_stats(ss.get("ocr_raw") or raw, {p["id"]: ss.get(f"lines_{aid}_{p['id']}", "").splitlines() for p in probs})
                    st.success(L(f"Строки приняты: исправлено {es['edited']} из {es['total']}. "
                                 "Нажмите «Проверить» ниже или поправьте текст во вкладке «Ввести текстом».",
                                 f"Жолдар қабылданды: {es['total']} жолдың {es['edited']}-і түзетілді. "
                                 "Төмендегі «Тексеру» батырмасын басыңыз немесе мәтінді «Мәтінмен енгізу» қойындысында түзетіңіз."))
        st.caption(L("Фото не сохраняется: после распознавания в базе остаются только строки текста.",
                     "Фото сақталмайды: танылғаннан кейін базада тек мәтін жолдары қалады."))
    with tab_text:
        number = next(a["number"] for a in asg if a["id"] == aid)
        demo = seed.LIVE_DEMO_TEXT if aid == live_aid else seed.EXTRA_DEMO_TEXT.get(number)
        # демо-ученица — по псевдониму в синтетическом классе: id меняются после удаления и импорта
        demo_sid = next((s["id"] for s in studs if s["alias"] == seed.STUDENTS[0] and s["class_name"] == seed.CLASS_NAME), None)
        who = f"{seed.STUDENTS[0]}, " if demo_sid is not None else ""
        if demo and st.button(L(f"Вставить демо-работу ({who}ДЗ №{number})", f"Демо-жұмысты қою ({who}ҮТ №{number})")):
            for p in probs:
                ss[f"lines_{aid}_{p['id']}"] = demo.get(p["idx"], "")
            ss["set_chk_student"] = demo_sid if demo_sid is not None else sid
            if aid == live_aid:
                ss["comment_prefill"] = seed.LIVE_DEMO_COMMENT
            st.rerun()
        for p in probs:
            st.text_area(f"№{p['idx']}. {p['statement']}  ·  {T.skill_name(p['skill'], lang)}",
                         key=f"lines_{aid}_{p['id']}", height=130,
                         placeholder=L("по одной строке решения на строку", "әр жолға шешімнің бір жолы"))

    if st.button(L("Проверить", "Тексеру"), type="primary", use_container_width=True):
        answers = {p["id"]: ss.get(f"lines_{aid}_{p['id']}", "").splitlines() for p in probs}
        with st.spinner(L("Проверяю шаги…", "Қадамдарды тексеріп жатырмын…")):
            results = pipeline.run_checks(conn, aid, answers)
        ss["check"] = {"sid": sid, "aid": aid, "answers": answers, "results": results, "saved": False}
        review_view.timer_start(ss, sid, aid)
        ss.pop("diff", None)

    chk = ss.get("check")
    if not chk or chk["aid"] != aid or chk["sid"] != sid:
        return
    if not chk["results"]:
        st.warning(L("Нет строк для проверки.", "Тексеретін жол жоқ."))
        return
    st.divider()
    pmap = {p["id"]: p for p in probs}
    for pid, res in chk["results"].items():
        p = pmap[pid]
        fe = res.first_error
        if fe:
            verdict = badge(L(f"ошибка в строке {fe['line']}", f"{fe['line']}-жолда қате"), "b-weak") + badge(T.name(fe["tag"], lang), "b-low")
        elif res.correct:
            verdict = badge(L("верно", "дұрыс"), "b-ok")
        else:
            verdict = badge(L("ответ не найден или не упрощён", "жауап табылмады не ықшамдалмаған"), "b-low")
        extra = ""
        if res.methods:
            extra += " · " + L("способ: ", "тәсіл: ") + ", ".join(T.name(m, lang).lower() for m in res.methods)
        habits = [h for h in res.habits]
        if habits:
            extra += " · " + ", ".join(T.name(h, lang).lower() for h in habits)
        st.markdown(f"#### №{p['idx']}. {esc(p['statement'])} {verdict}"
                    f"<span class='muted'>{esc(extra)}</span>", unsafe_allow_html=True)
        st.markdown(render_lines(res.lines, p["statement"], fe["line"] if fe else None), unsafe_allow_html=True)
        if fe:
            other = "kk" if lang == "ru" else "ru"
            st.markdown(f'<div class="q"><b>{L("Наводящий вопрос ученику", "Оқушыға бағыттаушы сұрақ")}</b><br>'
                        f'{esc(question_for(fe, lang))}<br><span class="muted">{esc(question_for(fe, other))}</span></div>',
                        unsafe_allow_html=True)
            if ocr.error_unverified(pid, res, ocr.unverified(ss.get("ocr_raw") or {}, chk["answers"])):
                st.warning(L("⚠️ Ошибка найдена в строке, которую распознавание прочитало неуверенно. "
                             "Сверьте её с фото перед записью.",
                             "⚠️ Қате тану сенімсіз оқыған жолдан табылды. Журналға жазбас бұрын оны фотомен салыстырыңыз."))
            with st.expander(L("Для учителя: что нашла проверка", "Мұғалімге: тексеру не тапты")):
                det = fe.get("detail") or {}
                st.markdown(f"- {L('Тип', 'Түрі')}: **{T.name(fe['tag'], lang)}**\n"
                            f"- {L('Переход', 'Ауысу')}: `{fe['evidence']}`")
                if det.get("expected"):
                    st.markdown(f"- {L('Вместо', 'Орнына')} `{det.get('num')}` {L('должно быть', 'болуы керек')} `{det['expected']}`")
                if det.get("f"):
                    st.markdown(f"- {L('Выражение', 'Өрнек')}: `{det['f']}`")
                if res.true_answer is not None:
                    st.markdown(f"- {L('Верный ответ', 'Дұрыс жауап')}: `{'; '.join(f'{v:g}' for v in res.true_answer) or '∅'}`")
            if not chk["saved"]:
                review_view.not_error_toggle(aid, pid, L)

    st.divider()
    if chk["saved"]:
        d = ss.get("diff") or {}
        if d.get("lines"):
            st.markdown('<div class="diff"><b>' + L("Портрет обновился:", "Портрет жаңарды:") + "</b><br>"
                        + "<br>".join(esc(x) for x in d["lines"]) + "</div>", unsafe_allow_html=True)
        else:
            st.success(L("Работа записана в журнал.", "Жұмыс журналға жазылды."))
        review_view.submission_controls(conn, ss.get("last_sub_id"), L, reviewer=REVIEWER)
        if st.button(L("Открыть портрет →", "Портретті ашу →"), type="primary"):
            go("portrait", sid)
        return
    if "comment_prefill" in ss:
        ss["comment_text"] = ss.pop("comment_prefill")
    st.text_area(L("Комментарий учителя (необязательно, свободным текстом, рус/каз)",
                   "Мұғалімнің пікірі (міндетті емес, еркін мәтін, орыс/қаз)"), key="comment_text", height=80)
    if st.button(L("Записать в журнал и обновить портрет", "Журналға жазып, портретті жаңарту"), type="primary",
                 use_container_width=True):
        if not auth.can_see(conn, user, sid) or not consent.required_ok(conn, sid):
            audit.log(conn, user, "record_refused", "student", sid)
            st.error(L("Нет согласия на анализ работ этого ученика — работа не записана. "
                       "Отметьте согласие по бумажной форме: «🛡️ Управление» → «Согласия».",
                       "Бұл оқушының жұмыстарын талдауға келісім жоқ — жұмыс жазылмады. "
                       "Келісімді қағаз нысаны бойынша белгілеңіз: «🛡️ Басқару» → «Келісімдер»."))
            return
        old = db.q(conn, "SELECT id FROM submissions WHERE student_id=? AND assignment_id=? AND source='live'", (sid, aid))
        for o in old:  # повторная живая проверка той же работы заменяет прежнюю
            pipeline.delete_submission(conn, o["id"])
        before = portrait.snapshot(portrait.build(conn, sid, lang))
        sub_id, _ = pipeline.record_submission(conn, sid, aid, chk["answers"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                               photo_name=ss.get("photo_name"), source="live", results=chk["results"],
                                               line_confidence=ocr.unverified(ss.get("ocr_raw") or {}, chk["answers"]))
        if ss.get("ocr_raw"):  # журнал распознаваний: что прочитала модель и что оставил учитель
            ocr_store.save(conn, sub_id, {k: v for k, v in ss["ocr_raw"].items() if k in chk["answers"]}, chk["answers"])
            ss.pop("ocr_raw", None)
        text = (ss.get("comment_text") or "").strip()
        if text:
            tagged, tagger = None, "keywords"
            if cfg.ready and auth.rate_limit(user, "llm"):  # лимит — как ошибка модели: разметка по словарю
                try:
                    tagged, tagger = llm.tag_comment(cfg, text), "llm"
                except llm.LLMError:
                    tagged = None
            pipeline.record_comment(conn, sub_id, text, tagged or tag_comment_keywords(text), tagger)
        review_view.after_record(conn, ss, sub_id, sid, aid, chk, L, reviewer=REVIEWER)  # время проверки и «это не ошибка»
        ss["last_sub_id"] = sub_id
        after = portrait.snapshot(portrait.build(conn, sid, lang))
        ss["diff"] = {"sid": sid, "lines": portrait.diff(before, after, lang)}
        audit.log(conn, user, "record_submission", "submission", sub_id)
        chk["saved"] = True
        st.rerun()


def page_log():
    st.title(L("Журнал наблюдений", "Бақылау журналы"))
    st.caption(L("Каждая строка — «ученик · что замечено · где доказательство». Портрет каждый раз считается заново "
                 "из этих строк, поэтому любой его пункт можно проследить до работы и строки.",
                 "Әр жол — «оқушы · не байқалды · дәлел қайда». Портрет әр жолы осы жолдардан қайта есептеледі, "
                 "сондықтан кез келген тармағын жұмыс пен жолға дейін қадағалауға болады."))
    if no_students():
        return
    studs = students()
    c = st.columns(3)
    who = c[0].selectbox(L("Ученик", "Оқушы"), [0] + [s["id"] for s in studs],
                         format_func=lambda i: L("все", "барлығы") if i == 0 else next(s["alias"] for s in studs if s["id"] == i))
    src = c[1].selectbox(L("Источник", "Көзі"), ["", "auto", "teacher", "practice"],
                         format_func=lambda x: {"": L("все", "барлығы"), "auto": L("автопроверка", "автотексеру"),
                                                "teacher": L("учитель", "мұғалім"),
                                                "practice": L("тренажёр", "жаттықтырғыш")}[x])
    kind = c[2].selectbox(L("Вид", "Түрі"), ["", "error", "method", "habit", "teacher"],
                          format_func=lambda x: {"": L("все", "барлығы"), "error": L("ошибка", "қате"),
                                                 "method": L("метод", "тәсіл"), "habit": L("привычка", "әдет"),
                                                 "teacher": L("из комментария", "пікірден")}[x])
    sql = """SELECT o.id, o.created_at, st.alias, a.number, p.idx, o.line_no, o.kind, o.tag, o.skill, o.evidence,
                    o.source, o.confidence FROM observations o JOIN students st ON st.id = o.student_id
             LEFT JOIN submissions s ON s.id = o.submission_id LEFT JOIN assignments a ON a.id = s.assignment_id
             LEFT JOIN problems p ON p.id = o.problem_id WHERE 1=1"""
    vis, args = visible_filter("o.student_id")
    sql += vis
    if who:
        sql += " AND o.student_id=?"; args.append(who)
    if src:
        sql += " AND o.source=?"; args.append(src)
    if kind:
        sql += " AND o.kind=?"; args.append(kind)
    sql += " ORDER BY o.created_at DESC"
    rows = db.q(conn, sql, args)
    marks = review_view.log_marks(conn, [r["id"] for r in rows], L)
    df = pd.DataFrame([{
        L("Время", "Уақыт"): r["created_at"][:16], L("Ученик", "Оқушы"): r["alias"],
        L("Работа", "Жұмыс"): f"№{r['number']}" if r["number"] else "", L("Задача", "Есеп"): str(r["idx"] or ""),
        L("Строка", "Жол"): str(r["line_no"] or ""), L("Вид", "Түрі"): r["kind"], L("Тег", "Тег"): T.name(r["tag"], lang),
        L("Отметка учителя", "Мұғалім белгісі"): marks.get(r["id"], ""),
        L("Навык", "Дағды"): T.skill_name(r["skill"], lang) if r["skill"] else "",
        L("Доказательство", "Дәлел"): r["evidence"], L("Источник", "Көзі"): r["source"],
        L("Уверенность", "Сенімділік"): r["confidence"]} for r in rows])
    sel = st.dataframe(df, hide_index=True, use_container_width=True, height=520,
                       on_select="rerun", selection_mode="single-row", key=f"log_table_{who}_{src}_{kind}")
    picked = sel.selection.rows if sel else []
    if picked and picked[0] < len(rows):
        review_view.log_controls(conn, rows[picked[0]], L, reviewer=REVIEWER)
    else:
        st.caption(L("Выберите строку слева в таблице, чтобы поставить отметку учителя.",
                     "Мұғалім белгісін қою үшін кестеде жолды сол жағынан таңдаңыз."))
    st.download_button(L("Скачать CSV", "CSV жүктеу"), df.to_csv(index=False).encode("utf-8-sig"),
                       "observations.csv", "text/csv", on_click=audit.log, args=(conn, user, "export_csv", "export"))


def page_about():
    st.title(L("Как это работает", "Бұл қалай жұмыс істейді"))
    st.markdown(L(
        "Автопроверка ДЗ по алгебре записывает в журнал ошибки, методы и привычки ученика, а из них и комментариев "
        "учителя собирается портрет «где слаб и как работать». **Языковая модель стоит только на входе** (почерк, "
        "свободный текст учителя) **и на выходе** (формулировки советов). Всё, что считает и решает, — обычный код, "
        "поэтому каждый вывод воспроизводим.",
        "ҮТ-ны автоматты тексеру оқушының қателерін, тәсілдері мен әдеттерін журналға жазады, ал олардан және "
        "мұғалім пікірлерінен «қай жері әлсіз және қалай жұмыс істеу керек» портреті құралады. **Тілдік модель тек "
        "кірісте** (қолжазба, мұғалімнің еркін мәтіні) **және шығыста** (кеңестерді тұжырымдау). Есептейтін және "
        "шешетіннің бәрі — қарапайым код, сондықтан әр қорытынды қайталанады."))
    st.graphviz_chart("""
digraph G { rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef2ff", fontname="Helvetica", fontsize=11];
  A [label="Фото ДЗ"]; B [label="Распознавание строк\\nvision-модель", fillcolor="#fef3c7"];
  C [label="Проверка шагов\\nSymPy"]; D [label="Ошибки и методы\\nправила"];
  T [label="Комментарий учителя"]; E [label="Разметка по словарю\\nLLM или словарь основ", fillcolor="#fef3c7"];
  F [label="Журнал наблюдений\\nSQLite", shape=cylinder, fillcolor="#dcfce7"];
  G [label="Сборка портрета\\nправила"]; H [label="Советы\\nшаблон (+ LLM)", fillcolor="#fef3c7"];
  I [label="Портрет и карта класса\\nStreamlit"];
  A -> B -> C -> D -> F; T -> E -> F; F -> G -> H -> I; }
""")
    st.caption(L("Жёлтым — где участвует языковая модель. Без неё система работает: ввод строк текстом, словарь основ, шаблоны.",
                 "Сары — тілдік модель қатысатын жерлер. Онсыз да жүйе жұмыс істейді: мәтінмен енгізу, түбір сөздігі, үлгілер."))

    st.subheader(L("Как проверяются строки", "Жолдар қалай тексеріледі"))
    st.markdown(L(
        "- **Уравнения**: сравниваются множества решений соседних строк (`solveset` на ℝ, с учётом ОДЗ условия) — так ловятся потерянные и лишние корни.\n"
        "- **Выражения**: разность соседних строк упрощается до нуля или совпадает в 20 случайных точках.\n"
        "- Первая строка, где проверка не прошла, — место ошибки. Тип определяют правила: *знак* (смена знака одного слагаемого делает строки равносильными), "
        "*ФСУ* (типичное неверное раскрытие (a±b)² или (a−b)(a+b)), *вычислительная* (строки расходятся ровно в одном числе), *потеря / лишний корень* "
        "(множество решений уменьшилось / выросло; для потери ищется делитель с x).\n"
        "- Отдельно проверяются строки D = …, теорема Виета, цепочки x₁ = (…)/2 = …, итоговый ответ и строки «не подходит».\n"
        "- Строка из распознавания идёт в SymPy только после белого списка символов — произвольный код выполнить нельзя.",
        "- **Теңдеулер**: көршілес жолдардың шешімдер жиыны салыстырылады (ℝ-да `solveset`, шарттың ММЖ ескеріледі) — жоғалған және артық түбірлер осылай табылады.\n"
        "- **Өрнектер**: көршілес жолдардың айырмасы нөлге дейін ықшамдалады немесе 20 кездейсоқ нүктеде сәйкес келеді.\n"
        "- Тексеруден өтпеген алғашқы жол — қатенің орны. Түрін ережелер анықтайды: *таңба*, *ҚКФ*, *есептеу*, *түбірді жоғалту / бөгде түбір*.\n"
        "- Танылған жол SymPy-ға тек рұқсат етілген таңбалар тізімінен кейін ғана түседі."))
    st.subheader(L("Как собирается портрет", "Портрет қалай құралады"))
    st.latex(r"p = \frac{\sum_i w_i\, e_i}{\sum_i w_i}, \qquad w_i = 0.8^{k_i}")
    st.markdown(L(
        "eᵢ = 1, если в работе i есть ошибка, kᵢ — сколько работ назад она сдана. **Слабое место** — если случаев ≥ 3 и p ≥ 40 %. "
        "Меньше трёх случаев — пометка «мало данных» вместо вывода. Если учитель и автопроверка отмечают одно и то же — «подтверждено дважды». "
        "Портрет описывает действия («в 4 работах из 6 нет проверки»), а не черты характера.",
        "eᵢ = 1, егер i жұмысында қате болса, kᵢ — ол неше жұмыс бұрын тапсырылғаны. **Әлсіз тұс** — жағдай ≥ 3 және p ≥ 40 %. "
        "Үштен аз болса — қорытынды орнына «дерек аз» белгісі. Мұғалім мен автотексеру бір нәрсені белгілесе — «екі рет расталды»."))

    st.subheader(L("Словарь тегов", "Тегтер сөздігі"))
    st.dataframe(pd.DataFrame([{L("Код", "Коды"): k, L("Вид", "Түрі"): v["kind"], "RU": v["ru"], "KZ": v["kk"]}
                               for k, v in T.TAGS.items()]), hide_index=True, use_container_width=True)

    st.subheader(L("Своё и стороннее", "Өзіміздікі және бөгде"))
    st.markdown(L(
        "| Компонент | Чьё |\n|---|---|\n"
        "| Нормализация рукописной записи, правила классификации ошибок, методов и привычек | **своё** (`core/checker.py`) |\n"
        "| Журнал наблюдений, взвешенная доля, пороги, «подтверждено дважды», советы | **своё** (`core/portrait.py`, `core/tags.py`) |\n"
        "| Словарь тегов и советов на русском и казахском | **своё** |\n"
        "| Интерфейс | **своё** на Streamlit |\n"
        "| Символьная математика (равносильность, solveset) | SymPy (open source) |\n"
        "| Распознавание почерка, разметка комментариев, формулировки | внешняя языковая модель по API (Claude / OpenAI-совместимая) |\n"
        "| База, таблицы | SQLite, pandas |",
        "| Компонент | Кімдікі |\n|---|---|\n"
        "| Қолжазбаны қалыпқа келтіру, қателерді, тәсілдер мен әдеттерді жіктеу ережелері | **өзіміздікі** |\n"
        "| Бақылау журналы, салмақталған үлес, шектер, кеңестер | **өзіміздікі** |\n"
        "| Орыс және қазақ тілдеріндегі тегтер мен кеңестер сөздігі | **өзіміздікі** |\n"
        "| Интерфейс | Streamlit-те **өзіміздікі** |\n"
        "| Символдық математика | SymPy (open source) |\n"
        "| Қолжазбаны тану, пікірлерді белгілеу | API арқылы сыртқы тілдік модель |\n"
        "| База | SQLite, pandas |"))
    st.subheader(L("Персональные данные", "Дербес деректер"))
    st.markdown(L("Псевдонимы вместо имён; фото не сохраняется — после распознавания остаются только строки текста; "
                  "реальные работы — только с согласия. В демо все ученики вымышленные.",
                  "Аттардың орнына бүркеншік аттар; фото сақталмайды — танылғаннан кейін тек мәтін жолдары қалады; "
                  "нақты жұмыстар — тек келісіммен. Демода барлық оқушы ойдан шығарылған."))


{"class": page_class, "portrait": page_portrait, "check": page_check, "log": page_log,
 "quality": lambda: review_view.render_quality(conn, L, lang, esc, student_ids=auth.visible_student_ids(conn, user)),
 "manage": lambda: manage_view.render(conn, user, L, lang),
 "practice": lambda: practice_view.render(conn, L, lang, esc, badge, render_lines, students, role=user["role"]),
 "about": page_about}[page]()
ss["_last_lang"] = lang

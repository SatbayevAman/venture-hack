"""Страница «🧩 Тренажёр»: разбор своей ошибки и задания под слабое место.

Логика — в core/practice.py и core/hints.py; здесь только интерфейс.
Ученику показываем номер строки и подсказку, но никогда — правильный ответ.
"""
from __future__ import annotations

import html
import random

import streamlit as st

from core import hints, practice
from core import tags as T

ss = st.session_state

LEVEL_TITLE = {1: ("Вопрос", "Сұрақ"), 2: ("Правило", "Ереже"), 3: ("Разобранный пример", "Талданған мысал")}
VERDICT_BADGE = {"self_check": "b-ok", "needs_lesson": "b-weak", "low_data": "b-low", "mixed": "b-low"}


def _wkey(name: str, default, lang: str, options: list) -> str:
    """Значение виджета общее для обоих языков (ключ виджета зависит от языка, как sticky() в app.py)."""
    if ss.get(name) not in options:
        ss[name] = default
    wkey = f"pr_w_{name}_{lang}"
    if ss.get(wkey) != ss[name]:
        ss[wkey] = ss[name]
    return wkey


def _sync(name: str, wkey: str):
    return lambda: ss.__setitem__(name, ss[wkey])


# ------------------------------------------------------------------ страница

def render(conn, L, lang, esc, badge, render_lines, students):
    practice.ensure_schema(conn)
    st.title(L("🧩 Тренажёр", "🧩 Жаттықтырғыш"))
    st.caption(L("Разбор своей ошибки и задачи под слабое место. Подсказки идут по ступеням: вопрос → правило → "
                 "разобранный пример. Готовый ответ тренажёр не показывает.",
                 "Өз қатеңді талдау және әлсіз тұсқа арналған есептер. Көмек сатылап беріледі: сұрақ → ереже → "
                 "талданған мысал. Жаттықтырғыш дайын жауапты көрсетпейді."))
    studs = list(students())  # фильтр видимости (роль «ученик») — внутри students() из app.py
    if not studs:
        st.info(L("Нет доступных учеников.", "Қолжетімді оқушы жоқ."))
        return
    alias = {s["id"]: s["alias"] for s in studs}
    if "set_student_id" in ss:  # переход go("practice", id) из портрета
        ss["student_id"] = ss.pop("set_student_id")
    c = st.columns([2, 3])
    k = _wkey("student_id", studs[0]["id"], lang, list(alias))
    sid = c[0].selectbox(L("Ученик", "Оқушы"), list(alias), key=k, format_func=lambda i: alias[i],
                         on_change=_sync("student_id", k))
    modes = ["fix_own", "weak_spot"]
    k = _wkey("pr_mode", "fix_own", lang, modes)
    mode = c[1].radio(L("Режим", "Режим"), modes, key=k, horizontal=True, on_change=_sync("pr_mode", k),
                      format_func=lambda m: {"fix_own": L("🔍 Разобрать свою ошибку", "🔍 Өз қатеңді талдау"),
                                             "weak_spot": L("🎯 Задания под слабое место",
                                                            "🎯 Әлсіз тұсқа арналған тапсырмалар")}[m])

    _reviews(conn, L, lang, esc, sid)
    st.divider()
    if mode == "fix_own":
        _fix_own(conn, L, lang, esc, badge, render_lines, sid)
    else:
        _weak_spot(conn, L, lang, esc, badge, render_lines, sid)

    st.divider()
    with st.expander(L("👩‍🏫 Для учителя: сводка по классу", "👩‍🏫 Мұғалімге: сынып бойынша жиынтық")):
        _class_block(conn, L, lang, esc, badge, studs)


# ------------------------------------------------------------------ задача: решение → проверка → подсказки

def _on_check(conn, run: dict, item_id_fn, key: str):
    def cb():
        item_id = item_id_fn()
        level = run["hint"].get(item_id, 0)
        r = practice.submit_attempt(conn, item_id, (ss.get(key) or "").splitlines(), level)
        run["last"][item_id] = r
        fe = r["result"].first_error
        if fe:
            run["fe"][item_id] = fe
            run["hint"][item_id] = max(level, 1)  # после ошибки сразу виден наводящий вопрос
    return cb


def _on_hint(run: dict, item_id: int):
    def cb():
        run["hint"][item_id] = min(3, run["hint"].get(item_id, 0) + 1)
    return cb


def _ladder(L, lang, esc, render_lines, item: dict, fe: dict, level: int):
    for lv in range(1, level + 1):
        title = L(*LEVEL_TITLE[lv])
        if lv < 3:
            st.markdown(f'<div class="q"><b>💡 {lv}. {esc(title)}</b><br>'
                        f'{esc(hints.hint(fe, lv, lang, item["statement"], item["skill"]))}</div>',
                        unsafe_allow_html=True)
            continue
        ex = hints.example(fe, item["statement"], item["skill"], family=item.get("tag"))
        if not ex:
            st.markdown(f'<div class="q"><b>💡 3. {esc(title)}</b><br>{esc(hints.NO_EXAMPLE[lang])}</div>',
                        unsafe_allow_html=True)
            continue
        rows = [{"line_no": i + 1, "raw_text": line + ("    " + ex["note"][lang] if i == ex["key_line"] else ""),
                 "status": "ok", "kind": "text", "note": ""} for i, line in enumerate(ex["reference"])]
        st.markdown(f'<div class="q"><b>💡 3. {esc(title)}</b><br>{esc(hints.EXAMPLE_HEAD[lang])}'
                    + render_lines(rows, ex["statement"], ex["key_line"] + 1)
                    + f'{esc(hints.EXAMPLE_TAIL[lang])}</div>', unsafe_allow_html=True)


def _feedback(L, lang, esc, badge, render_lines, run: dict, item: dict):
    """Результат последней попытки и лестница подсказок."""
    item_id = item["id"]
    r = run["last"].get(item_id)
    level = run["hint"].get(item_id, 0)
    fe = run["fe"].get(item_id)
    if r and r["ok"]:
        had_error = bool(fe) or item.get("mode") == "fix_own"
        st.markdown(render_lines(r["result"].lines, item["statement"]), unsafe_allow_html=True)
        st.success(L("Исправлено! 🎉", "Түзетілді! 🎉") if had_error else
                   L("Верно с первой попытки! 🎉", "Бірінші әрекеттен дұрыс! 🎉"))
        msg = {"self_fixed": L("Ты справился(-ась) после одного вопроса — значит, правило ты знаешь.",
                               "Бір сұрақтан кейін-ақ шығардың — демек, ережені білесің."),
               "fixed_after_rule": L("Правило помогло — запиши его на полях и держи перед глазами.",
                                     "Ереже көмектесті — оны дәптердің шетіне жазып, көз алдыңда ұста."),
               "needs_example": L("Пример помог. Через пару дней вернёмся к этой задаче, чтобы закрепить.",
                                  "Мысал көмектесті. Бекіту үшін бірнеше күннен кейін осы есепке ораламыз.")}
        if r["outcome"]:
            st.caption(msg[r["outcome"]])
        return
    if r:
        res = r["result"]
        if r["reason"] == "error":
            n = res.first_error["line"]
            st.markdown(render_lines(res.lines, item["statement"], n), unsafe_allow_html=True)
            st.warning(L(f"Посмотри на строку {n} — здесь что-то пошло не так.",
                         f"{n}-жолға қара — осы жерде бірдеңе дұрыс болмай тұр."))
        elif r["reason"] == "need_check":
            st.info(L("Решение верное! Осталось сделать проверку: подставь ответ в исходное уравнение отдельной "
                      "строкой, например «Проверка: …».",
                      "Шешім дұрыс! Тек тексеру қалды: жауапты бастапқы теңдеуге жеке жолда қой, "
                      "мысалы «Тексеру: …»."))
        elif r["reason"] == "need_steps":
            k = item["requirement"].get("min_lines", 2)
            st.info(L(f"Ответ верный, но шагов маловато: запиши каждое преобразование отдельной строкой "
                      f"(нужно хотя бы {k}).",
                      f"Жауап дұрыс, бірақ қадамдар аз: әр түрлендіруді жеке жолға жаз (кемінде {k} жол керек)."))
        else:
            st.info(L("Решение пока не доведено до конца: допиши ответ (выражение — упрости до конца).",
                      "Шешім әлі соңына жеткен жоқ: жауапты жаз (өрнекті соңына дейін ықшамда)."))
    if fe and level:
        _ladder(L, lang, esc, render_lines, item, fe, level)


def _requirement_note(L, item: dict) -> str:
    req = item.get("requirement") or {}
    if req.get("check"):
        return L("Сделай проверку подстановкой — без неё задача не засчитывается.",
                 "Орнына қою арқылы тексер — онсыз есеп есептелмейді.")
    if req.get("min_lines"):
        k = req["min_lines"]
        return L(f"Записывай каждый шаг отдельной строкой (не меньше {k} строк).",
                 f"Әр қадамды жеке жолға жаз (кемінде {k} жол).")
    return ""


# ------------------------------------------------------------------ режим «разобрать свою ошибку»

def _fix_own(conn, L, lang, esc, badge, render_lines, sid: int):
    errs = practice.own_errors(conn, sid)
    if not errs:
        st.success(L("В сохранённых работах ошибок нет — загляни в «Задания под слабое место».",
                     "Сақталған жұмыстарда қате жоқ — «Әлсіз тұсқа арналған тапсырмаларға» көз жүгірт."))
        return
    opts = list(range(len(errs)))

    def label(i):
        e = errs[i]
        live = " 🟣" if e["source"] == "live" else ""
        return f"{L('ДЗ', 'ҮТ')} №{e['work_no']} · №{e['idx']} · {e['statement']}{live}"

    i = st.selectbox(L("Работа с ошибкой (свежие сверху)", "Қатесі бар жұмыс (жаңалары жоғарыда)"), opts,
                     format_func=label, key=f"pr_err_{sid}_{lang}")
    e = errs[i]
    rkey = f"pr_fix_{sid}_{e['submission_id']}_{e['problem_id']}"
    if rkey not in ss:
        res0 = practice.original_check(conn, e["submission_id"], e["problem_id"])
        ss[rkey] = {"item_id": None, "res0": res0, "hint": {}, "last": {}, "fe": {}, "ver": ss.get(rkey + "_n", 0)}
    run = ss[rkey]
    res0 = run["res0"]
    fe0 = res0.first_error or {"tag": e["tag"] or "other", "line": e["first_error_line"], "detail": {}}
    n = fe0["line"]

    st.markdown(f"#### {esc(e['statement'])} "
                + badge(T.skill_name(e["skill"], lang), "b-low"), unsafe_allow_html=True)
    st.markdown(L(f"Твоё решение. Строка {n} отмечена — с неё что-то пошло не так.",
                  f"Сенің шешімің. {n}-жол белгіленген — осы жерден бірдеңе дұрыс болмай қалды."))
    st.markdown(render_lines(res0.lines, e["statement"], n), unsafe_allow_html=True)

    item_id = run["item_id"]
    item = practice.get_item(conn, item_id) if item_id else {
        "id": None, "statement": e["statement"], "skill": e["skill"], "requirement": {}, "mode": "fix_own",
        "tag": fe0["tag"]}
    if item_id is None:  # до первой попытки виден вопрос из проверки работы
        run["fe"].setdefault(None, fe0)
        run["hint"].setdefault(None, 1)
    key = f"pr_lines_{rkey}_{run['ver']}"
    if key not in ss:
        ss[key] = "\n".join(practice.prefill(practice.original_lines(conn, e["submission_id"], e["problem_id"]), n))
    solved = bool(item_id and item.get("solved"))

    def ensure_item():
        if run["item_id"] is None:
            run["item_id"] = practice.start_fix_own(conn, sid, e["submission_id"], e["problem_id"])
            for d in ("hint", "fe"):
                run[d][run["item_id"]] = run[d].pop(None, None) or (1 if d == "hint" else fe0)
        return run["item_id"]

    if not solved:
        st.text_area(L("Реши заново с отмеченной строки — по одной строке решения на строку. Строки до ошибки уже вписаны.",
                       "Белгіленген жолдан бастап қайта шығар — әр жолға шешімнің бір жолы. Қатеге дейінгі жолдар жазылып қойылған."),
                     key=key, height=140)
        b = st.columns([1, 1, 2])
        b[0].button(L("Проверить", "Тексеру"), type="primary", use_container_width=True, key=f"{key}_chk",
                    on_click=_on_check(conn, run, ensure_item, key))
        cur = run["item_id"]
        if run["hint"].get(cur, 0) < 3:
            b[1].button(L("💡 Ещё подсказка", "💡 Тағы көмек"), use_container_width=True, key=f"{key}_hint",
                        on_click=_on_hint(run, cur))
    _feedback(L, lang, esc, badge, render_lines, run, dict(item, id=run["item_id"], mode="fix_own"))
    if solved:
        def restart():
            ss[rkey + "_n"] = ss.get(rkey + "_n", 0) + 1
            ss.pop(rkey, None)
        st.button(L("Разобрать ещё раз", "Қайта талдау"), on_click=restart, key=f"{key}_again")


# ------------------------------------------------------------------ режим «задания под слабое место»

def _new_run(session_id: int, ids: list, kind: str) -> dict:
    return {"session_id": session_id, "items": ids, "pos": 0, "hint": {}, "last": {}, "fe": {}, "kind": kind}


def _weak_spot(conn, L, lang, esc, badge, render_lines, sid: int):
    rkey = f"pr_run_{sid}"
    run = ss.get(rkey)
    if run is None or run["pos"] >= len(run["items"]):
        if run is not None and run["items"]:
            solved = sum(1 for i in run["items"] if practice.get_item(conn, i)["solved"])
            st.success(L(f"Набор пройден: решено {solved} из {len(run['items'])}.",
                         f"Топтама аяқталды: {len(run['items'])} есептің {solved}-і шығарылды."))
        targets = practice.targets_for(conn, sid, lang)
        opts = list(range(len(targets)))
        c = st.columns([3, 1])
        ti = c[0].selectbox(L("Что тренируем", "Нені жаттықтырамыз"), opts, key=f"pr_target_{sid}_{lang}",
                            format_func=lambda i: targets[i]["title"] + (f" — {targets[i]['why']}" if targets[i]["why"] else ""))
        n = c[1].selectbox(L("Сколько задач", "Неше есеп"), [3, 4, 5], index=1, key=f"pr_n_{lang}")

        def start():
            session_id, ids = practice.start_weak_spot(conn, sid, targets[ti], n=n, seed=random.randrange(10 ** 9))
            if ids:
                ss[rkey] = _new_run(session_id, ids, "weak_spot")

        st.button(L("Подобрать задания", "Тапсырмаларды таңдау") if run is None else L("Ещё набор", "Тағы бір топтама"),
                  type="primary", on_click=start)
        return
    _runner(conn, L, lang, esc, badge, render_lines, run, rkey)


def _runner(conn, L, lang, esc, badge, render_lines, run: dict, rkey: str):
    total = len(run["items"])
    item_id = run["items"][run["pos"]]
    item = practice.get_item(conn, item_id)
    st.progress(run["pos"] / total, text=L(f"Задача {run['pos'] + 1} из {total}", f"{total} есептің {run['pos'] + 1}-і"))
    head = badge(T.skill_name(item["skill"], lang), "b-low")
    if run["kind"] == "review":
        head += badge(L("повторение", "қайталау"), "b-conf")
    st.markdown(f"#### {esc(item['statement'])} {head}", unsafe_allow_html=True)
    note = _requirement_note(L, item)
    if note:
        st.caption(note)
    key = f"pr_lines_item_{item_id}"
    if not item["solved"]:
        st.text_area(L("Твоё решение — по одной строке на строку", "Сенің шешімің — әр жолға бір жол"), key=key,
                     height=140)
    b = st.columns([1, 1, 1, 1])
    if not item["solved"]:
        b[0].button(L("Проверить", "Тексеру"), type="primary", use_container_width=True, key=f"{key}_chk",
                    on_click=_on_check(conn, run, lambda: item_id, key))
        if run["fe"].get(item_id) and run["hint"].get(item_id, 0) < 3:
            b[1].button(L("💡 Ещё подсказка", "💡 Тағы көмек"), use_container_width=True, key=f"{key}_hint",
                        on_click=_on_hint(run, item_id))

    def nxt():
        if not practice.get_item(conn, item_id)["solved"]:
            practice.abandon_item(conn, item_id, run["hint"].get(item_id, 0))
        run["pos"] += 1
        if run["pos"] >= total:
            practice.finish_session(conn, run["session_id"])

    b[3].button(L("Дальше →", "Келесі →") if item["solved"] else L("Пропустить", "Өткізіп жіберу"),
                use_container_width=True, key=f"{key}_next", on_click=nxt)
    _feedback(L, lang, esc, badge, render_lines, run, item)


# ------------------------------------------------------------------ интервальное повторение

def _reviews(conn, L, lang, esc, sid: int):
    rv = practice.reviews(conn, sid)
    if rv["due"]:
        c = st.columns([4, 1])
        nd = len(rv["due"])
        c[0].markdown(f'<div class="q"><b>{esc(L(f"🔁 Пора повторить: {nd}", f"🔁 Қайталайтын уақыт келді: {nd}"))}</b><br>'
                      + esc(L("Эти задачи получились только с разобранным примером — вернёмся к ним, чтобы закрепить.",
                              "Бұл есептер тек талданған мысалмен шықты — бекіту үшін оларға қайта ораламыз."))
                      + "<br><span class='muted'>" + esc("; ".join(r["statement"] for r in rv["due"][:5])) + "</span></div>",
                      unsafe_allow_html=True)

        def start():
            session_id, ids = practice.start_review(conn, sid, [r["id"] for r in rv["due"]])
            ss[f"pr_run_{sid}"] = _new_run(session_id, ids, "review")
            ss["pr_mode"] = "weak_spot"

        c[1].button(L("Повторить", "Қайталау"), type="primary", use_container_width=True, on_click=start)
    elif rv["later"]:
        st.caption(L("🔁 Следующее повторение: ", "🔁 Келесі қайталау: ") + rv["later"][0]["due_at"][:10])


# ------------------------------------------------------------------ для учителя

def _outcomes_line(L, o: dict) -> str:
    return (L("сам(а) по вопросу", "сұрақ бойынша өзі") + f" {o['self_fixed']} · "
            + L("после правила", "ережеден кейін") + f" {o['fixed_after_rule']} · "
            + L("нужен пример", "мысал керек") + f" {o['needs_example']}")


def _class_block(conn, L, lang, esc, badge, studs):
    groups = practice.class_summary(conn, studs)
    if not groups:
        st.caption(L("Пока никто не занимался в тренажёре.", "Әзірге жаттықтырғышта ешкім жаттықпаған."))
        return
    titles = {"self_check": L("Исправляют сами по вопросу — нужна привычка самопроверки",
                              "Сұрақ бойынша өздері түзетеді — өзін-өзі тексеру әдеті керек"),
              "needs_lesson": L("Нужен разбор темы на уроке", "Тақырыпты сабақта талдау керек"),
              "mixed": L("Картина неоднозначная", "Көрініс біржақты емес"),
              "low_data": L("Мало данных (меньше 3 задач с ошибкой)", "Дерек аз (қатесі бар есеп 3-тен аз)")}
    for v in ("self_check", "needs_lesson", "mixed", "low_data"):
        rows = groups.get(v)
        if not rows:
            continue
        st.markdown(f"{badge(titles[v], VERDICT_BADGE[v])}", unsafe_allow_html=True)
        for _, alias, sm in rows:
            tags = ", ".join(T.name(r["tag"], lang) for r in sm["by_tag"] if r["tag"] in T.TAGS)
            st.markdown(f"- **{esc(alias)}** — " + L(f"решено {sm['solved']} из {sm['tried']}",
                                                    f"{sm['tried']} есептің {sm['solved']}-і шықты")
                        + f" · {esc(_outcomes_line(L, sm['outcomes']))}"
                        + (f" <span class='muted'>({esc(tags)})</span>" if tags else ""), unsafe_allow_html=True)


def render_portrait_block(conn, student_id: int, L, lang):
    """Блок «Тренажёр» в портрете учителя. Ничего не выводит, если тренировок не было."""
    practice.ensure_schema(conn)
    s = practice.summary(conn, student_id)
    if not s["has_data"]:
        return
    esc = lambda x: html.escape(str(x))  # noqa: E731
    st.subheader(L("🧩 Тренажёр", "🧩 Жаттықтырғыш"))
    c = st.columns(5)
    c[0].metric(L("Задач решено", "Шығарылған есеп"), f"{s['solved']}/{s['tried']}")
    c[1].metric(L("С первой попытки", "Бірінші әрекеттен"), s["first_try"])
    c[2].metric(L("Сам(а) по вопросу", "Сұрақ бойынша өзі"), s["outcomes"]["self_fixed"])
    c[3].metric(L("После правила", "Ережеден кейін"), s["outcomes"]["fixed_after_rule"])
    c[4].metric(L("Нужен пример", "Мысал керек"), s["outcomes"]["needs_example"])
    v = s["verdict"]
    st.markdown(f'<div class="card"><span class="badge {VERDICT_BADGE[v]}">{esc(L("вывод", "қорытынды"))}</span> '
                f'{esc(practice.VERDICTS[v][lang])}</div>', unsafe_allow_html=True)
    for r in s["by_tag"]:
        name = T.name(r["tag"], lang) if r["tag"] in T.TAGS else L("Закрепление", "Бекіту")
        st.markdown(f"- **{esc(name)}** — " + L(f"решено {r['solved']} из {r['tried']}",
                                                 f"{r['tried']} есептің {r['solved']}-і шықты")
                    + f" · {esc(_outcomes_line(L, r['outcomes']))}"
                    + f" <span class='muted'>· {esc(practice.VERDICTS[r['verdict']][lang])}</span>",
                    unsafe_allow_html=True)
    if s["refs"]:
        with st.expander(L(f"Доказательства из журнала ({len(s['refs'])})", f"Журналдағы дәлелдер ({len(s['refs'])})")):
            for r in s["refs"][:12]:
                st.markdown(f"- {esc(r['created_at'][:16])} · **{esc(T.name(r['tag'], lang))}** · "
                            + L(f"ступень {r['hint_level']}", f"{r['hint_level']}-саты") + f" — `{esc(r['evidence'])}`")
    st.caption(L("Источник — журнал наблюдений (source = practice). В числа портрета выше тренировки не входят.",
                 "Дереккөзі — бақылау журналы (source = practice). Жаттығулар жоғарыдағы портрет сандарына кірмейді."))

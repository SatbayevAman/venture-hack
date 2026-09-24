"""Учитель в контуре: отметки на выводах, «это не ошибка», оценка портрета, страница «Качество».

Логика — в core/review.py и core/quality.py; здесь только Streamlit.
L — функция L(ru, kk) из app.py; язык определяется по ней же.
"""
from __future__ import annotations

import html
import time

import pandas as pd
import streamlit as st

from core import db, quality, review
from core import tags as T

TIMER_RESET = 30 * 60   # «Проверить» позже чем через 30 мин после первого — это уже новая проверка


def _lang(L) -> str:
    return L("ru", "kk")


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _pct(x) -> str:
    return f"{round(x * 100)} %"


# ---------------------------------------------------------------- отметка на выводе

def mark_label(m: dict | None, L) -> str:
    """Короткая подпись последней отметки — для бейджа и колонки журнала."""
    if not m:
        return ""
    if m["verdict"] == "confirm":
        return L("✓ верно", "✓ дұрыс")
    if m["verdict"] == "reject":
        return L("✗ неверно", "✗ дұрыс емес")
    return L("↻ другой тег: ", "↻ басқа тег: ") + T.name(m["new_tag"], _lang(L))


def mark_badge(m: dict | None, L) -> str:
    if not m:
        return f'<span class="badge b-low">{_e(L("нет отметки учителя", "мұғалім белгісі жоқ"))}</span>'
    cls = {"confirm": "b-ok", "reject": "b-weak", "retag": "b-conf"}[m["verdict"]]
    extra = ""
    if m["verdict"] == "reject":
        extra = L(" · не учитывается в портрете", " · портретте ескерілмейді")
    who = f'{m.get("reviewer") or ""}, {str(m.get("created_at") or "")[:16]}'.strip(", ")
    note = f' <span class="muted">«{_e(m["comment"])}»</span>' if m.get("comment") else ""
    return f'<span class="badge {cls}" title="{_e(who)}">{_e(mark_label(m, L) + extra)}</span>{note}'


def controls(conn, obs_id, L, key: str) -> None:
    """Три кнопки на выводе: «✓ верно», «✗ неверно», «↻ другой тег» + необязательный комментарий."""
    if obs_id is None:
        return
    o = db.q1(conn, "SELECT id, kind, tag, source FROM observations WHERE id=?", (obs_id,))
    if o is None or o["source"] not in ("auto", "teacher"):
        return
    lang = _lang(L)
    m = review.latest(conn, [obs_id]).get(obs_id)
    st.markdown(f'<div class="muted" style="margin:2px 0 4px 0">{_e(L("Отметка учителя:", "Мұғалім белгісі:"))} '
                f'{mark_badge(m, L)}</div>', unsafe_allow_html=True)
    c = st.columns([1, 1, 1.25, 2.6])
    comment = c[3].text_input(L("Комментарий", "Пікір"), key=f"{key}_c", label_visibility="collapsed",
                              placeholder=L("комментарий (необязательно)", "пікір (міндетті емес)"))
    reject_label = L("✗ неверно", "✗ дұрыс емес") if o["source"] == "auto" else L("✗ снять тег", "✗ тегті алу")
    verdict = new_tag = None
    if c[0].button(L("✓ верно", "✓ дұрыс"), key=f"{key}_ok", use_container_width=True,
                   disabled=bool(m and m["verdict"] == "confirm")):
        verdict = "confirm"
    if c[1].button(reject_label, key=f"{key}_no", use_container_width=True,
                   disabled=bool(m and m["verdict"] == "reject")):
        verdict = "reject"
    opts = review.retag_options(o["kind"], o["source"], exclude=o["tag"])
    with c[2].popover(L("↻ другой тег", "↻ басқа тег"), use_container_width=True, disabled=not opts):
        new = st.selectbox(L("Правильный тег", "Дұрыс тег"), opts, key=f"{key}_t",
                           format_func=lambda t: T.name(t, lang))
        if st.button(L("Сохранить", "Сақтау"), key=f"{key}_rt", type="primary"):
            verdict, new_tag = "retag", new
    if verdict:
        try:
            review.add(conn, obs_id, verdict, new_tag=new_tag, comment=comment)
        except ValueError as ex:
            st.error(str(ex))
            return
        st.session_state.pop(f"{key}_c", None)
        st.rerun()


def precision_badge(conn, tag: str, L, student_ids=None) -> str:
    """Бейдж «требует проверки», если по отметкам учителя правило для тега часто ошибается.
    student_ids — те же ученики, что на странице «Качество» у этого пользователя."""
    d = quality.tag_status(conn, tag, student_ids=student_ids)
    if not d or not d["needs_review"]:
        return ""
    text = L(f"требует проверки: точность {_pct(d['precision'])}, отметок: {d['reviewed']}",
             f"тексеруді қажет етеді: {d['reviewed']} белгі бойынша дәлдігі {_pct(d['precision'])}")
    return f'<span class="badge b-low">{_e(text)}</span>'


# ---------------------------------------------------------------- экран проверки

def timer_start(ss, sid: int, aid: int) -> None:
    """«Проверить»: запомнить начало проверки этой работы (повторное «Проверить» не сбрасывает)."""
    t = ss.get("eps_timer")
    now = time.time()
    if not t or (t["sid"], t["aid"]) != (sid, aid) or now - t["t0"] > TIMER_RESET:
        ss["eps_timer"] = {"sid": sid, "aid": aid, "t0": now}


def not_error_toggle(aid: int, pid: int, L) -> None:
    """B4: «Это не ошибка» у первой ошибки до записи."""
    st.checkbox(L("Это не ошибка — записать работу, но вывод сразу отметить «неверно»",
                  "Бұл қате емес — жұмысты жазу, бірақ қорытындыны бірден «дұрыс емес» деп белгілеу"),
                key=f"eps_noterr_{aid}_{pid}")


def after_record(conn, ss, sub_id: int, sid: int, aid: int, chk: dict, L) -> None:
    """После записи в журнал: время проверки и отметки «это не ошибка»."""
    t = ss.pop("eps_timer", None)
    if t and (t["sid"], t["aid"]) == (sid, aid):
        res = chk.get("results") or {}
        quality.record_timing(conn, sub_id, sid, time.time() - t["t0"], n_problems=len(res),
                              n_lines=sum(len(r.lines) for r in res.values()))
    pids = [pid for pid in (chk.get("results") or {}) if ss.pop(f"eps_noterr_{aid}_{pid}", False)]
    if pids:
        review.reject_errors(conn, sub_id, pids, comment=L("до записи: это не ошибка", "жазар алдында: бұл қате емес"))


def submission_controls(conn, sub_id, L) -> None:
    """Отметки для ошибок только что записанной работы и для тегов из комментария учителя."""
    if not sub_id:
        return
    lang = _lang(L)
    obs = review.for_submission(conn, sub_id)
    errs = [o for o in obs if o["source"] == "auto" and o["kind"] == "error"]
    tchr = [o for o in obs if o["source"] == "teacher"]
    if not errs and not tchr:
        return
    with st.expander(L("✓ ✗ Отметьте выводы проверки — портрет пересчитается",
                       "✓ ✗ Тексеру қорытындыларын белгілеңіз — портрет қайта есептеледі"), expanded=True):
        for o in errs:
            where = L(f"№{o['problem_idx']}, строка {o['line_no']}", f"№{o['problem_idx']}, {o['line_no']}-жол")
            st.markdown(f"**{_e(where)}: {_e(T.name(o['tag'], lang))}** — `{_e(o['evidence'])}`",
                        unsafe_allow_html=True)
            controls(conn, o["id"], L, key=f"eps_sub_{o['id']}")
        if tchr:
            st.markdown("**" + L("Теги из комментария учителя", "Мұғалім пікіріндегі тегтер") + "**")
            for o in tchr:
                st.markdown(f"«{_e(o['evidence'])}» → **{_e(T.name(o['tag'], lang))}**", unsafe_allow_html=True)
                controls(conn, o["id"], L, key=f"eps_sub_{o['id']}")


# ---------------------------------------------------------------- журнал

def log_marks(conn, ids, L) -> dict:
    """{observation_id: подпись последней отметки} — для колонки «Отметка учителя»."""
    return {i: mark_label(m, L) for i, m in review.latest(conn, ids).items()}


def log_controls(conn, row, L) -> None:
    """Отметка выбранной строки журнала."""
    lang = _lang(L)
    st.markdown(f"**{_e(row['alias'])} · {_e(T.name(row['tag'], lang))}** — `{_e(row['evidence'])}`",
                unsafe_allow_html=True)
    if row["source"] not in ("auto", "teacher"):
        st.caption(L("Отметки ставятся на выводы автопроверки и теги из комментариев.",
                     "Белгілер автотексеру қорытындыларына және пікір тегтеріне қойылады."))
        return
    controls(conn, row["id"], L, key=f"eps_log_{row['id']}")


# ---------------------------------------------------------------- похожесть портрета (B3)

def rating_form(conn, p: dict, L) -> None:
    lang = _lang(L)
    sid = p["student"]["id"]
    items = {}
    for e in p["errors"]:
        if e["weak"]:
            items[f"err:{e['skill']}:{e['tag']}"] = (L("Слабое место: ", "Әлсіз тұс: ") + T.name(e["tag"], lang)
                                                     + " · " + T.skill_name(e["skill"], lang).lower())
    for s in p["strengths"]:
        items[f"strength:{s['skill']}"] = L("Получается: ", "Жақсы шығады: ") + T.skill_name(s["skill"], lang)
    for t in p["teacher"]:
        items[f"teacher:{t['tag']}"] = L("Учитель пишет: ", "Мұғалім жазады: ") + T.name(t["tag"], lang)
    for r in p["recs"]:
        items[f"rec:{r['key']}"] = L("Совет: ", "Кеңес: ") + r["title"]
    prev = review.ratings(conn, sid)
    with st.expander(L("🎯 Похоже ли это на ученика? Оцените портрет", "🎯 Бұл оқушыға ұқсай ма? Портретті бағалаңыз")):
        if prev:
            last = prev[-1]
            st.caption(L(f"Последняя оценка: {last['score']}/5 ({last['created_at'][:16]}), всего оценок: {len(prev)}",
                         f"Соңғы баға: {last['score']}/5 ({last['created_at'][:16]}), барлық баға: {len(prev)}"))
        with st.form(key=f"eps_rate_{sid}", clear_on_submit=True):
            score = st.radio(L("1 — совсем не похоже, 5 — очень похоже", "1 — мүлде ұқсамайды, 5 — өте ұқсайды"),
                             [1, 2, 3, 4, 5], index=None, horizontal=True)
            wrong = st.multiselect(L("Какие пункты неверны?", "Қай тармақтар дұрыс емес?"), list(items),
                                   format_func=lambda k: items.get(k, k))
            comment = st.text_input(L("Комментарий (необязательно)", "Пікір (міндетті емес)"))
            if st.form_submit_button(L("Сохранить оценку", "Бағаны сақтау")):
                if score is None:
                    st.warning(L("Выберите оценку от 1 до 5.", "1-ден 5-ке дейін баға таңдаңыз."))
                else:
                    review.add_rating(conn, sid, score, wrong, comment)
                    st.success(L("Оценка сохранена — она попадёт на страницу «Качество».",
                                 "Баға сақталды — ол «Сапа» бетіне түседі."))


# ---------------------------------------------------------------- страница «Качество»

def render_quality(conn, L, lang: str, esc, student_ids=None) -> None:
    """student_ids — ученики, видимые пользователю (auth.visible_student_ids); None — все."""
    st.title(L("Качество выводов", "Қорытындылар сапасы"))
    st.caption(L("Все цифры — только из отметок учителя и замеров в этом приложении, ничего не оценивается «на глаз». "
                 "Отметки ставятся в доказательствах портрета, на экране проверки после записи и в журнале.",
                 "Барлық сан — тек мұғалім белгілері мен осы қосымшадағы өлшеулерден, ештеңе «көзбен» бағаланбайды. "
                 "Белгілер портрет дәлелдерінде, жазғаннан кейін тексеру экранында және журналда қойылады."))
    rows = quality.tag_precision(conn, student_ids=student_ids)
    ov = quality.overall_precision(rows)
    ev = quality.eval_accuracy()
    tm = quality.timing_summary(conn, student_ids)
    rs = review.rating_summary(conn, student_ids)

    c = st.columns(4)
    c[0].metric(L("Точность правил по отметкам", "Белгілер бойынша ережелер дәлдігі"),
                _pct(ov["precision"]) if ov else "—",
                help=L("верно / отмечено по всем тегам автопроверки", "дұрыс / белгіленген, автотексерудің барлық тегі"))
    c[0].caption(L(f"отметок: {ov['reviewed']}", f"белгі: {ov['reviewed']}") if ov else L("отметок нет", "белгі жоқ"))
    c[1].metric(L("Первая ошибка на размеченных", "Белгіленгендегі алғашқы қате"),
                f"{ev['ok_tag']}/{ev['n']}" if ev["available"] and ev["n"] else "—")
    c[1].caption(L("строка и тег, eval/labels.csv", "жол және тег, eval/labels.csv"))
    c[2].metric(L("Медиана проверки", "Тексеру медианасы"),
                f"{tm['median_seconds']:.0f} {L('с', 'с')}" if tm["median_seconds"] is not None else "—")
    c[2].caption(L(f"на работу, замеров: {tm['n']}", f"бір жұмысқа, өлшеу: {tm['n']}"))
    c[3].metric(L("Похожесть портрета", "Портрет ұқсастығы"), f"{rs['mean']:.1f} / 5" if rs["mean"] is not None else "—")
    c[3].caption(L(f"оценок: {rs['n']}", f"баға: {rs['n']}"))

    # ---- 1. точность тегов
    st.header(L("1. Точность правил по отметкам учителя", "1. Мұғалім белгілері бойынша ережелер дәлдігі"))
    if not rows:
        st.info(L("Отметок пока нет. Откройте портрет → «Доказательства» и отметьте выводы «✓ верно» или «✗ неверно».",
                  "Әзірге белгі жоқ. Портрет → «Дәлелдер» бөлімін ашып, қорытындыларды «✓ дұрыс» не «✗ дұрыс емес» деп белгілеңіз."))
    else:
        if ov and ov["synthetic"]:
            st.markdown(f'<span class="badge b-syn">{esc(L("данные синтетические", "синтетикалық деректер"))}</span> '
                        + esc(L(f"Отметок на синтетических работах демо: {ov['synthetic']} из {ov['reviewed']}. "
                                "Для слайда нужны отметки на реальных работах.",
                                f"{ov['reviewed']} белгінің {ov['synthetic']} — демодағы синтетикалық жұмыстарда. "
                                "Слайд үшін нақты жұмыстардағы белгілер керек.")), unsafe_allow_html=True)
        st.dataframe(_precision_df(rows, L, lang), hide_index=True, use_container_width=True)
        st.caption(L(f"«Требует проверки» — точность ниже {_pct(quality.PRECISION_MIN)} при не менее чем "
                     f"{quality.REVIEWS_MIN} отметках. Меньше отметок — статус не ставится.",
                     f"«Тексеруді қажет етеді» — кемінде {quality.REVIEWS_MIN} белгіде дәлдік "
                     f"{_pct(quality.PRECISION_MIN)}-дан төмен. Белгі аз болса — күй қойылмайды."))
    trows = quality.tag_precision(conn, "teacher", student_ids)
    if trows:
        st.markdown("**" + L("Теги из комментариев учителя (разметка моделью или словарём)",
                             "Мұғалім пікірлеріндегі тегтер (модель не сөздік белгілеген)") + "**")
        st.dataframe(_precision_df(trows, L, lang), hide_index=True, use_container_width=True)

    # ---- 2. кандидаты на исправление правил
    st.header(L("2. Кандидаты на исправление правил", "2. Ережелерді түзетуге үміткерлер"))
    bad = [d["tag"] for d in rows if d["needs_review"]]
    show_all = st.checkbox(L("Показать отклонённые случаи по всем тегам", "Барлық тег бойынша қабылданбаған жағдайларды көрсету"),
                           value=not bad, key="eps_q_all")
    tags = None if show_all else bad
    cases = quality.rejected_cases(conn, tags, student_ids=student_ids) if (show_all or bad) else []
    if not cases:
        st.caption(L("Нет тегов со статусом «требует проверки» и отклонённых случаев.",
                     "«Тексеруді қажет етеді» күйіндегі тег және қабылданбаған жағдай жоқ."))
    else:
        for tag in sorted({x["tag"] for x in cases}):
            items = [x for x in cases if x["tag"] == tag]
            with st.expander(f"{T.name(tag, lang)} (`{tag}`) — {len(items)}"):
                for x in items:
                    where = " · ".join(v for v in [
                        f"{L('ДЗ', 'ҮТ')} №{x['work_no']}" if x.get("work_no") else "",
                        f"{L('задача', 'есеп')} {x['problem_idx']}" if x.get("problem_idx") else "",
                        f"{L('строка', 'жол')} {x['line_no']}" if x.get("line_no") else ""] if v)
                    verdict = L("✗ неверно", "✗ дұрыс емес") if x["verdict"] == "reject" else \
                        L("↻ другой тег: ", "↻ басқа тег: ") + T.name(x["new_tag"], lang)
                    note = f" — «{esc(x['review_comment'])}»" if x.get("review_comment") else ""
                    st.markdown(f"- **{esc(verdict)}** · {esc(where)} · {esc(x.get('alias'))}: "
                                f"`{esc(x['evidence'])}`{note}", unsafe_allow_html=True)
        st.download_button(L("Скачать как markdown", "Markdown ретінде жүктеу"),
                           quality.candidates_markdown(conn, lang, tags, student_ids).encode("utf-8"),
                           "rule_candidates.md", "text/markdown")

    # ---- 3. размеченные работы
    st.header(L("3. Точность на размеченных работах", "3. Белгіленген жұмыстардағы дәлдік"))
    if not ev["available"]:
        st.info(L("Файла eval/labels.csv нет. Заполните его по инструкции в eval/README.md: одна строка — одна задача, "
                  "строки решения как в тетради, номер первой неверной строки и тег ошибки.",
                  "eval/labels.csv файлы жоқ. Оны eval/README.md нұсқаулығы бойынша толтырыңыз: бір жол — бір есеп, "
                  "шешім жолдары дәптердегідей, алғашқы қате жолдың нөмірі және қате тегі."))
    elif not ev["n"]:
        st.info(L("В eval/labels.csv нет строк — см. eval/README.md.", "eval/labels.csv-те жол жоқ — eval/README.md қараңыз."))
    else:
        n = ev["n"]
        st.markdown(L(f"Первая ошибка (строка): **{ev['ok_line']}/{n} = {_pct(ev['ok_line'] / n)}** · "
                      f"строка и тег: **{ev['ok_tag']}/{n} = {_pct(ev['ok_tag'] / n)}**",
                      f"Алғашқы қате (жол): **{ev['ok_line']}/{n} = {_pct(ev['ok_line'] / n)}** · "
                      f"жол және тег: **{ev['ok_tag']}/{n} = {_pct(ev['ok_tag'] / n)}**"))
        st.dataframe(pd.DataFrame([{
            "": "✓" if r["ok_tag"] else "✗", L("Условие", "Шарт"): r["statement"],
            L("Эталон", "Эталон"): f"{r['want_line']}/{r['want_tag'] or '-'}",
            L("Проверка", "Тексеру"): f"{r['got_line']}/{r['got_tag'] or '-'}"} for r in ev["rows"]]),
            hide_index=True, use_container_width=True)
        st.caption(L("Та же цифра, что даёт `python evaluate.py` (без распознавания фото). "
                     "Разметка — eval/labels.csv, инструкция — eval/README.md.",
                     "`python evaluate.py` беретін сан (фото танусыз). Белгілеу — eval/labels.csv, нұсқаулық — eval/README.md."))

    # ---- 4. время проверки
    st.header(L("4. Время проверки", "4. Тексеру уақыты"))
    if tm["median_seconds"] is None:
        st.info(L("Замеров пока нет: время пишется на экране «Проверка работы» — от «Проверить» до «Записать в журнал».",
                  "Әзірге өлшеу жоқ: уақыт «Жұмысты тексеру» экранында жазылады — «Тексеру»-ден «Журналға жазу»-ға дейін."))
    else:
        st.markdown(L(f"Медиана **{tm['median_seconds']:.0f} с** на работу (работ: {tm['n']}) — от «Проверить» до «Записать в журнал».",
                      f"Бір жұмысқа медиана **{tm['median_seconds']:.0f} с** (жұмыс: {tm['n']}) — «Тексеру»-ден «Журналға жазу»-ға дейін."))
    c1, c2 = st.columns([2, 1])
    cur = tm["manual_minutes"]
    val = c1.number_input(L("Сколько минут у вас уходит на проверку одной такой работы вручную?",
                            "Осындай бір жұмысты қолмен тексеруге неше минут кетеді?"),
                          min_value=0.0, max_value=120.0, step=0.5, value=cur, key="eps_manual_min",
                          placeholder=L("по данным опроса учителей", "мұғалімдер сауалнамасы бойынша"))
    c2.write("")
    if c2.button(L("Сохранить", "Сақтау"), key="eps_manual_save", use_container_width=True):
        quality.set_manual_minutes(conn, val if val else None)
        st.rerun()
    if cur is not None and tm["median_seconds"] is not None:
        st.success(L(f"Медиана {tm['median_seconds']:.0f} с на работу против {cur:g} мин вручную (по опросу): "
                     f"экономия ≈ {tm['saved_minutes_per_work']:.1f} мин на работу.",
                     f"Бір жұмысқа медиана {tm['median_seconds']:.0f} с, қолмен — {cur:g} мин (сауалнама бойынша): "
                     f"үнемдеу ≈ бір жұмысқа {tm['saved_minutes_per_work']:.1f} мин."))
    elif cur is None:
        st.caption(L("Пока время ручной проверки не введено, экономия не показывается.",
                     "Қолмен тексеру уақыты енгізілмейінше, үнемдеу көрсетілмейді."))

    # ---- 5. похожесть портрета
    st.header(L("5. Похожесть портрета", "5. Портреттің ұқсастығы"))
    if rs["mean"] is None:
        st.info(L("Оценок пока нет: форма «Похоже ли это на ученика?» — внизу портрета.",
                  "Әзірге баға жоқ: «Бұл оқушыға ұқсай ма?» формасы — портреттің төменгі жағында."))
    else:
        st.markdown(L(f"Средняя оценка **{rs['mean']:.1f} из 5** — последняя оценка по каждому ученику "
                      f"(учеников: {rs['n_students']}, всего оценок: {rs['n']}).",
                      f"Орташа баға **5-тен {rs['mean']:.1f}** — әр оқушының соңғы бағасы бойынша "
                      f"(оқушы: {rs['n_students']}, барлық баға: {rs['n']})."))
        if rs["wrong"]:
            st.markdown("**" + L("Чаще всего неверны:", "Жиі дұрыс емес:") + "** " + "; ".join(
                f"{_item_label(k, lang)} — {n}" for k, n in rs["wrong"][:5]))

    # ---- 6. выгрузка
    st.header(L("6. Цифры для слайда", "6. Слайдқа арналған сандар"))
    md = quality.metrics_markdown(conn, lang, student_ids)
    with st.expander(L("Предпросмотр", "Алдын ала қарау")):
        st.markdown(md)
    c1, c2 = st.columns(2)
    c1.download_button(L("Скачать метрики (markdown)", "Метрикаларды жүктеу (markdown)"), md.encode("utf-8"),
                       "quality_metrics.md", "text/markdown", use_container_width=True)
    c2.download_button(L("Скачать метрики (CSV)", "Метрикаларды жүктеу (CSV)"),
                       quality.metrics_csv(conn, student_ids).encode("utf-8-sig"), "quality_metrics.csv", "text/csv",
                       use_container_width=True)


def _precision_df(rows, L, lang) -> pd.DataFrame:
    return pd.DataFrame([{
        L("Тег", "Тег"): T.name(d["tag"], lang), L("Точность", "Дәлдік"): _pct(d["precision"]),
        L("Статус", "Күйі"): L("требует проверки", "тексеруді қажет етеді") if d["needs_review"] else
        (L("мало отметок", "белгі аз") if d["reviewed"] < quality.REVIEWS_MIN else L("в норме", "қалыпты")),
        L("Отмечено", "Белгіленді"): d["reviewed"], L("Верно", "Дұрыс"): d["confirm"],
        L("Неверно", "Дұрыс емес"): d["reject"], L("Другой тег", "Басқа тег"): d["retag"],
        L("Частая замена", "Жиі ауыстыру"): (f"{T.name(d['top_replacement'], lang)} ({d['top_replacement_n']})"
                                             if d["top_replacement"] else ""),
        L("Наблюдений", "Бақылау"): d["observations"], L("Код", "Коды"): d["tag"],
    } for d in rows])


def _item_label(key: str, lang: str) -> str:
    kind, *rest = key.split(":")
    if kind == "err" and len(rest) == 2:
        return f"{T.name(rest[1], lang)} ({T.skill_name(rest[0], lang).lower()})"
    if kind == "strength" and rest:
        return T.skill_name(rest[0], lang)
    if kind == "teacher" and rest:
        return T.name(rest[0], lang)
    if kind == "rec" and rest:
        return T.name(rest[-1], lang)
    return key

"""Стыки после слияния шести направлений (агент Омега, пункты O1–O15 из agents/OMEGA.md)."""
import time
from pathlib import Path

import pytest

from core import auth, consent, db, ocr_store, pipeline, portrait, practice, quality, review, seed

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Синтетический класс строится один раз; тесты работают на копии базы."""
    path = tmp_path_factory.mktemp("omega") / "seed.db"
    conn = db.connect(path)
    seed.build(conn)
    conn.close()
    return path


@pytest.fixture
def conn(seeded, tmp_path):
    src = db.connect(seeded)
    dst = db.connect(tmp_path / "t.db")
    src.backup(dst)
    src.close()
    yield dst
    dst.close()


@pytest.fixture
def app_db(seeded, tmp_path, monkeypatch):
    """База для прогона app.py через AppTest: копия сида + демо-пользователи; PORTRET_DB указывает на неё."""
    path = tmp_path / "app.db"
    src, dst = db.connect(seeded), db.connect(path)
    src.backup(dst)
    src.close()
    auth.ensure_demo_users(dst)
    monkeypatch.setenv("PORTRET_DB", str(path))
    monkeypatch.delenv("PORTRET_AUTH", raising=False)
    yield dst
    dst.close()


def run_app(page: str, user: dict | None, lang: str = "ru", **state):
    """Один прогон app.py: вход — через session_state["user"], раздел — через session_state["page"]."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_resource.clear()  # get_conn кэширует соединение на процесс — у каждого теста своя база
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.session_state["lang"] = lang
    at.session_state["page"] = page
    if user is not None:
        at.session_state["user"] = user
        at.session_state["user_seen_at"] = time.time()
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _texts(at) -> str:
    """Весь видимый текст страницы — для проверок «есть / нет на экране»."""
    parts = []
    for kind in ("markdown", "caption", "warning", "info", "error", "success", "title", "header", "subheader", "metric"):
        for el in getattr(at, kind):
            parts.append(str(getattr(el, "value", "")) + " " + str(getattr(el, "label", "")))
    parts += [e.label for e in at.expander]
    return "\n".join(parts)


def _aigerim(conn) -> int:
    return db.q1(conn, "SELECT id FROM students WHERE alias=? ORDER BY id LIMIT 1", (seed.STUDENTS[0],))["id"]


def _live_demo(conn, sid: int, aid: int | None = None, text: dict | None = None) -> int:
    """Живая проверка, как в page_check: прежняя живая работа того же задания удаляется."""
    aid = aid or seed.live_assignment_id(conn)
    text = text or seed.LIVE_DEMO_TEXT
    answers = {p["id"]: text.get(p["idx"], "").splitlines() for p in pipeline.problems_of(conn, aid)}
    for o in db.q(conn, "SELECT id FROM submissions WHERE student_id=? AND assignment_id=? AND source='live'", (sid, aid)):
        pipeline.delete_submission(conn, o["id"])
    sub_id, _ = pipeline.record_submission(conn, sid, aid, answers, "2026-09-24 10:00:00", source="live")
    return sub_id


def _lost_root(p: dict) -> tuple:
    e = next(e for e in p["errors"] if e["tag"] == "lost_root")
    return e["count"], e["total"]


def dangling(conn) -> list[str]:
    """Строки, которые ссылаются на несуществующие работы или наблюдения."""
    refs = {"submission_id": "submissions", "source_submission_id": "submissions", "observation_id": "observations"}
    out = []
    for t, cols in db.tables(conn).items():
        for col, target in refs.items():
            if col in cols:
                n = db.q1(conn, f"""SELECT COUNT(*) c FROM {db.qi(t)} WHERE {col} IS NOT NULL
                                    AND {col} NOT IN (SELECT id FROM {target})""")["c"]
                if n:
                    out.append(f"{t}.{col}: {n}")
    return out


# ---------------------------------------------------------------- O1. повторная проверка работы

def test_recheck_leaves_no_traces(conn):
    sid = _aigerim(conn)
    sub = _live_demo(conn, sid)
    assert _lost_root(portrait.build(conn, sid)) == (5, 7)
    err = db.q1(conn, "SELECT id, problem_id FROM observations WHERE submission_id=? AND kind='error'", (sub,))
    review.add(conn, err["id"], "reject", comment="не ошибка")
    assert _lost_root(portrait.build(conn, sid)) == (4, 7)
    # следы других направлений: распознавание, замер времени, тренажёр по этой работе
    pid = err["problem_id"]
    ocr_store.save(conn, sub, {pid: [{"text": "x = 7", "confidence": 0.9}, {"text": "Ответ: 7", "confidence": 0.9}]},
                   {pid: ["x = 7", "Ответ: 7"]})
    quality.record_timing(conn, sub, sid, 42.0)
    item = practice.start_fix_own(conn, sid, sub, pid)
    r = practice.submit_attempt(conn, item, ["x² − 7x = 0", "x(x − 7) = 0", "x = 0 или x = 7"], 1)
    assert r["outcome"] == "self_fixed"

    new = _live_demo(conn, sid)  # повторная проверка той же работы, теперь текстом
    marks = review.latest(conn, [o["id"] for o in db.q(conn, "SELECT id FROM observations WHERE submission_id=?", (new,))])
    assert marks == {}  # старая отметка «неверно» не прирастает к новой ошибке
    assert _lost_root(portrait.build(conn, sid)) == (5, 7)
    assert db.q1(conn, "SELECT COUNT(*) c FROM ocr_lines WHERE submission_id=?", (new,))["c"] == 0
    assert db.q1(conn, "SELECT COUNT(*) c FROM check_timings WHERE submission_id=?", (new,))["c"] == 0
    assert practice.summary(conn, sid)["outcomes"]["self_fixed"] == 1  # исход тренажёра остался в журнале
    assert db.q1(conn, "SELECT source_submission_id s FROM practice_items WHERE id=?", (item,))["s"] is None
    assert dangling(conn) == []


def test_recheck_twice_same_numbers(conn):
    sid = _aigerim(conn)
    _live_demo(conn, sid)
    before = portrait.snapshot(portrait.build(conn, sid))
    _live_demo(conn, sid)
    assert portrait.snapshot(portrait.build(conn, sid)) == before
    assert dangling(conn) == []


# ---------------------------------------------------------------- O2. «Качество» — только свои классы

def _secret_9b(conn) -> tuple[int, int]:
    """Ученик 9 «Б» с отклонённой ошибкой, замером и оценкой портрета."""
    sid = conn.execute("INSERT INTO students (alias, class_name) VALUES (?,?)", ("Секретный-9Б", "9 «Б»")).lastrowid
    sub = _live_demo(conn, sid)
    err = db.q1(conn, "SELECT id FROM observations WHERE submission_id=? AND kind='error'", (sub,))["id"]
    review.add(conn, err, "reject", comment="чужой комментарий 9Б")
    quality.record_timing(conn, sub, sid, 999.0)
    review.add_rating(conn, sid, 1, [], "оценка 9Б")
    return sid, err


def test_quality_scoped_to_visible_students(conn):
    sid_b, err_b = _secret_9b(conn)
    a = _aigerim(conn)
    own = db.q1(conn, "SELECT id FROM observations WHERE student_id=? AND kind='error' AND source='auto'", (a,))["id"]
    review.add(conn, own, "reject", comment="свой комментарий")
    review.add_rating(conn, a, 4, [], "")
    ta = auth.create_user(conn, "teacher_a", "Учитель А", "teacher", "password-a1", classes=[seed.CLASS_NAME])
    auth.create_user(conn, "teacher_b", "Учитель Б", "teacher", "password-b1", classes=["9 «Б»"])
    vis_a = auth.visible_student_ids(conn, auth.get_user(conn, ta))
    assert sid_b not in vis_a and a in vis_a

    cases = quality.rejected_cases(conn, None, student_ids=vis_a)
    assert [c["id"] for c in cases] == [own]
    md = quality.candidates_markdown(conn, "ru", None, student_ids=vis_a)
    assert "Секретный-9Б" not in md and "чужой комментарий" not in md and seed.STUDENTS[0] in md
    prec = quality.tag_precision(conn, student_ids=vis_a)
    lost = next(d for d in prec if d["tag"] == "lost_root")
    assert lost["reviewed"] == 1
    assert quality.timing_summary(conn, vis_a)["n"] == 0
    assert review.rating_summary(conn, vis_a)["n"] == 1
    for text in (quality.metrics_markdown(conn, "ru", vis_a), quality.metrics_csv(conn, vis_a)):
        assert "999" not in text

    # None — все ученики, как раньше
    assert {c["id"] for c in quality.rejected_cases(conn)} == {own, err_b}
    assert quality.tag_precision(conn) == quality.tag_precision(conn, student_ids=None)
    assert next(d for d in quality.tag_precision(conn) if d["tag"] == "lost_root")["reviewed"] == 2
    assert "Секретный-9Б" in quality.candidates_markdown(conn)
    assert quality.timing_summary(conn)["n"] == 1 and review.rating_summary(conn)["n"] == 2
    assert quality.rejected_cases(conn, None, student_ids=set()) == []


# ---------------------------------------------------------------- O3. тренажёр и согласие

def test_practice_gate_follows_consent(conn):
    sid = _aigerim(conn)
    assert practice.can_practice(conn, sid)  # синтетический ученик: согласие ставится само
    consent.revoke(conn, sid)
    assert not practice.can_practice(conn, sid)
    consent.give(conn, sid, "№ 12 от 01.09.2026", None)
    assert practice.can_practice(conn, sid)
    real = conn.execute("INSERT INTO students (alias, class_name) VALUES (?,?)", ("Настоящий", seed.CLASS_NAME)).lastrowid
    assert not practice.can_practice(conn, real)  # у настоящего ученика согласия по умолчанию нет


@pytest.mark.parametrize("login", [auth.DEMO_STUDENT, auth.DEMO_TEACHER])
def test_practice_page_without_consent_writes_nothing(app_db, login):
    sid = _aigerim(app_db)
    consent.required_ok(app_db, sid)
    consent.revoke(app_db, sid)
    user = auth.get_user_by_login(app_db, login)
    at = run_app("practice", user, student_id=sid)
    text = _texts(at)
    assert "нет согласия" in text.lower()
    assert ("Для учителя" in text) == (login == auth.DEMO_TEACHER)  # сводка по классу — только учителю
    practice.ensure_schema(app_db)
    for t in ("practice_sessions", "practice_items", "practice_attempts"):
        assert db.q1(app_db, f"SELECT COUNT(*) c FROM {t}")["c"] == 0


def test_practice_class_block_hidden_for_student(app_db):
    student = auth.get_user_by_login(app_db, auth.DEMO_STUDENT)
    assert "Для учителя" not in _texts(run_app("practice", student))
    teacher = auth.get_user_by_login(app_db, auth.DEMO_TEACHER)
    assert "Для учителя" in _texts(run_app("practice", teacher))


# ---------------------------------------------------------------- O4. «<» и «>» в доказательствах

def test_no_escaped_text_inside_code_spans():
    """В code span Markdown сущности не раскрываются: «x &lt; −2» вместо «x < −2». Только <code>{esc(…)}</code>."""
    import re
    bad = re.compile(r"`\{(esc|_e|_esc)\(")
    files = [ROOT / "app.py", *sorted((ROOT / "ui").glob("*.py"))]
    hits = [f"{f.name}:{i}" for f in files for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
            if bad.search(line)]
    assert hits == []


# ---------------------------------------------------------------- O5. лимит обращений к модели

def click(at, label: str):
    """Нажать кнопку по началу подписи и перерисовать страницу."""
    btn = next(b for b in at.button if b.label.startswith(label))
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def record_demo_work(at):
    """Сценарий демо: «Вставить демо-работу» → «Проверить» → «Записать в журнал и обновить портрет»."""
    click(at, "Вставить демо-работу")
    click(at, "Проверить")
    return click(at, "Записать в журнал")


def test_rate_limit_falls_back_to_keywords(app_db, monkeypatch):
    from core import llm
    calls = []
    monkeypatch.setattr(auth, "rate_limit", lambda user, action="llm", now=None: calls.append(action) or False)
    monkeypatch.setattr(llm, "tag_comment", lambda *a, **k: pytest.fail("модель вызвана сверх лимита"))
    teacher = auth.get_user_by_login(app_db, auth.DEMO_TEACHER)
    at = run_app("check", teacher, llm_cfg=llm.LLMConfig("anthropic", "test-key", "m", ""))
    record_demo_work(at)
    sub = db.q1(app_db, "SELECT id FROM submissions WHERE source='live' ORDER BY id DESC LIMIT 1")
    assert sub is not None  # работа записана, хотя прежняя уже удалена к моменту разметки
    tc = db.q1(app_db, "SELECT tagger FROM teacher_comments WHERE submission_id=?", (sub["id"],))
    assert tc["tagger"] == "keywords" and calls == ["llm"]
    assert db.q1(app_db, "SELECT COUNT(*) c FROM observations WHERE submission_id=? AND source='teacher'",
                 (sub["id"],))["c"] > 0


def test_rate_limit_blocks_personalize(app_db, monkeypatch):
    from core import llm
    monkeypatch.setattr(auth, "rate_limit", lambda user, action="llm", now=None: False)
    monkeypatch.setattr(llm, "personalize", lambda *a, **k: pytest.fail("модель вызвана сверх лимита"))
    teacher = auth.get_user_by_login(app_db, auth.DEMO_TEACHER)
    at = run_app("portrait", teacher, llm_cfg=llm.LLMConfig("anthropic", "test-key", "m", ""))
    click(at, "✨ Привязать советы")
    assert any("Лимит обращений к модели" in w.value for w in at.warning)


# ---------------------------------------------------------------- O6. reviewer — id пользователя

def test_reviewer_shown_as_user_name(conn):
    from ui import review as review_view
    L = lambda ru, kk: ru  # noqa: E731
    uid = auth.create_user(conn, "t_rev", "Учитель Иванова", "teacher", "password-rv1", classes=[seed.CLASS_NAME])
    o1, o2 = [r["id"] for r in db.q(conn, "SELECT id FROM observations WHERE kind='error' ORDER BY id LIMIT 2")]
    review.add(conn, o1, "confirm", reviewer=str(uid))
    review.add(conn, o2, "reject")  # старая отметка: reviewer по умолчанию «учитель»
    marks = review.latest(conn, [o1, o2])
    assert marks[o1]["reviewer"] == str(uid) and marks[o2]["reviewer"] == review.DEFAULT_REVIEWER
    assert "Учитель Иванова" in review_view.mark_badge(marks[o1], L, conn)
    assert review_view.mark_badge(marks[o2], L, conn) == review_view.mark_badge(marks[o2], L)  # как раньше
    assert review_view.log_marks(conn, [o1, o2], L) == {o1: "✓ верно · Учитель Иванова", o2: "✗ неверно"}


def test_marks_from_app_store_user_id(app_db):
    teacher = auth.get_user_by_login(app_db, auth.DEMO_TEACHER)
    at = record_demo_work(run_app("check", teacher))
    click(at, "✓ верно")
    r = db.q1(app_db, "SELECT reviewer FROM reviews ORDER BY id DESC LIMIT 1")
    assert r["reviewer"] == str(teacher["id"])


# ---------------------------------------------------------------- O7. фильтр «тренажёр» в журнале

def test_log_filter_practice(app_db):
    sid = _aigerim(app_db)
    item = practice.start_fix_own(app_db, sid, *[db.q1(app_db, """SELECT at.submission_id, at.problem_id FROM attempts at
        JOIN submissions s ON s.id = at.submission_id WHERE s.student_id=? AND at.first_error_line IS NOT NULL
        ORDER BY s.submitted_at DESC LIMIT 1""", (sid,))[k] for k in ("submission_id", "problem_id")])
    practice.abandon_item(app_db, item, 1)  # исход needs_example → журнал, source='practice'
    teacher = auth.get_user_by_login(app_db, auth.DEMO_TEACHER)
    at = run_app("log", teacher)
    box = next(b for b in at.selectbox if b.label == "Источник")
    assert "тренажёр" in box.options
    box.select("practice").run()
    df = at.dataframe[0].value
    assert len(df) == 1 and set(df["Источник"]) == {"practice"}


# ---------------------------------------------------------------- O8. распознавание знает новые темы Беты

NEW_TOPIC_LINES = [
    ("inequality", "x ∈ [−2; 3]"), ("inequality", "+ − +"), ("inequality", "(−∞; −1) ∪ [3; +∞)"),
    ("inequality", "x < −3"), ("inequality", "Ответ: (−∞; −3)"),
    ("system", "(3; 2)"), ("system", "Ответ: (3; 4), (4; 3)"), ("system", "x + y = 5; x − y = 1"),
    ("system", "⎩x − y = 1"), ("system", "x₁ = 3, x₂ = 4"),
    ("biquadratic", "t = x²"), ("biquadratic", "Пусть x² = t, t ≥ 0"), ("biquadratic", "t₁ = 4, t₂ = 1"),
]


@pytest.mark.parametrize("kind,text", NEW_TOPIC_LINES)
def test_score_lines_new_topics(kind, text):
    from core import ocr
    line = ocr.score_lines([text], kind)[0]
    assert "unparsable" not in line["flags"] and not ocr.needs_review(line)


def test_score_lines_kind_still_flags_garbage():
    from core import ocr
    for kind, text in [("inequality", "x < −3 + ("), ("system", "x + = 3"), ("biquadratic", "x² = (4")]:
        assert "unparsable" in ocr.score_lines([text], kind)[0]["flags"]
    # без вида и для equation/expression — прежний общий разбор
    assert ocr.score_lines(["x ∈ [−2; 3]"])[0]["flags"] == ["unparsable"]
    assert ocr.score_lines(["x ∈ [−2; 3]"], "equation")[0]["flags"] == ["unparsable"]


def test_parses_agrees_with_check_on_extra_assignments():
    """parses вида = «check не пометил строку unparsed» на эталонах и типичных ошибках ДЗ №8–10."""
    from core import ocr
    from core.checker import check_problem
    for ex in seed.EXTRA_ASSIGNMENTS:
        for idx, (_, kind, st, ref, ans) in enumerate(ex["problems"], 1):
            for lines in [ref, *seed.EXTRA_VARIANTS.get(ex["number"], {}).get(idx, {}).values()]:
                for lr in check_problem(kind, st, lines, len(ref), ans).lines:
                    flagged = "unparsable" in ocr.score_line(lr.raw, kind)["flags"]
                    assert flagged == (lr.status == "unparsed"), (kind, lr.raw)


def test_unverified_swap_line_not_downgraded():
    """Верная ошибка swap_xy в строке ответа «(2; 3)», прочитанной уверенно, пишется с полной уверенностью."""
    from core import ocr
    from core.checker import check_problem
    st, lines = "x + y = 5; x − y = 1", ["x = 3", "y = 2", "(2; 3)"]
    res = check_problem("system", st, lines, None, "(3; 2)")
    assert res.first_error and res.first_error["tag"] == "swap_xy"
    raw = ocr.score_lines(lines, "system")
    assert all(not ocr.needs_review(l) for l in raw)
    assert not ocr.error_unverified(1, res, ocr.unverified({1: raw}, {1: lines}))
    old = ocr.score_lines(lines)  # без вида строка ответа «не разбирается» → confidence 0.6 в журнале
    assert ocr.error_unverified(1, res, ocr.unverified({1: old}, {1: lines}))


@pytest.mark.parametrize("raw,want", [
    (r"x \in [-2; 3]", "x ∈ [-2; 3]"),
    (r"(-\infty; -3)", "(-∞; -3)"),
    (r"(-\infty; -1) \cup [3; +\infty)", "(-∞; -1) ∪ [3; +∞)"),
    (r"\{ x + y = 5", "x + y = 5"),
    (r"\{(3; 2)\}", "(3; 2)"),
])
def test_clean_text_new_latex(raw, want):
    from core import ocr
    assert ocr.clean_text(raw) == want


def test_ocr_prompt_rules_for_new_topics():
    from core import llm
    for bit in ("≤ ≥", "∞", "∪", "+ − +", "двумя строками", "\\infty"):
        assert bit in llm._RULES
    assert "фигурные скобки LaTeX" in llm._RULES  # запрет — только на скобки LaTeX, не на систему в тетради


def test_evaluate_passes_kind_to_score_lines(tmp_path, monkeypatch):
    import evaluate
    from core import llm
    (tmp_path / "p.jpg").write_bytes(b"fake")
    monkeypatch.setattr(evaluate, "ROOT", tmp_path)
    monkeypatch.setattr(llm, "recognize_detailed", lambda cfg, data, problems, passes=1: {
        1: [{"text": "x ∈ [−2; 3]", "unsure": []}], 2: [{"text": "x ∈ [−2; 3]", "unsure": []}]})
    rows = [{"photo": "p.jpg", "problem": "1", "kind": "inequality", "statement": "x² − x − 6 ≤ 0"},
            {"photo": "p.jpg", "problem": "2", "kind": "equation", "statement": "x² = 4"}]
    out, _ = evaluate.recognize_photo(llm.LLMConfig(), "p.jpg", rows, 1)
    assert out[1][0]["flags"] == [] and out[2][0]["flags"] == ["unparsable"]


# ---------------------------------------------------------------- O9. подписи видов задач и строк

def test_kind_names_cover_all_kinds():
    from core import roster
    from core import tags as T
    for k in roster.problem_kinds():
        assert T.KIND_NAMES[k]["ru"] and T.KIND_NAMES[k]["kk"]
    for k in ("ineq", "signs", "subst", "system", "interval", "point", "answer", "check", "domain", "rejected"):
        assert T.LINE_KINDS[k]["ru"] and T.LINE_KINDS[k]["kk"]


def test_line_kinds_of_new_topics_have_labels():
    """Каждый вид строки, который выдают проверки Беты на эталонах и ошибках, имеет подпись (кроме служебных)."""
    from core import tags as T
    from core.checker import check_problem
    service = {"eq", "eqs", "root", "text", "numeric", "unparsed", "expr", ""}
    seen = set()
    for ex in seed.EXTRA_ASSIGNMENTS:
        for idx, (_, kind, st, ref, ans) in enumerate(ex["problems"], 1):
            for lines in [ref, *seed.EXTRA_VARIANTS.get(ex["number"], {}).get(idx, {}).values()]:
                seen |= {lr.kind for lr in check_problem(kind, st, lines, len(ref), ans).lines}
    more = [("inequality", "x² − x − 6 ≤ 0", ["x² − x − 6 = 0", "x₁ = 3, x₂ = −2", "x ∈ [−2; 3]"]),
            ("system", "x + y = 5; x − y = 1", ["{x + y = 5", "⎩x − y = 1", "x = 3, y = 2", "(3; 2)"])]
    for kind, st, lines in more:
        seen |= {lr.kind for lr in check_problem(kind, st, lines, None, None).lines}
    assert {"ineq", "signs", "interval", "subst", "system", "point"} <= seen
    assert seen - service <= set(T.LINE_KINDS)


# ---------------------------------------------------------------- O10. демо-работа для ДЗ №8

def _hw(conn, number: int) -> int:
    return db.q1(conn, "SELECT id FROM assignments WHERE number=?", (number,))["id"]


def test_extra_demo_text_hw8(conn):
    aid = _hw(conn, 8)
    probs = {p["idx"]: p["id"] for p in pipeline.problems_of(conn, aid)}
    res = pipeline.run_checks(conn, aid, {pid: seed.EXTRA_DEMO_TEXT[8][i].splitlines() for i, pid in probs.items()})
    fe1 = res[probs[1]].first_error
    assert (fe1["line"], fe1["tag"]) == (3, "ineq_flip")
    assert res[probs[2]].first_error is None and res[probs[2]].correct and "interval_method" in res[probs[2]].methods
    fe3 = res[probs[3]].first_error
    assert (fe3["line"], fe3["tag"], fe3["detail"].get("domain")) == (2, "boundary", ["−1"])


def test_demo_button_hw8_finds_aigerim_by_alias(app_db):
    from core import privacy
    old = _aigerim(app_db)
    privacy.delete_student(app_db, old)  # id 1 больше не Айгерим: ищем по псевдониму, а не по id
    new = app_db.execute("INSERT INTO students (alias, class_name) VALUES (?,?)", (seed.STUDENTS[0], seed.CLASS_NAME)).lastrowid
    app_db.commit()
    assert new != old
    teacher = auth.get_user_by_login(app_db, auth.DEMO_TEACHER)
    other = db.q1(app_db, "SELECT id FROM students WHERE alias=?", (seed.STUDENTS[1],))["id"]
    at = run_app("check", teacher, chk_asg=_hw(app_db, 8), chk_student=other)
    click(at, "Вставить демо-работу")
    assert at.session_state["chk_student"] == new
    click(at, "Проверить")
    text = _texts(at)
    assert "ошибка в строке 3" in text
    for label in ("неравенство", "знаки"):  # O9: подписи видов строк справа
        assert label in text


# ---------------------------------------------------------------- O11. способы решения новых тем в портрете

def test_methods_by_skill_new_topics(conn):
    sid = _aigerim(conn)
    before = portrait.build(conn, sid)
    assert set(before["methods_by_skill"]) == {"quadratic"}  # в сиде методы — только у квадратных
    disc = next(m for m in before["methods_by_skill"]["quadratic"] if m["tag"] == "disc")
    assert (disc["count"], disc["total"]) == next((m["count"], m["total"]) for m in before["methods"] if m["tag"] == "disc")
    _live_demo(conn, sid, _hw(conn, 8), seed.EXTRA_DEMO_TEXT[8])
    p = portrait.build(conn, sid)
    assert db.q1(conn, "SELECT COUNT(*) c FROM observations WHERE tag='interval_method' AND student_id=?", (sid,))["c"] == 2
    im = next(m for m in p["methods_by_skill"]["inequality"] if m["tag"] == "interval_method")
    assert (im["count"], im["total"]) == (1, 1) and len(im["refs"]) == 2
    assert p["methods"] == before["methods"]  # квадратные — как раньше


def test_portrait_pages_show_new_methods(app_db):
    sid = _aigerim(app_db)
    _live_demo(app_db, sid, _hw(app_db, 8), seed.EXTRA_DEMO_TEXT[8])
    teacher = auth.get_user_by_login(app_db, auth.DEMO_TEACHER)
    at = run_app("portrait", teacher, student_id=sid)
    assert "Неравенства: какими способами" in _texts(at)
    student = auth.get_user_by_login(app_db, auth.DEMO_STUDENT)
    assert "метод интервалов" in _texts(run_app("portrait", student)).lower()


# ---------------------------------------------------------------- O12. «N наблюдений в журнале»

def test_n_obs_does_not_depend_on_marks(conn):
    sid = _aigerim(conn)
    n = portrait.build(conn, sid)["n_obs"]
    class_counter = db.q1(conn, "SELECT COUNT(*) c FROM observations WHERE 1=1 AND student_id IN (?)", (sid,))["c"]
    assert n == class_counter  # тот же запрос, что в page_class
    o = db.q1(conn, "SELECT id FROM observations WHERE student_id=? AND kind='error' AND source='auto'", (sid,))["id"]
    review.add(conn, o, "reject")
    assert portrait.build(conn, sid)["n_obs"] == n

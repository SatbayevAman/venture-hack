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

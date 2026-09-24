"""Тренажёр ученика (агент Гамма): шаблоны, лестница подсказок, сигнал в журнал, сводка."""
import json
import random

import pytest

from core import db, hints, pipeline, portrait, practice, seed
from core import tags as T
from core.checker import check_problem
from core.tags import tag_comment_keywords

ERROR_TAGS = ("lost_root", "extra_root", "sign", "fsu", "calc", "other")
TEMPLATES = [(tag, tid, fn) for tag, fams in practice.FAMILIES.items() for tid, fn in fams]


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("gamma") / "base.db"
    conn = db.connect(path)
    seed.build(conn)
    conn.close()
    return path


@pytest.fixture
def conn(seeded, tmp_path):
    """Свежая копия засеянной базы на каждый тест."""
    src = db.connect(seeded)
    dst = db.connect(tmp_path / "t.db")
    src.backup(dst)
    src.close()
    return dst


def _live_demo(conn):
    aid = seed.live_assignment_id(conn)
    answers = {pr["id"]: seed.LIVE_DEMO_TEXT[pr["idx"]].split("\n") for pr in pipeline.problems_of(conn, aid)}
    sub_id, _ = pipeline.record_submission(conn, 1, aid, answers, "2026-09-24 08:00:00", source="live")
    pipeline.record_comment(conn, sub_id, seed.LIVE_DEMO_COMMENT, tag_comment_keywords(seed.LIVE_DEMO_COMMENT), "keywords")
    return sub_id


def _lost(conn, sid=1):
    e = next(e for e in portrait.build(conn, sid, "ru")["errors"] if e["tag"] == "lost_root")
    return e["count"], e["total"]


# ---------------------------------------------------------------- генераторы

@pytest.mark.parametrize("tag,tid,fn", TEMPLATES, ids=[t[1] for t in TEMPLATES])
def test_template_reference_passes_checker(tag, tid, fn):
    statements = set()
    made = 0
    for s in range(20):
        it = fn(random.Random(s))
        if it is None:  # неудачные параметры — генератор возьмёт другие
            continue
        made += 1
        res = check_problem(it["kind"], it["statement"], it["reference"], len(it["reference"]), it["answer"])
        assert res.first_error is None and res.correct is True, (tid, it["statement"], it["reference"])
        if tag == "check_done":
            assert "check_done" in res.habits
        statements.add(it["statement"])
    assert made >= 10
    assert len(statements) > 1  # разные seed — разные задачи


@pytest.mark.parametrize("tag", list(practice.FAMILIES) + [None])
def test_generate_distinct_and_self_checked(tag):
    items = practice.generate(tag, None, n=5, seed=7)
    assert len(items) == 5
    assert len({i["statement"] for i in items}) == 5
    for it in items:
        assert practice.self_check(it)
        for key in ("statement", "kind", "skill", "reference", "answer"):
            assert it[key]
    assert [i["statement"] for i in practice.generate(tag, None, 5, seed=8)] != [i["statement"] for i in items]


def test_lost_root_family_is_x2_kx():
    items = practice.generate("lost_root", "quadratic", n=5, seed=1)
    assert all(i["skill"] == "quadratic" for i in items)
    assert all("x" in i["statement"] and "²" in i["statement"] or "(x" in i["statement"] for i in items)
    for it in items:  # у каждой задачи семьи два корня, один теряется при делении
        assert len(check_problem("equation", it["statement"], []).true_answer) == 2


def test_requirements_for_habits():
    chk = practice.generate("check_done", None, n=3, seed=2)
    assert all(i["requirement"].get("check") for i in chk)
    no_check = [l for l in chk[0]["reference"] if not l.startswith("Проверка")]
    res = check_problem(chk[0]["kind"], chk[0]["statement"], no_check, len(chk[0]["reference"]))
    assert practice.accepts(chk[0], res) == (False, "need_check")

    st = practice.generate("skip_steps", None, n=3, seed=2)[0]
    assert st["requirement"]["min_lines"] >= 2
    res = check_problem(st["kind"], st["statement"], [st["reference"][-1]], len(st["reference"]))
    assert practice.accepts(st, res) == (False, "need_steps")


# ---------------------------------------------------------------- подсказки

FE = {"lost_root": {"tag": "lost_root", "line": 1, "detail": {"f": "x", "division": True}},
      "extra_root": {"tag": "extra_root", "line": 6, "detail": {"domain": ["2"]}},
      "sign": {"tag": "sign", "line": 1, "detail": {}},
      "fsu": {"tag": "fsu", "line": 1, "detail": {"f": "(a + 3)²"}},
      "calc": {"tag": "calc", "line": 2, "detail": {"num": "−6"}},
      "other": {"tag": "other", "line": 2, "detail": {}}}
CASES = {"lost_root": ("x² = 5x", "quadratic"), "extra_root": ("x²/(x − 2) = (3x − 2)/(x − 2)", "quadratic"),
         "sign": ("3x + 5 = x − 3", "linear"), "fsu": ("(a + 3)² − (a − 3)(a + 3)", "simplify"),
         "calc": ("3x + 5 = x − 3", "linear"), "other": ("5x − 7 = 2x + 8", "linear")}


def _fe(tag):
    return dict(FE[tag], P_acc="условие", P_gen="условия", P_nom_kk="шарт", P_abl_kk="шарттан")


@pytest.mark.parametrize("tag", ERROR_TAGS)
@pytest.mark.parametrize("lang", ["ru", "kk"])
def test_hint_ladder_not_empty(tag, lang):
    statement, skill = CASES[tag]
    texts = [hints.hint(_fe(tag), level, lang, statement, skill) for level in (1, 2, 3)]
    assert all(t and t.strip() for t in texts)
    assert len(set(texts)) == 3
    assert tag in hints.RULES


@pytest.mark.parametrize("tag", ERROR_TAGS)
def test_level3_is_not_students_problem(tag):
    statement, skill = CASES[tag]
    ex = hints.example(_fe(tag), statement, skill)
    assert ex is not None
    assert practice._key(ex["statement"]) != practice._key(statement)
    assert practice.self_check(ex)  # пример гарантированно верный
    assert 0 <= ex["key_line"] < len(ex["reference"]) and ex["note"]["ru"] and ex["note"]["kk"]


def test_levels_1_2_do_not_leak_answer():
    fe = _fe("lost_root")
    for lang in ("ru", "kk"):
        for level in (1, 2):
            assert not hints.leaks_answer(hints.hint(fe, level, lang, "x² = 5x", "quadratic"), "0; 5")
    # и для всех тегов на их задачах
    for tag in ERROR_TAGS:
        statement, skill = CASES[tag]
        true = check_problem("equation", statement, []).true_answer if "=" in statement else None
        for lang in ("ru", "kk"):
            for level in (1, 2):
                assert not hints.leaks_answer(hints.hint(_fe(tag), level, lang, statement, skill), true)


def test_leaks_answer_detects():
    assert hints.leaks_answer("Правильно: x = 5", "0; 5")
    assert hints.leaks_answer("x₁ = 0", [0.0, 5.0])
    assert hints.leaks_answer("Ответ: 0; 5", "0; 5")
    assert hints.leaks_answer("корни 5 и 0", [0.0, 5.0])
    assert hints.leaks_answer("x = −4", [-4.0])
    assert not hints.leaks_answer("x = −4", [4.0])
    assert not hints.leaks_answer("В строке 5 ошибка", [0.0, 5.0])


@pytest.mark.parametrize("statement", ["x² = 5x", "4x² = 16x", "x² = 7x", "x(x − 3) = 2(x − 3)"])
def test_level3_prefers_example_without_students_roots(statement):
    ex = hints.example(_fe("lost_root"), statement, "quadratic")
    own = set(check_problem("equation", statement, []).true_answer)
    assert not own & set(hints._values(ex["answer"]))


def test_level3_follows_problem_family():
    """Ошибка классифицирована как вычислительная, но тренируем потерю корня — пример из семьи lost_root."""
    fe = dict(_fe("calc"), line=1)
    ex = hints.example(fe, "x(x − 2) = 3(x − 2)", "quadratic", family="lost_root")
    assert ex["template"] in {tid for tid, _ in practice.FAMILIES["lost_root"]}
    assert hints.example(fe, "x(x − 2) = 3(x − 2)", "quadratic")["template"] == "calc_quad"


def test_unknown_tag_gets_generic_rule():
    assert hints.rule("brand_new_tag", "ru") == hints.GENERIC_RULE["ru"]
    assert hints.hint({"tag": "brand_new_tag", "line": 1}, 3, "kk", "3x + 5 = x − 3", "linear")


# ---------------------------------------------------------------- сценарий без интерфейса

def test_scenario_self_fixed_does_not_change_portrait(conn):
    assert _lost(conn) == (4, 6)
    before = portrait.snapshot(portrait.build(conn, 1, "ru"))

    sid = practice.start_session(conn, 1, "weak_spot", "lost_root", "quadratic")
    item_id = practice.add_item(conn, sid, practice.generate("lost_root", "quadratic", 1, seed=3)[0])
    item = practice.get_item(conn, item_id)
    k = next(v for v in item["answer"].replace("−", "-").split("; ") if v != "0")  # «разделил(а) на x»

    r1 = practice.submit_attempt(conn, item_id, [f"x = {k}"], hint_level=0)
    assert not r1["ok"] and r1["reason"] == "error" and r1["result"].first_error["tag"] == "lost_root"
    assert r1["outcome"] is None
    q = hints.hint(r1["result"].first_error, 1, "ru", item["statement"], item["skill"])
    assert not hints.leaks_answer(q, item["answer"])

    r2 = practice.submit_attempt(conn, item_id, item["reference"], hint_level=1)
    assert r2["ok"] and r2["outcome"] == "self_fixed"

    obs = db.q(conn, "SELECT * FROM observations WHERE source='practice'")
    assert len(obs) == 1
    o = obs[0]
    assert (o["tag"], o["kind"], o["student_id"], o["submission_id"]) == ("self_fixed", "habit", 1, None)
    assert json.loads(o["detail"]) == {"hint_level": 1, "error_tag": "lost_root", "item_id": item_id, "solved": True}
    assert "Потеря корня" in o["evidence"] and "строке 1" in o["evidence"]

    # портрет не изменился: он считает только auto и teacher
    assert _lost(conn) == (4, 6)
    after = portrait.snapshot(portrait.build(conn, 1, "ru"))
    assert after == before
    assert portrait.build(conn, 1, "ru")["recs"][0]["tag"] == "lost_root"
    # тренировка не попала в submissions
    assert db.q1(conn, "SELECT COUNT(*) c FROM submissions WHERE student_id=1")["c"] == 6


def test_first_try_gives_no_observation(conn):
    sid = practice.start_session(conn, 2, "weak_spot", "sign", "linear")
    it = practice.generate("sign", "linear", 1, seed=4)[0]
    item_id = practice.add_item(conn, sid, it)
    r = practice.submit_attempt(conn, item_id, it["reference"], hint_level=0)
    assert r["ok"] and r["outcome"] is None
    assert not db.q(conn, "SELECT 1 FROM observations WHERE source='practice'")
    assert db.q1(conn, "SELECT correct FROM practice_attempts WHERE item_id=?", (item_id,))["correct"] == 1


@pytest.mark.parametrize("level,expected", [(0, "self_fixed"), (1, "self_fixed"), (2, "fixed_after_rule"),
                                            (3, "needs_example")])
def test_outcome_rule(level, expected):
    assert practice.outcome_for(level, True) == expected
    assert practice.outcome_for(level, False) == "needs_example"


def test_abandon_after_error_is_needs_example_and_schedules_review(conn):
    sid = practice.start_session(conn, 3, "weak_spot", "fsu", "simplify", now="2026-09-24 10:00:00")
    it = practice.generate("fsu", "simplify", 1, seed=5)[0]
    item_id = practice.add_item(conn, sid, it)
    r = practice.submit_attempt(conn, item_id, ["0"], hint_level=0, now="2026-09-24 10:01:00")
    assert r["result"].first_error is not None
    assert practice.abandon_item(conn, item_id, 3, now="2026-09-24 10:05:00") == "needs_example"
    assert practice.abandon_item(conn, item_id, 3) is None  # исход пишется один раз
    rv = practice.reviews(conn, 3, now="2026-09-25 10:00:00")
    assert not rv["due"] and len(rv["later"]) == 2
    rv = practice.reviews(conn, 3, now="2026-09-26 10:05:00")
    assert len(rv["due"]) == 1 and len(rv["later"]) == 1
    # повторение: та же задача, решённая — снимается из списка
    _, [rid] = practice.start_review(conn, 3, [rv["due"][0]["id"]], now="2026-09-26 11:00:00")
    assert practice.get_item(conn, rid)["statement"] == it["statement"]
    assert practice.submit_attempt(conn, rid, it["reference"], 0, now="2026-09-26 11:02:00")["ok"]
    assert len(practice.reviews(conn, 3, now="2026-09-26 12:00:00")["due"]) == 0


def test_fix_own_demo_aigerim(conn):
    """ДЗ №7 после живой проверки: разбор своей ошибки → «Исправлено!» → self_fixed."""
    sub_id = _live_demo(conn)
    own = practice.own_errors(conn, 1)
    first = own[0]
    assert first["submission_id"] == sub_id and first["statement"] == "x² = 7x" and first["first_error_line"] == 1
    assert first["tag"] == "lost_root"

    res0 = practice.original_check(conn, first["submission_id"], first["problem_id"])
    assert res0.first_error["line"] == 1
    q = hints.hint(res0.first_error, 1, "ru", first["statement"], first["skill"])
    assert "Может ли x быть равно нулю" in q and not hints.leaks_answer(q, "0; 7")
    assert practice.prefill(practice.original_lines(conn, sub_id, first["problem_id"]), 1) == []

    before = _lost(conn)
    item_id = practice.start_fix_own(conn, 1, sub_id, first["problem_id"])
    r = practice.submit_attempt(conn, item_id, ["x² − 7x = 0", "x(x − 7) = 0", "x = 0 или x = 7"], hint_level=1)
    assert r["ok"] and r["outcome"] == "self_fixed"
    o = db.q1(conn, "SELECT * FROM observations WHERE source='practice'")
    assert (o["submission_id"], o["problem_id"], o["line_no"]) == (sub_id, first["problem_id"], 1)
    assert _lost(conn) == before == (5, 7)


# ---------------------------------------------------------------- сводка и цели

def _fake_outcome(conn, student_id, outcome, tag="lost_root", n=1):
    for i in range(n):
        sid = practice.start_session(conn, student_id, "weak_spot", tag, "quadratic")
        it = practice.generate(tag, None, 1, seed=100 * student_id + i)[0]
        item_id = practice.add_item(conn, sid, it)
        practice.submit_attempt(conn, item_id, ["x = 12345"] if it["kind"] == "equation" else ["0"], 0)
        if outcome == "needs_example" and i % 2:  # не решена — тоже needs_example
            practice.abandon_item(conn, item_id, 3)
        else:
            level = {"self_fixed": 1, "fixed_after_rule": 2, "needs_example": 3}[outcome]
            assert practice.submit_attempt(conn, item_id, it["reference"], level)["outcome"] == outcome


def test_summary_verdicts(conn):
    assert not practice.summary(conn, 4)["has_data"]

    _fake_outcome(conn, 4, "self_fixed", n=2)
    assert practice.summary(conn, 4)["verdict"] == "low_data"
    _fake_outcome(conn, 4, "self_fixed")
    _fake_outcome(conn, 4, "fixed_after_rule")
    s = practice.summary(conn, 4)
    assert s["has_data"] and s["n_outcomes"] == 4 and s["verdict"] == "self_check"  # 3/4 = 75 %
    row = next(r for r in s["by_tag"] if r["tag"] == "lost_root")
    assert row["solved"] == 4 and row["outcomes"]["self_fixed"] == 3

    _fake_outcome(conn, 5, "needs_example", n=2)
    _fake_outcome(conn, 5, "self_fixed")
    _fake_outcome(conn, 5, "fixed_after_rule")
    s = practice.summary(conn, 5)
    assert s["verdict"] == "needs_lesson"  # 2/4 = 50 %

    _fake_outcome(conn, 6, "self_fixed")
    _fake_outcome(conn, 6, "fixed_after_rule", n=2)
    assert practice.summary(conn, 6)["verdict"] == "mixed"

    groups = practice.class_summary(conn, db.q(conn, "SELECT * FROM students"))
    assert [a for _, a, _ in groups["self_check"]] == ["Арман"]
    assert [a for _, a, _ in groups["needs_lesson"]] == ["Жанель"]


def test_summary_first_try_only(conn):
    sid = practice.start_session(conn, 7, "weak_spot", "sign", "linear")
    it = practice.generate("sign", "linear", 1, seed=9)[0]
    practice.submit_attempt(conn, practice.add_item(conn, sid, it), it["reference"], 0)
    s = practice.summary(conn, 7)
    assert s["has_data"] and s["solved"] == 1 and s["first_try"] == 1 and s["n_outcomes"] == 0


def test_targets_for_aigerim_is_lost_root(conn):
    t = practice.targets_for(conn, 1, "ru")
    assert t[0]["tag"] == "lost_root" and t[0]["skill"] == "quadratic" and t[0]["mode"] == "weak"
    sid, ids = practice.start_weak_spot(conn, 1, t[0], n=4, seed=1)
    stmts = [practice.get_item(conn, i)["statement"] for i in ids]
    assert len(ids) == 4 and len(set(stmts)) == 4
    assert all("x" in s for s in stmts)
    own = {r["statement"] for r in db.q(conn, "SELECT statement FROM problems")}
    for sd in range(30):  # задачи из ДЗ (например, x² = 5x и будущее x² = 7x) не попадают в набор
        _, ids = practice.start_weak_spot(conn, 1, t[0], n=5, seed=sd)
        assert not own & {practice.get_item(conn, i)["statement"] for i in ids}


def test_targets_every_student_known_or_mixed(conn):
    for s in db.q(conn, "SELECT id FROM students"):
        t = practice.targets_for(conn, s["id"], "kk")
        assert t and (t[0]["tag"] in practice.FAMILIES or t[0]["mode"] == "mixed")
        assert practice.generate(t[0]["tag"], t[0]["skill"], 3, seed=1)


def test_new_tags_in_dictionary():
    for tag in practice.OUTCOMES:
        t = T.TAGS[tag]
        assert t["kind"] == "habit"
        for f in ("ru", "kk", "teacher_ru", "teacher_kk", "student_ru", "student_kk"):
            assert t[f]
    for key in practice.VERDICTS:
        assert practice.VERDICTS[key]["ru"] and practice.VERDICTS[key]["kk"]


def test_optional_topics_only_when_tag_exists(monkeypatch):
    """Новые темы Беты: шаблоны появляются только вместе с тегом и только если проверка их принимает."""
    monkeypatch.setattr(practice, "FAMILIES", dict(practice.FAMILIES))
    monkeypatch.setattr(practice, "_USABLE", {})
    practice.register_optional_topics()
    assert "ineq_flip" not in practice.FAMILIES
    monkeypatch.setitem(T.TAGS, "ineq_flip", {"kind": "error", "ru": "Знак неравенства", "kk": "Теңсіздік таңбасы"})
    practice.register_optional_topics()
    assert "ineq_flip" in practice.FAMILIES
    assert hints.rule("ineq_flip", "kk") == hints.RULES["ineq_flip"]["kk"]
    # эталон уходит в check_problem; пока проверка не знает неравенств, задачи отбрасываются, а не ломаются
    items = practice.generate("ineq_flip", None, n=2, seed=1)
    assert all(practice.self_check(i) for i in items)
    assert practice.usable("ineq_flip") == bool(items)

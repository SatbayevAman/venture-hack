"""Тесты агента Бета: новые виды задач и типы ошибок.

Каждый тип ошибки находится на нужной строке; для каждого правила есть случай,
где оно не должно срабатывать; наводящие вопросы не называют ответ.
Запуск: python -m pytest -q
"""
import pytest
import sympy as sp

from core import db, pipeline, portrait, seed
from core import tags as T
from core.checker import check_problem
from core.kinds import KINDS
from core.kinds.inequality import fmt_set, parse_intervals, parse_relation
from core.tags import question_for

oo = sp.oo


def first(kind, statement, lines, ref=4):
    r = check_problem(kind, statement, lines, ref)
    fe = r.first_error or {}
    return r, fe.get("tag"), fe.get("line")


# ---------------------------------------------------------------- неравенства: реестр

def test_inequality_registered():
    assert "inequality" in KINDS
    r = check_problem("inequality", "2x − 3 > 5", ["x > 4"], 3)
    assert r.kind == "inequality" and r.true_answer is None and r.final == ["(4; +∞)"]


# ---------------------------------------------------------------- неравенства: ошибки

@pytest.mark.parametrize("statement,lines,tag,line", [
    # знак не перевёрнут при делении на −3
    ("−3x < 6", ["x < −2"], "ineq_flip", 1),
    ("4 − x ≥ 2x + 1", ["−x − 2x ≥ 1 − 4", "−3x ≥ −3", "x ≥ 1", "Ответ: [1; +∞)"], "ineq_flip", 3),
    # перевёрнут на строке ответа (записью промежутков)
    ("−3x < 6", ["Ответ: (−∞; −2)"], "ineq_flip", 1),
    # знак при переносе
    ("2x − 3 > 5", ["2x > 5 − 3"], "sign", 1),
    # деление на x / умножение на знаменатель
    ("x² > 3x", ["x > 3"], "ineq_div_var", 1),
    ("x(x − 3) > 2(x − 3)", ["x > 2"], "ineq_div_var", 1),
    ("(x − 1)/(x + 2) ≥ 0", ["x − 1 ≥ 0", "x ≥ 1"], "ineq_div_var", 1),
    # граница: строгое вместо нестрогого
    ("x² − 5x + 6 ≤ 0", ["x₁ = 2, x₂ = 3", "Ответ: (2; 3)"], "boundary", 2),
    ("2x − 3 ≥ 5", ["2x > 8"], "boundary", 1),
    # не тот промежуток
    ("x² − 5x + 6 ≤ 0", ["x² − 5x + 6 = 0", "x₁ = 2, x₂ = 3", "+ − +", "Ответ: (−∞; 2] ∪ [3; +∞)"], "interval_choice", 4),
    ("x² − 5x + 6 < 0", ["x < 2 или x > 3"], "interval_choice", 1),
    # вычислительная: одно неверное число, неверный корень, неверный D
    ("2x − 3 > 5", ["2x > 6", "x > 3"], "calc", 1),
    ("x² − 5x + 6 ≤ 0", ["x₁ = 2, x₂ = 4", "Ответ: [2; 4]"], "calc", 1),
    ("x² − 5x + 6 ≤ 0", ["x² − 5x + 6 = 0", "D = 25 − 24 = 9"], "calc", 2),
    # ФСУ
    ("(x + 1)² > x² + 3", ["x² + 1 > x² + 3"], "fsu", 1),
])
def test_inequality_errors(statement, lines, tag, line):
    _, t, ln = first("inequality", statement, lines)
    assert (t, ln) == (tag, line)


def test_boundary_domain_detail():
    r, t, ln = first("inequality", "(x − 1)/(x + 2) ≥ 0", ["Ответ: (−∞; −2] ∪ [1; +∞)"])
    assert (t, ln) == ("boundary", 1)
    assert r.first_error["detail"]["domain"] == ["−2"]
    # строгое/нестрогое — без пометки ОДЗ
    r, t, _ = first("inequality", "x² − 5x + 6 ≤ 0", ["x₁ = 2, x₂ = 3", "Ответ: (2; 3)"])
    assert t == "boundary" and not r.first_error["detail"].get("domain")


# ---------------------------------------------------------------- неравенства: верные решения

@pytest.mark.parametrize("statement,lines,methods", [
    ("2x − 3 > 5", ["2x > 8", "x > 4", "Ответ: (4; +∞)"], []),
    # верное деление на отрицательное — не ineq_flip
    ("−3x < 6", ["x > −2"], []),
    ("4 − x ≥ 2x + 1", ["−x − 2x ≥ 1 − 4", "−3x ≥ −3", "x ≤ 1", "Ответ: (−∞; 1]"], []),
    # разложение вместо деления на x — не ineq_div_var
    ("x² > 3x", ["x² − 3x > 0", "x(x − 3) > 0", "x < 0 или x > 3"], ["interval_method"]),
    # полный метод интервалов с D, знаками и ответом
    ("x² − x − 6 ≤ 0", ["x² − x − 6 = 0", "D = 1 + 24 = 25", "x₁ = (1 + 5)/2 = 3", "x₂ = (1 − 5)/2 = −2",
                        "+ − +", "Ответ: [−2; 3]"], ["interval_method"]),
    # дробь: нуль знаменателя выколот; (x − 1)(x + 2) ≥ 0 при x ≠ −2 — равносильно на ОДЗ
    ("(x − 1)/(x + 2) ≥ 0", ["ОДЗ: x ≠ −2", "x = 1, x = −2", "Ответ: (−∞; −2) ∪ [1; +∞)"], ["interval_method"]),
    ("(x − 1)/(x + 2) ≥ 0", ["(x − 1)(x + 2) ≥ 0", "x ≠ −2", "x = 0: −1/2 < 0", "Ответ: x < −2 или x ≥ 1"], ["interval_method"]),
    # двойное неравенство, R, ∅
    ("−1 < 2x + 1 ≤ 5", ["−2 < 2x ≤ 4", "−1 < x ≤ 2", "Ответ: (−1; 2]"], []),
    ("x² + 1 > 0", ["Ответ: R"], []),
    ("x² + 1 < 0", ["нет решений"], []),
])
def test_inequality_correct(statement, lines, methods):
    r, t, _ = first("inequality", statement, lines)
    assert t is None and r.correct is True and r.methods == methods


def test_inequality_habits():
    r = check_problem("inequality", "x² − 5x + 6 ≤ 0",
                      ["(x − 2)(x − 3) ≤ 0", "Ответ: [2; 3]", "Проверка: x = 2,5: 0,5·(−0,5) < 0 ✓"], 6)
    assert r.correct and {"check_done", "skip_steps"} <= set(r.habits)
    r = check_problem("inequality", "(x − 3)/(x + 1) ≥ 0", ["ОДЗ: x ≠ −1", "x₁ = 3, x₂ = −1", "Ответ: (−∞; −1) ∪ [3; +∞)"], 3)
    assert "domain_noted" in r.habits


def test_inequality_sign_chart_is_info():
    r = check_problem("inequality", "x² − 5x + 6 > 0", ["x₁ = 2, x₂ = 3", "+ − +", "Ответ: (−∞; 2) ∪ (3; +∞)"], 3)
    assert [l.kind for l in r.lines] == ["root", "signs", "answer"] and r.lines[1].status == "info"


def test_inequality_unparsed_line_is_flagged():
    r = check_problem("inequality", "2x − 3 > 5", ["2x > 8 )(", "x > 4"], 3)
    assert r.lines[0].status == "unparsed" and r.first_error is None and r.correct


# ---------------------------------------------------------------- запись промежутков

@pytest.mark.parametrize("text,expected", [
    ("(−∞; 2) ∪ [3; +∞)", sp.Union(sp.Interval.open(-oo, 2), sp.Interval(3, oo))),
    ("[1; 5)", sp.Interval.Ropen(1, 5)),
    ("(−∞; +∞)", sp.S.Reals),
    ("R", sp.S.Reals),
    ("ℝ", sp.S.Reals),
    ("∅", sp.S.EmptySet),
    ("(-oo; 2) U (3; oo)", sp.Union(sp.Interval.open(-oo, 2), sp.Interval.open(3, oo))),
    ("(2,5; 3]", sp.Interval.Lopen(sp.Rational(5, 2), 3)),       # десятичная запятая при «;»
    ("(2, 5)", sp.Interval.open(2, 5)),                            # запятая как разделитель
    ("[-1, 2.5)", sp.Interval.Ropen(-1, sp.Rational(5, 2))),
    ("(−∞; −2) ∪ {3}", sp.Union(sp.Interval.open(-oo, -2), sp.FiniteSet(3))),
    ("[1/2; √2]", sp.Interval(sp.Rational(1, 2), sp.sqrt(2))),
])
def test_interval_notation(text, expected):
    assert parse_intervals(text) == expected


@pytest.mark.parametrize("text", ["(x − 1)(x + 2)", "(5; 2)", "(1; 2) (3; 4)", "x > 2", ""])
def test_not_interval_notation(text):
    assert parse_intervals(text) is None


def test_interval_forms_in_lines():
    for ans in ("Ответ: x ∈ (−∞; 2) ∪ (3; +∞)", "Жауабы: (−∞; 2) U (3; +∞)", "x < 2 или x > 3", "x < 2 немесе x > 3"):
        r = check_problem("inequality", "x² − 5x + 6 > 0", [ans], 3)
        assert r.correct, ans
    r = check_problem("inequality", "x² − 4 ≤ 0", ["нет решений"], 3)
    assert r.correct is False and r.final == ["∅"]


def test_relation_or_and_chain():
    rel = parse_relation("x < 2 или x ≥ 3")
    assert rel.solve() == sp.Union(sp.Interval.open(-oo, 2), sp.Interval(3, oo))
    assert parse_relation("−1 < x ⩽ 2").solve() == sp.Interval.Lopen(-1, 2)
    assert parse_relation("x = 2") is None
    assert fmt_set(sp.Union(sp.Interval.open(-oo, -1), sp.Interval(3, oo))) == "(−∞; −1) ∪ [3; +∞)"


# ---------------------------------------------------------------- словарь и вопросы

NEW_ERROR_TAGS = ["ineq_flip", "ineq_div_var", "interval_choice", "boundary"]


def test_new_tags_in_dictionary():
    assert T.SKILLS["inequality"] == {"ru": "Неравенства", "kk": "Теңсіздіктер"}
    assert T.SKILLS_SHORT["inequality"]["kk"] == "Теңсіздік"
    for tag in NEW_ERROR_TAGS:
        t = T.TAGS[tag]
        assert t["kind"] == "error"
        for f in ("ru", "kk", "teacher_ru", "teacher_kk", "student_ru", "student_kk"):
            assert t[f].strip(), (tag, f)
    assert T.TAGS["interval_method"]["kind"] == "method" and T.TAGS["interval_method"]["kk"]


@pytest.mark.parametrize("statement,lines,answer_bits", [
    ("−3x < 6", ["x < −2"], ["−2", "> −2", "x >"]),
    ("x² > 3x", ["x > 3"], ["x < 0", "(−∞; 0)"]),
    ("(x − 1)/(x + 2) ≥ 0", ["x − 1 ≥ 0", "x ≥ 1"], ["−2", "x < −2"]),
    ("x² − 5x + 6 ≤ 0", ["x² − 5x + 6 = 0", "x₁ = 2, x₂ = 3", "Ответ: (−∞; 2] ∪ [3; +∞)"], ["[2; 3]", "2 ≤ x"]),
    ("x² − 5x + 6 ≤ 0", ["x₁ = 2, x₂ = 3", "Ответ: (2; 3)"], ["[2; 3]", "квадратн", "≤"]),
    ("(x − 1)/(x + 2) ≥ 0", ["Ответ: (−∞; −2] ∪ [1; +∞)"], ["−2", "(−∞; −2)"]),
])
def test_questions_for_new_tags(statement, lines, answer_bits):
    r = check_problem("inequality", statement, lines, 3)
    fe = r.first_error
    assert fe["tag"] in NEW_ERROR_TAGS
    for lang in ("ru", "kk"):
        q = question_for(fe, lang)
        assert q and "{" not in q and str(fe["line"]) in q
        for bit in answer_bits:
            assert bit not in q, (lang, q)


def test_comment_keywords_new_tags():
    tags = {t["tag"] for t in T.tag_comment_keywords("Не меняет знак неравенства, путает промежутки и границы.")}
    assert {"ineq_flip", "interval_choice", "boundary"} <= tags
    tags = {t["tag"] for t in T.tag_comment_keywords("Теңсіздік таңбасын ауыстырмайды, шекара қате.")}
    assert {"ineq_flip", "boundary"} <= tags
    assert all(T.CONFIRMS[t] == {t} for t in NEW_ERROR_TAGS)


# ---------------------------------------------------------------- демо-задание

def test_extra_assignment_references_are_correct():
    for ex in seed.EXTRA_ASSIGNMENTS:
        for skill, kind, st, ref, ans in ex["problems"]:
            assert skill in T.SKILLS
            r = check_problem(kind, st, ref, len(ref))
            assert r.first_error is None and r.correct is True, st


VARIANT_TAGS = {"flip": "ineq_flip", "sign": "sign", "boundary": "boundary", "choice": "interval_choice",
                "domain": "boundary", "mul": "ineq_div_var", "subst": "subst", "swap": "swap_xy",
                "lost": "lost_root", "fsu": "fsu", "neg_t": "neg_t", "pm": "lost_root", "cancel": "cancel_terms"}


def test_extra_variants_give_intended_tags():
    for ex in seed.EXTRA_ASSIGNMENTS:
        for idx, variants in seed.EXTRA_VARIANTS.get(ex["number"], {}).items():
            _, kind, st, ref, _ = ex["problems"][idx - 1]
            for v, lines in variants.items():
                assert first(kind, st, lines, len(ref))[1] == VARIANT_TAGS[v], (st, v)


def test_extra_assignment_keeps_demo(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed.build(conn)
    live = db.q1(conn, "SELECT number FROM assignments WHERE id=?", (seed.live_assignment_id(conn),))
    assert live["number"] == 7
    a8 = db.q1(conn, "SELECT id FROM assignments WHERE number=8")
    probs = pipeline.problems_of(conn, a8["id"])
    assert [p["kind"] for p in probs] == ["inequality"] * 3
    assert db.q1(conn, "SELECT COUNT(*) c FROM submissions WHERE assignment_id=?", (a8["id"],))["c"] == 0

    # живая проверка ДЗ №8: ошибка попадает в журнал, портрет и карту класса без правок их кода
    answers = {probs[0]["id"]: ["−3x < 11 − 2", "−3x < 9", "x < −3", "Ответ: (−∞; −3)"]}
    pipeline.record_submission(conn, 2, a8["id"], answers, "2026-09-28 10:00:00", source="live")
    obs = db.q1(conn, "SELECT * FROM observations WHERE tag='ineq_flip'")
    assert obs["skill"] == "inequality" and obs["line_no"] == 3
    p = portrait.build(conn, 2, "ru")
    assert any(e["tag"] == "ineq_flip" and e["skill"] == "inequality" for e in p["errors"])
    row = next(r for r in portrait.class_map(conn, "ru") if r["alias"] == "Данияр")
    assert (row["cells"]["inequality"]["bad"], row["cells"]["inequality"]["total"]) == (1, 1)


# ---------------------------------------------------------------- B1. системы уравнений

S1 = "x + y = 5; x − y = 1"
S2 = "2x + y = 7; x − y = 2"
SQ = "x² + y² = 25; x + y = 7"
SQ_HEAD = ["y = 7 − x", "x² + (7 − x)² = 25", "x² + 49 − 14x + x² = 25", "2x² − 14x + 24 = 0", "x² − 7x + 12 = 0"]


@pytest.mark.parametrize("statement,lines,methods", [
    (S1, ["y = 5 − x", "x − (5 − x) = 1", "2x − 5 = 1", "2x = 6", "x = 3", "y = 5 − 3 = 2", "Ответ: (3; 2)"], ["substitution"]),
    (S1, ["2x = 6", "x = 3", "y = 5 − 3", "y = 2", "Ответ: (3; 2)"], ["addition"]),
    ("2x + 3y = 12; x − y = 1", ["x = 1 + y", "2 + 2y + 3y = 12", "5y = 10", "y = 2", "x = 3", "Ответ: (3; 2)"], ["substitution"]),
    # система двумя строками с фигурной скобкой и в одной строке через «;»
    (S1, ["⎧ y = 5 − x", "⎩ x − (5 − x) = 1", "x = 3", "y = 2", "Ответ: (3; 2)"], ["substitution"]),
    (S1, ["y = 5 − x; x − (5 − x) = 1", "x = 3", "y = 2", "Ответ: x = 3, y = 2"], ["substitution"]),
    (S1, ["Ответ: x = 3 и y = 2"], []),
    # квадратное + линейное: два решения
    (SQ, SQ_HEAD + ["x₁ = 3, x₂ = 4", "y₁ = 4, y₂ = 3", "Ответ: (3; 4), (4; 3)"], ["substitution"]),
    # нет решений / бесконечно много
    ("x + y = 2; x + y = 5", ["Ответ: нет решений"], []),
    ("x + y = 2; 2x + 2y = 4", ["Ответ: бесконечно много решений"], []),
])
def test_system_correct(statement, lines, methods):
    r, t, _ = first("system", statement, lines)
    assert t is None and r.correct is True and r.methods == methods


@pytest.mark.parametrize("statement,lines,tag,line", [
    # подстановка без скобок: минус перед подставленным выражением / коэффициент на одно слагаемое
    (S2, ["y = 7 − 2x", "x − 7 − 2x = 2"], "subst", 2),
    ("2x + 3y = 12; x − y = 1", ["y = x − 1", "2x + 3x − 1 = 12"], "subst", 2),
    # перепутаны x и y
    (S2, ["y = 7 − 2x", "x − (7 − 2x) = 2", "3x = 9", "x = 3", "y = 1", "Ответ: (1; 3)"], "swap_xy", 6),
    # знак при переносе — после верной подстановки это уже не subst
    (S1, ["y = 5 − x", "x − (5 − x) = 1", "x − 5 + x = 1", "2x = 1 − 5"], "sign", 4),
    # вычислительные
    (S1, ["2x = 8", "x = 4", "y = 1", "Ответ: (4; 1)"], "calc", 1),
    (S1, ["2x = 6", "x = 3", "y = 5 − 3 = 3"], "calc", 3),
    # потерянное решение и ФСУ в квадратной системе
    (SQ, SQ_HEAD[:4] + ["x = 3", "y = 4", "Ответ: (3; 4)"], "lost_root", 7),
    (SQ, ["y = 7 − x", "x² + (7 − x)² = 25", "x² + 49 + x² = 25"], "fsu", 3),
    # лишнее решение у несовместной системы
    ("x + y = 2; x + y = 5", ["Ответ: (1; 1)"], "extra_root", 1),
    # ошибочное уравнение внутри строки-системы
    (S1, ["⎧ y = 5 − x", "⎩ x − 5 − x = 1"], "subst", 2),
])
def test_system_errors(statement, lines, tag, line):
    _, t, ln = first("system", statement, lines)
    assert (t, ln) == (tag, line)


def test_system_lines_after_error_are_relative():
    r = check_problem("system", S2, ["y = 7 − 2x", "x − 7 − 2x = 2", "−x = 9", "x = −9", "y = 25", "Ответ: (−9; 25)"], 6)
    assert [l.status for l in r.lines] == ["ok", "error", "after", "after", "after", "after"]


def test_system_subst_not_on_correct_substitution():
    r, t, _ = first("system", S2, ["y = 7 − 2x", "x − (7 − 2x) = 2", "x − 7 + 2x = 2", "3x = 9"])
    assert t is None and all(l.status == "ok" for l in r.lines)


def test_system_questions():
    for lines, tag in ((["y = 7 − 2x", "x − 7 − 2x = 2"], "subst"),
                       (["y = 7 − 2x", "x − (7 − 2x) = 2", "3x = 9", "x = 3", "y = 1", "Ответ: (1; 3)"], "swap_xy")):
        r = check_problem("system", S2, lines, 6)
        assert r.first_error["tag"] == tag
        for lang in ("ru", "kk"):
            q = question_for(r.first_error, lang)
            assert q and "{" not in q and "(3; 1)" not in q and "x = 3" not in q
    assert T.SKILLS["system"]["kk"] and T.SKILLS_SHORT["system"]["ru"] == "Системы"
    for tag in ("subst", "swap_xy"):
        assert all(T.TAGS[tag][f] for f in ("ru", "kk", "teacher_ru", "teacher_kk", "student_ru", "student_kk"))
    assert T.TAGS["substitution"]["kind"] == T.TAGS["addition"]["kind"] == "method"

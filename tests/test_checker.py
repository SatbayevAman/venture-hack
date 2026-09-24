"""Тесты проверки: каждый тип ошибки из словаря находится на нужной строке.

Запуск: python -m pytest -q
"""
import pytest

from core.checker import check_problem, normalize
from core.tags import question_for, tag_comment_keywords


def first(kind, statement, lines, ref=3):
    r = check_problem(kind, statement, lines, ref)
    return r, (r.first_error or {}).get("tag"), (r.first_error or {}).get("line")


@pytest.mark.parametrize("statement,lines,tag,line", [
    # потеря корня при делении на x
    ("x² = 5x", ["x = 5", "Ответ: 5"], "lost_root", 1),
    ("3x² = 12x", ["3x = 12", "x = 4"], "lost_root", 1),
    ("x(x − 3) = 2(x − 3)", ["x = 2"], "lost_root", 1),
    # забыли ±
    ("x² − 16 = 0", ["x² = 16", "x = 4"], "lost_root", 2),
    # посторонний корень: не учтена ОДЗ
    ("x²/(x − 2) = (3x − 2)/(x − 2)", ["x² = 3x − 2", "x² − 3x + 2 = 0", "D = 9 − 8 = 1",
                                        "x₁ = (3 + 1)/2 = 2", "x₂ = (3 − 1)/2 = 1", "Ответ: 1; 2"], "extra_root", 6),
    # знак при переносе / раскрытии скобок
    ("3x + 5 = x − 3", ["3x − x = −3 + 5", "2x = 2", "x = 1"], "sign", 1),
    ("2(x − 3) + 4 = 3x − 1", ["2x + 6 + 4 = 3x − 1"], "sign", 1),
    # знак в формуле корней (взяли b вместо −b)
    ("x² − 5x + 6 = 0", ["D = 1", "x₁ = (−5 + 1)/2 = −2", "x₂ = (−5 − 1)/2 = −3"], "sign", 2),
    # вычислительная
    ("3x + 5 = x − 3", ["3x − x = −3 − 5", "2x = −6"], "calc", 2),
    ("x² − 5x + 6 = 0", ["D = 25 − 24 = 9"], "calc", 1),
])
def test_equation_errors(statement, lines, tag, line):
    _, t, ln = first("equation", statement, lines)
    assert (t, ln) == (tag, line)


@pytest.mark.parametrize("statement,lines,tag,line", [
    ("(a + 3)² − (a − 3)(a + 3)", ["a² + 9 − (a² − 9)", "18"], "fsu", 1),
    ("(x − 2)² + 4x", ["x² − 4 + 4x"], "fsu", 1),
    ("(b + 5)(b − 5) − b(b − 2)", ["b² + 25 − (b² − 2b)"], "fsu", 1),
    ("(a + 3)² − (a − 3)(a + 3)", ["a² + 6a + 9 − (a² − 9)", "a² + 6a + 9 − a² − 9"], "sign", 2),
    ("3(x − 2) − 2(x + 1)", ["3x − 6 − 2x − 2", "x − 7"], "calc", 2),
])
def test_expression_errors(statement, lines, tag, line):
    _, t, ln = first("expression", statement, lines)
    assert (t, ln) == (tag, line)


@pytest.mark.parametrize("kind,statement,lines,methods", [
    ("equation", "x² − 5x + 6 = 0", ["D = (−5)² − 4·6 = 25 − 24 = 1", "x₁ = (5 + 1)/2 = 3", "x₂ = (5 − 1)/2 = 2", "Ответ: 2; 3"], ["disc"]),
    ("equation", "x² − 5x + 6 = 0", ["x₁ + x₂ = 5", "x₁ · x₂ = 6", "x₁ = 2, x₂ = 3", "Ответ: 2; 3"], ["vieta"]),
    ("equation", "x² = 5x", ["x² − 5x = 0", "x(x − 5) = 0", "x = 0 или x = 5", "Ответ: 0; 5"], ["factoring"]),
    ("equation", "x²/(x − 2) = (3x − 2)/(x − 2)", ["ОДЗ: x ≠ 2", "x² − 3x + 2 = 0", "(x − 1)(x − 2) = 0",
                                                   "x = 1 или x = 2", "x = 2 — посторонний корень", "Ответ: 1"], ["factoring"]),
    ("expression", "(a + 3)² − (a − 3)(a + 3)", ["a² + 6a + 9 − (a² − 9)", "a² + 6a + 9 − a² + 9", "6a + 18"], []),
])
def test_correct_solutions(kind, statement, lines, methods):
    r, t, _ = first(kind, statement, lines)
    assert t is None and r.correct is True and r.methods == methods


def test_habits():
    r = check_problem("equation", "3x + 5 = x − 3", ["x = −4", "Проверка: 3·(−4) + 5 = −4 − 3 ✓"], 3)
    assert r.correct and set(r.habits) == {"check_done", "skip_steps"}


def test_handwriting_normalization():
    # кириллическая «х», «Д», индексы, ±, десятичная запятая, казахские слова
    r = check_problem("equation", "х² − 5х + 6 = 0", ["Д = 25 − 24 = 1", "х1,2 = (5 ± 1)/2", "Жауабы: 2 және 3"], 3)
    assert r.first_error is None and r.correct
    assert normalize("x = 2,5").text == "x = 2.5"
    assert normalize("корней нет").empty


def test_unsafe_input_is_not_executed():
    r = check_problem("equation", "x + 1 = 2", ["__import__('os').system('echo hi')"], 2)
    assert r.lines[0].status in ("unparsed", "info")


def test_guiding_question_does_not_reveal_answer():
    r = check_problem("equation", "x² = 5x", ["x = 5"], 3)
    q = question_for(r.first_error, "ru")
    assert "x = 0" not in q and "0" not in q.replace("нулю", "")
    assert question_for(r.first_error, "kk")


def test_comment_tagging_ru_kk():
    tags = {t["tag"] for t in tag_comment_keywords("Опять торопится — потеряла корень x = 0.")}
    assert {"rushes", "lost_root"} <= tags
    assert {t["tag"] for t in tag_comment_keywords("Жауапты тексермейді.")} == {"no_check"}

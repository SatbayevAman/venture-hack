"""Распознанные строки → уверенность каждой строки.

Уверенность считает код, а не модель: модели плохо откалиброваны, когда
оценивают себя числом. Модель только отмечает неразборчивые фрагменты
(«?» в тексте и список unsure), дальше — правила:

* «?» или непустой unsure              → флаг illegible,  не выше 0.4;
* строка не разбирается SymPy          → флаг unparsable, не выше 0.3
  (для видов задач из core.kinds решает parses модуля вида: score_lines(lines, kind));
* два прочтения разошлись (passes=2)   → флаг disagree,   не выше 0.5;
* иначе                                → 0.95.

Строка ниже UNSURE «требует проверки»: учитель видит ⚠️, а ошибка в такой
строке, если учитель её не поправил, пишется в журнал с пониженной
уверенностью (см. pipeline.record_submission, параметр line_confidence).
"""
from __future__ import annotations

import difflib
import re

from . import checker

UNSURE = 0.8          # ниже порога — строка «требует проверки»
OK_CONFIDENCE = 0.95
ILLEGIBLE_MAX = 0.4
UNPARSABLE_MAX = 0.3
DISAGREE_MAX = 0.5

_LABELS = ("answer", "check", "domain", "rejected")


# --------------------------------------------------------------------------
# 1. Предобработка ответа модели: обрывки LaTeX → запись, которую понимает checker
# --------------------------------------------------------------------------

def _braced(s: str, i: int):
    """s[i] == '{' → (содержимое, индекс после закрывающей скобки) с учётом вложенности."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return s[i + 1:], len(s)


def _group(inner: str) -> str:
    """Содержимое {…} → число или одна буква как есть, иначе в скобках: \\frac{1}{2a} → 1/(2a)."""
    inner = inner.strip()
    return inner if re.fullmatch(r"\d+(?:\.\d+)?|[A-Za-z]|√\([^()]*\)", inner) else f"({inner})"


def _frac_sqrt(s: str) -> str:
    """\\frac{a}{b} → (a)/(b); \\sqrt{…} → √(…); \\sqrt[3]{…} не бывает в 7–9 кл. — оставляем."""
    out, i = [], 0
    while i < len(s):
        m = re.match(r"\\[dt]?frac\s*(?=\{)", s[i:])
        if m:
            j = i + m.end()
            num, j = _braced(s, j)
            while j < len(s) and s[j] == " ":
                j += 1
            den, j = _braced(s, j) if j < len(s) and s[j] == "{" else ("", j)
            out.append(f"{_group(_frac_sqrt(num))}/{_group(_frac_sqrt(den))}")
            i = j
            continue
        m = re.match(r"\\sqrt\s*(?=\{)", s[i:])
        if m:
            inner, i = _braced(s, i + m.end())
            out.append(f"√({_frac_sqrt(inner).strip()})")
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


_SIMPLE = (
    (r"\\left\s*", ""), (r"\\right\s*", ""),
    (r"\\cdot\b", "·"), (r"\\times\b", "·"),
    (r"\\pm\b", "±"), (r"\\mp\b", "∓"),
    (r"\\leq?\b", "≤"), (r"\\geq?\b", "≥"), (r"\\neq?\b", "≠"),
    (r"\\sqrt\b\s*", "√"),
    (r"\\varnothing\b|\\emptyset\b", "∅"),
    (r"\\infty\b", "∞"), (r"\\cup\b", "∪"), (r"\\in\b", "∈"),   # промежутки и объединения (неравенства)
    (r"\\Rightarrow\b|\\implies\b|\\to\b", "⇒"),
    (r"\\[,;:!]|\\quad\b|\\qquad\b|~", " "),
)
_SUB = {"0": "₀", "1": "₁", "2": "₂", "3": "₃"}
_SUP = {"2": "²", "3": "³"}


def clean_text(text: str) -> str:
    """Ответ модели → строка для checker.normalize. Правила — только то, что реально
    встречается в ответах vision-моделей: обрывки LaTeX и лишние пробелы."""
    s = str(text or "")
    s = s.replace("$", "")
    s = s.replace("\\{", "").replace("\\}", "")  # скобки множества и системы: иначе остаётся «\ »
    # \text{Ответ}: → Ответ:, \mathrm{D} → D
    s = re.sub(r"\\(?:text|mathrm|mbox|textbf)\s*\{([^{}]*)\}", r"\1", s)
    s = _frac_sqrt(s)
    for pat, rep in _SIMPLE:
        s = re.sub(pat, rep, s)
    # индексы: x_{1,2} → x₁,₂; x_{1}, x_1 → x₁
    s = re.sub(r"_\{\s*1\s*,\s*2\s*\}", "₁,₂", s)
    s = re.sub(r"([A-Za-z])_(?:\{\s*([0-3])\s*\}|([0-3])(?![0-9]))",
               lambda m: m.group(1) + _SUB[m.group(2) or m.group(3)], s)
    # степени: ^{2} → ², ^{2x} → ^(2x), ^2 → ²
    s = re.sub(r"\^\s*\{\s*([23])\s*\}", lambda m: _SUP[m.group(1)], s)
    s = re.sub(r"\^\s*\{([^{}]*)\}", lambda m: "^" + _group(m.group(1)), s)
    s = re.sub(r"\^\s*([23])(?![0-9])", lambda m: _SUP[m.group(1)], s)
    # оставшиеся фигурные скобки LaTeX-группировки
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


# --------------------------------------------------------------------------
# 2. Уверенность строк
# --------------------------------------------------------------------------

def is_unparsable(text: str) -> bool:
    """Нет метки (ответ, проверка, ОДЗ, «не подходит») и хотя бы одна часть не разбирается."""
    nm = checker.normalize(text.replace("±", "+"))
    if nm.label in _LABELS:
        return False
    body = nm.text.replace("±", "+").strip()
    if not body:  # строка из одних слов — это текст, а не формула
        return False
    parts = re.split(r"[=|;]|,\s*(?=[A-Za-z](?:_\d)?\s*=)", body)
    for part in parts:
        part = re.split(r"[<>≤≥]", part)[0]
        try:
            checker.parse(part)
        except checker.ParseError:
            return True
    return False


def _unparsable(text: str, kind=None) -> bool:
    """Вид задачи из core.kinds (неравенство, система, …) сам решает, разбирается ли строка:
    «x ∈ [−2; 3]», «+ − +», «(3; 2)» — верная запись, а не ошибка распознавания."""
    if kind:
        from .kinds import KINDS, PARSES  # внутри функции: модули видов импортируют checker
        if kind in KINDS and kind in PARSES:
            try:
                return not PARSES[kind](text)
            except Exception:  # noqa: BLE001 — любая ошибка разбора = строку нужно проверить
                return True
    return is_unparsable(text)


def score_line(line, kind=None) -> dict:
    """{'text', 'unsure'[, 'alternatives']} → та же строка + confidence и flags.
    kind — вид задачи (problems.kind); без него — общий разбор, как раньше."""
    if isinstance(line, str):
        line = {"text": line, "unsure": []}
    out = dict(line)
    text = str(out.get("text", ""))
    out["text"] = text
    out["unsure"] = [str(u) for u in (out.get("unsure") or []) if str(u).strip()]
    conf, flags = OK_CONFIDENCE, []
    if "?" in text or out["unsure"]:
        flags.append("illegible")
        conf = min(conf, ILLEGIBLE_MAX)
    if _unparsable(text, kind):
        flags.append("unparsable")
        conf = min(conf, UNPARSABLE_MAX)
    alts = [a for a in (out.get("alternatives") or [])]
    if len(alts) > 1 and len({_key(a) for a in alts}) > 1:
        flags.append("disagree")
        conf = min(conf, DISAGREE_MAX)
    out["confidence"] = conf
    out["flags"] = flags
    return out


def score_lines(lines: list, kind=None) -> list:
    return [score_line(l, kind) for l in lines]


def needs_review(line: dict) -> bool:
    return float(line.get("confidence", 1.0)) < UNSURE


# --------------------------------------------------------------------------
# 3. Два прочтения (этап B1): выравнивание строк
# --------------------------------------------------------------------------

def _key(text: str) -> str:
    """Ключ сравнения прочтений: нормализованная строка без пробелов."""
    return checker.normalize(clean_text(text)).text.replace(" ", "").lower() or str(text).strip().lower()


def merge_readings(first: list, second: list) -> list:
    """Два прочтения одной задачи → одна последовательность строк.

    Совпавшие строки берутся из первого прочтения (unsure — объединение).
    Разошедшиеся — текст первого прочтения, оба варианта в alternatives.
    Строка, которая есть только в одном прочтении, получает второй вариант «» —
    «такой строки нет»."""
    a = [dict(l) for l in first]
    b = [dict(l) for l in second]
    sm = difflib.SequenceMatcher(a=[_key(l["text"]) for l in a], b=[_key(l["text"]) for l in b], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for la, lb in zip(a[i1:i2], b[j1:j2]):
                la["unsure"] = list(dict.fromkeys(list(la.get("unsure") or []) + list(lb.get("unsure") or [])))
                out.append(la)
            continue
        n = max(i2 - i1, j2 - j1)
        for k in range(n):
            la = a[i1 + k] if i1 + k < i2 else None
            lb = b[j1 + k] if j1 + k < j2 else None
            base = dict(la or lb)
            base["alternatives"] = [la["text"] if la else "", lb["text"] if lb else ""]
            base["unsure"] = list(dict.fromkeys(list((la or {}).get("unsure") or []) +
                                                list((lb or {}).get("unsure") or [])))
            out.append(base)
    return out


# --------------------------------------------------------------------------
# 4. Какие строки учитель не проверил
# --------------------------------------------------------------------------

def numbered(lines: list) -> list:
    """[(line_no, text)] — нумерация как в checker: с 1 по позиции, пустые строки пропускаются."""
    return [(i, l) for i, l in enumerate(lines, start=1) if str(l).strip()]


def unverified(ocr_raw: dict, answers: dict) -> dict:
    """Строки из распознавания с confidence < UNSURE, которые учитель не исправил.

    ocr_raw: {problem_id: [{'text', 'confidence', ...}]}, answers: {problem_id: [строки]}.
    Строки ответа сопоставляются с распознанными по тексту (после strip()) с учётом
    порядка, поэтому вставка или удаление строки учителем не сбивает соответствие.
    → {(problem_id, line_no): confidence}"""
    out = {}
    for pid, rec in (ocr_raw or {}).items():
        rec = [r for r in rec if str(r.get("text", "")).strip()]
        ans = numbered(answers.get(pid) or [])
        sm = difflib.SequenceMatcher(a=[str(r["text"]).strip() for r in rec],
                                     b=[str(t).strip() for _, t in ans], autojunk=False)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                r = rec[blk.a + k]
                conf = float(r.get("confidence", 1.0))
                if conf < UNSURE:
                    out[(pid, ans[blk.b + k][0])] = conf
    return out


def edit_stats(ocr_raw: dict, answers: dict) -> dict:
    """Сколько распознанных строк учитель исправил: {'edited', 'total'} (этап B5)."""
    total = edited = 0
    for pid, rec in (ocr_raw or {}).items():
        rec_t = [str(r.get("text", "")).strip() for r in rec if str(r.get("text", "")).strip()]
        ans_t = [str(t).strip() for _, t in numbered(answers.get(pid) or [])]
        sm = difflib.SequenceMatcher(a=rec_t, b=ans_t, autojunk=False)
        same = sum(b.size for b in sm.get_matching_blocks())
        total += len(rec_t)
        edited += len(rec_t) - same
    return {"edited": edited, "total": total}


def error_unverified(problem_id: int, result, line_confidence: dict) -> bool:
    """Опирается ли первая ошибка задачи на непроверенное распознавание.

    Да, если неуверенная и неправленая строка — это строка ошибки или строка перед ней
    (ошибка — это переход «строка до → строка ошибки»), либо если в задаче есть такая строка,
    которую проверка не смогла разобрать: пропущенная строка меняет вывод (например, x₂ = ?
    превращает верное решение в «потерю корня» в строке x₁)."""
    fe = getattr(result, "first_error", None)
    if not fe or not line_confidence:
        return False
    low = {no for (pid, no), c in line_confidence.items() if pid == problem_id and c < UNSURE}
    if fe["line"] in low or fe.get("prev_line") in low:
        return True
    return any(l.no in low and l.status == "unparsed" for l in getattr(result, "lines", []))

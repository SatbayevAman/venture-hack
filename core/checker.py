"""Проверка рукописного решения по строкам.

Вход: условие задачи и список строк решения (как их распознала vision-модель
или ввёл учитель). Выход: статус каждой строки, первая ошибка с тегом из
словаря, методы и привычки. Здесь нет языковой модели: всё решает SymPy и
правила, поэтому каждый вывод воспроизводим.

Типы задач:
  * equation   — уравнение (линейное, квадратное, дробно-рациональное);
                 соседние строки сравниваются по множеству решений на R
                 (с учётом ОДЗ исходного уравнения);
  * expression — упрощение выражения; соседние выражения должны быть
                 тождественно равны (simplify или совпадение в 20 точках).
"""
from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass, field
from typing import Optional

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
LOCALS = {c: sp.Symbol(c) for c in string.ascii_letters}
LOCALS["sqrt"] = sp.sqrt
for _v in ("x", "y"):
    for _i in ("1", "2"):
        LOCALS[f"{_v}_{_i}"] = sp.Symbol(f"{_v}_{_i}")
X = LOCALS["x"]
K = sp.Symbol("k_")  # вспомогательный символ «неизвестная константа»

CYR = "а-яёәғқңөұүһіА-ЯЁӘҒҚҢӨҰҮҺІ"
_ALLOWED = re.compile(r"^[0-9a-zA-Z_+\-*/^().\s]*$")


# --------------------------------------------------------------------------
# 1. Нормализация строки
# --------------------------------------------------------------------------

KW_ANSWER = ("ответ", "жауап", "жауаб", "answer")
KW_CHECK = ("провер", "тексер", "check")
KW_DOMAIN = ("одз", "ооу", "ммж", "область допуст")
KW_REJECT = ("не подход", "посторон", "не удовл", "не входит", "не принадлеж",
             "жарамайды", "бөгде", "сәйкес келмейді", "кірмейді")
KW_EMPTY = ("нет корней", "корней нет", "нет решений", "решений нет",
            "нет действительных", "түбірі жоқ", "түбірлері жоқ", "шешімі жоқ",
            "шешімдері жоқ", "∅")
KW_VIETA = ("виет", "vieta")


@dataclass
class Norm:
    text: str                 # строка, готовая к разбору SymPy (может быть пустой)
    label: Optional[str]      # answer | check | domain | rejected | None
    empty: bool = False       # «корней нет»
    vieta_kw: bool = False
    check_mark: bool = False  # ✓ в строке


_MATHCH = set("0123456789+-*/^()=<>≠.,²³")


def _cyr_lookalikes(s: str) -> str:
    """Кириллические х, у, а, с, Д внутри формулы → латиница.

    Буква заменяется, только если ближайший непробельный сосед — цифра,
    знак действия или латинская буква, а не кириллица (иначе это слово).
    """
    table = {"х": "x", "Х": "x", "у": "y", "а": "a", "с": "c", "Д": "D"}
    out = []
    n = len(s)
    for i, ch in enumerate(s):
        if ch in table:
            j = i - 1
            while j >= 0 and s[j] == " ":
                j -= 1
            k = i + 1
            while k < n and s[k] == " ":
                k += 1
            left = s[j] if j >= 0 else ""
            right = s[k] if k < n else ""
            adj_left = s[i - 1] if i > 0 else ""
            adj_right = s[i + 1] if i + 1 < n else ""
            cyr = re.compile(f"[{CYR}]")
            if cyr.match(adj_left or " ") or cyr.match(adj_right or " "):
                out.append(ch)
                continue
            mathy = any(c and (c in _MATHCH or c.isascii() and c.isalpha()) for c in (left, right))
            if mathy and not (cyr.match(left or " ") and cyr.match(right or " ")):
                out.append(table[ch])
                continue
        out.append(ch)
    return "".join(out)


def _split_identifiers(s: str) -> str:
    """ac → a*c; sqrt, x_1 и одиночные буквы оставляем как есть."""
    def repl(m: re.Match) -> str:
        tok = m.group(0)
        if tok == "sqrt" or re.fullmatch(r"[a-zA-Z](_\d)?", tok):
            return tok
        m2 = re.fullmatch(r"([a-zA-Z]+)(_\d)?", tok)
        if m2:
            head, idx = m2.group(1), m2.group(2) or ""
            return "*".join(head[:-1] + head[-1]) + idx if len(head) > 1 else head + idx
        return "*".join(c for c in tok if c != "_")
    return re.sub(r"[A-Za-z_][A-Za-z0-9_]*", repl, s)


def normalize(raw: str) -> Norm:
    s = raw.strip()
    low = s.lower()
    label = None
    if re.match(r"^\s*(" + "|".join(KW_ANSWER) + ")", low):
        label = "answer"
    elif any(k in low for k in KW_CHECK):
        label = "check"
    elif any(k in low for k in KW_REJECT):
        label = "rejected"
    elif any(k in low for k in KW_DOMAIN) or "≠" in s or "!=" in s:
        label = "domain"
    empty = any(k in low for k in KW_EMPTY)
    vieta_kw = any(k in low for k in KW_VIETA)
    check_mark = any(c in s for c in ("✓", "✔", "✅"))

    # символы
    for a, b in (("−", "-"), ("–", "-"), ("—", "-"), ("‒", "-"), ("·", "*"), ("×", "*"),
                 ("∙", "*"), ("⋅", "*"), ("•", "*"), ("÷", "/"), ("²", "^2"), ("³", "^3"),
                 ("₁", "1"), ("₂", "2"), ("＝", "="), ("✓", ""), ("✔", ""), ("✅", ""),
                 ("**", "^"), ("⁰", "^0"), ("¹", "^1")):
        s = s.replace(a, b)
    # метка в начале строки: «Ответ:», «Проверка:», «ОДЗ:»
    s = re.sub(rf"^[\s{CYR}.]+:", " ", s)
    s = _cyr_lookalikes(s)
    # «или» → разделитель частей, «и» в ответе → «;»
    s = re.sub(r"(?i)\s*(или|немесе|\bor\b|∨)\s*", " | ", s)
    if label == "answer":
        s = re.sub(r"(?i)\s+(и|және|and)\s+", ";", s)
    # скобки с пояснениями на кириллице: «(ОДЗ)», «(по т. Виета)»
    s = re.sub(rf"\([^()]*[{CYR}][^()]*\)", " ", s)
    # слова на кириллице и хвостовые пояснения
    s = re.sub(rf"[{CYR}]+\.?", " ", s)
    s = s.replace("∅", " ").replace("{", " ").replace("}", " ")
    s = s.replace("[", "(").replace("]", ")")
    # x1,2 = ... → x = ... (с ±)
    s = re.sub(r"(?<![A-Za-z])([xy])\s*_?\s*1\s*,\s*2", r"\1", s)
    # индексы корней: x1, x_1 → x_1
    s = re.sub(r"(?<![A-Za-z0-9_])([xy])\s*_?\s*([12])(?![0-9])", r"\1_\2", s)
    # десятичная запятая
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    # корни: √49, √D, √(…)
    s = re.sub(r"√\s*\(", "sqrt(", s)
    s = re.sub(r"√\s*([0-9.]+|[A-Za-z](?:_\d)?)", r"sqrt(\1)", s)
    s = s.replace("√", "sqrt")
    s = s.replace(":", "/")  # «6 : 2» — деление
    s = re.sub(r"^\s*(№\s*\d+|\d+\s*\))\s*", "", s)  # нумерация задач «№1», «1)»
    s = s.replace("№", " ")
    s = re.sub(r"\s+", " ", s).strip().strip(".;,").strip()
    return Norm(text=s, label=label, empty=empty, vieta_kw=vieta_kw, check_mark=check_mark)


# --------------------------------------------------------------------------
# 2. Разбор в SymPy (с белым списком символов — строка идёт в parse_expr)
# --------------------------------------------------------------------------

class ParseError(Exception):
    pass


def parse(s: str, evaluate: bool = True) -> sp.Expr:
    s = s.strip()
    if not s:
        raise ParseError("пусто")
    if len(s) > 200 or not _ALLOWED.match(s):
        raise ParseError("недопустимые символы")
    if re.search(r"\^\s*\(?\s*-?\d{3,}", s) or "^^" in s.replace(" ", ""):
        raise ParseError("слишком большая степень")
    s = _split_identifiers(s)
    try:
        e = parse_expr(s, local_dict=dict(LOCALS), transformations=TRANSFORMS, evaluate=evaluate)
    except Exception as ex:  # noqa: BLE001 — любой сбой разбора = «не распознано»
        raise ParseError(str(ex)) from ex
    if not isinstance(e, sp.Expr):
        raise ParseError("не выражение")
    return e


def ev(e: sp.Expr) -> sp.Expr:
    """Вычислить невычисленное дерево (после parse(evaluate=False))."""
    try:
        return e.doit()
    except Exception:  # noqa: BLE001
        return sp.sympify(str(e))


def _cmp_split(seg: str) -> str:
    """«1 > 0» → «1»: отбрасываем сравнения в хвосте."""
    return re.split(r"[<>≤≥]", seg)[0].strip()


# --------------------------------------------------------------------------
# 3. Множества решений и сравнение
# --------------------------------------------------------------------------

_solve_cache: dict = {}


def solve(diff: sp.Expr, var: sp.Symbol = X):
    key = (sp.srepr(diff), str(var))
    if key in _solve_cache:
        return _solve_cache[key]
    try:
        S = sp.solveset(diff, var, sp.S.Reals)
    except Exception:  # noqa: BLE001
        S = None
    _solve_cache[key] = S
    return S


def finite_vals(S) -> Optional[list]:
    if isinstance(S, sp.FiniteSet):
        try:
            return sorted(float(v) for v in S)
        except TypeError:
            return None
    if S == sp.S.EmptySet:
        return []
    return None


def _minus(S, excl: list):
    if S is None:
        return None
    if excl:
        return sp.Complement(S, sp.FiniteSet(*excl))
    return S


def vals_eq(a: list, b: list) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(x - y) < 1e-9 * (1 + abs(x)) for x, y in zip(sorted(a), sorted(b)))


def vals_subset(a: list, b: list) -> bool:
    """a ⊂ b строго."""
    return len(a) < len(b) and all(any(abs(x - y) < 1e-9 * (1 + abs(x)) for y in b) for x in a)


def set_equal(A, B) -> bool:
    if A is None or B is None:
        return False
    fa, fb = finite_vals(A), finite_vals(B)
    if fa is not None and fb is not None:
        return vals_eq(fa, fb)
    try:
        return bool(A == B) or bool(sp.simplify(sp.Complement(A, B)) == sp.S.EmptySet and sp.simplify(sp.Complement(B, A)) == sp.S.EmptySet)
    except Exception:  # noqa: BLE001
        return False


def expr_equal(a: sp.Expr, b: sp.Expr) -> bool:
    try:
        if sp.cancel(sp.together(a - b)) == 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    syms = sorted((a - b).free_symbols, key=str)
    rng = random.Random(42)
    ok_points = 0
    for _ in range(20):
        sub = {s: sp.Float(rng.uniform(-4.3, 4.7)) for s in syms}
        try:
            va = complex(a.evalf(subs=sub))
            vb = complex(b.evalf(subs=sub))
        except Exception:  # noqa: BLE001
            continue
        if abs(va - vb) > 1e-7 * (1 + abs(va)):
            return False
        ok_points += 1
    return ok_points > 0


def domain_exclusions(diff: sp.Expr, var: sp.Symbol = X) -> list:
    try:
        den = sp.denom(sp.together(diff))
        if den.has(var):
            S = sp.solveset(den, var, sp.S.Reals)
            if isinstance(S, sp.FiniteSet):
                return list(S)
    except Exception:  # noqa: BLE001
        pass
    return []


def eq_equiv(d1: sp.Expr, d2: sp.Expr, excl: list, var: sp.Symbol = X) -> bool:
    """Равносильны ли уравнения d1 = 0 и d2 = 0 на ОДЗ исходного уравнения."""
    return set_equal(_minus(solve(d1, var), excl), _minus(solve(d2, var), excl))


# --------------------------------------------------------------------------
# 4. Классификаторы ошибок
# --------------------------------------------------------------------------

def _terms(e: sp.Expr) -> list:
    return [t for t in sp.Add.make_args(e) if t != 0]


def _flip_variants(L: sp.Expr, R: sp.Expr):
    """Все варианты «сменить знак одного слагаемого» в L = R."""
    for side, E in (("L", L), ("R", R)):
        for t in _terms(E):
            newE = ev(E) - 2 * ev(t)
            yield (newE, ev(R)) if side == "L" else (ev(L), newE), t


def _fsu_variants(e: sp.Expr):
    """Типичные неверные раскрытия (a±b)² и (a−b)(a+b) внутри выражения."""
    out = []
    for node in sp.preorder_traversal(e):
        if isinstance(node, sp.Pow) and node.exp == 2 and isinstance(node.base, sp.Add):
            ts = _terms(ev(node.base))
            if len(ts) == 2:
                t1, t2 = ts
                for wrong in (t1**2 + t2**2, t1**2 - t2**2, t2**2 - t1**2,
                              t1**2 + t1 * t2 + t2**2, t1**2 - 2 * t1 * t2 + t2**2):
                    if sp.expand(wrong - ev(node)) != 0:
                        out.append((node, wrong))
        if isinstance(node, sp.Mul):
            adds = [a for a in node.args if isinstance(a, sp.Add)]
            if len(adds) == 2:
                A, B = (_terms(ev(a)) for a in adds)
                if len(A) == 2 and len(B) == 2:
                    prod = sp.expand(ev(adds[0]) * ev(adds[1]))
                    for t1 in A:
                        for t2 in A:
                            if t1 is t2:
                                continue
                            if sp.expand(prod - (t1**2 - t2**2)) == 0:
                                rest = sp.Mul(*[a for a in node.args if a not in adds])
                                out.append((node, rest * (t1**2 + t2**2)))
    return out


def _numeric_paths(e: sp.Expr, path=()):
    if e.is_Number:
        yield path, e
        return
    if isinstance(e, sp.Pow):
        yield from _numeric_paths(e.base, path + (0,))
        return
    for i, a in enumerate(e.args):
        if isinstance(e, sp.Mul) and a == -1:
            continue
        yield from _numeric_paths(a, path + (i,))


def _calc_score(kv, path) -> float:
    """Какую из «исправленных» констант считать ошибочной: школьные числа
    обычно целые, а ошибаются чаще в свободном члене, чем в коэффициенте."""
    score = 0.0
    if getattr(kv, "is_integer", False):
        score += 2
    score -= 0.1 * len(path)  # глубоко вложенное число — скорее коэффициент
    return score


def _replace_at(e: sp.Expr, path, new):
    if not path:
        return new
    args = list(e.args)
    args[path[0]] = _replace_at(args[path[0]], path[1:], new)
    return e.func(*args)


def _fmt(v) -> str:
    v = sp.nsimplify(v) if isinstance(v, sp.Float) else v
    s = str(v).replace("**", "^").replace("*", "·").replace("sqrt", "√").replace("-", "−")
    return s


def classify_eq_step(prevLR, curLR, excl, var=X):
    """Почему строка cur не равносильна prev. → (tag, detail)."""
    pL, pR = prevLR
    cL, cR = curLR
    d_prev = ev(pL) - ev(pR)
    d_cur = ev(cL) - ev(cR)
    S_prev = _minus(solve(d_prev, var), excl)
    S_cur = _minus(solve(d_cur, var), excl)

    # формулы сокращённого умножения
    for side_i, E in enumerate((pL, pR)):
        for node, wrong in _fsu_variants(E):
            try:
                E2 = ev(E.xreplace({node: wrong}))
            except Exception:  # noqa: BLE001
                continue
            L2, R2 = (E2, ev(pR)) if side_i == 0 else (ev(pL), E2)
            if set_equal(_minus(solve(L2 - R2, var), excl), S_cur):
                return "fsu", {"f": _fmt(ev(node))}
    # знак: сменить знак одного слагаемого в текущей строке …
    for (L2, R2), t in _flip_variants(cL, cR):
        if set_equal(_minus(solve(L2 - R2, var), excl), S_prev):
            return "sign", {"term": _fmt(ev(t))}
    # … или в предыдущей (перенёс, не сменив знак, и сразу посчитал)
    for (L2, R2), t in _flip_variants(pL, pR):
        if set_equal(_minus(solve(L2 - R2, var), excl), S_cur):
            return "sign", {"term": _fmt(ev(t))}
    # вычислительная: одно число в строке неверно
    fv = finite_vals(S_prev)
    if fv:
        r = sp.nsimplify(fv[0])
        found = []
        for side_i, E in enumerate((cL, cR)):
            for path, num in _numeric_paths(E):
                try:
                    Ek = ev(_replace_at(E, path, K))
                    other = ev(cR) if side_i == 0 else ev(cL)
                    dk = (Ek - other) if side_i == 0 else (other - Ek)
                    cands = sp.solve(sp.Eq(dk.subs(var, r), 0), K)
                except Exception:  # noqa: BLE001
                    continue
                for kv in cands[:3]:
                    if not kv.is_real or sp.simplify(kv - num) == 0:
                        continue
                    if set_equal(_minus(solve(dk.subs(K, kv), var), excl), S_prev):
                        found.append((_calc_score(kv, path), num, kv))
        if found:
            found.sort(key=lambda t: -t[0])
            _, num, kv = found[0]
            return "calc", {"num": _fmt(num), "expected": _fmt(kv)}
    # потеря / лишний корень
    a, b = finite_vals(S_cur), finite_vals(S_prev)
    if a is not None and b is not None:
        if vals_subset(a, b):
            return "lost_root", _division_detail(pL, pR, d_prev, d_cur, var)
        if vals_subset(b, a):
            return "extra_root", {}
    return "other", {}


def _division_detail(pL, pR, d_prev, d_cur, var=X) -> dict:
    """Если prev = q·cur и q содержит x, а обе части prev делятся на q — «деление на q»."""
    try:
        q = sp.cancel(sp.together(d_prev) / sp.together(d_cur))
        if q.has(var) and q.is_polynomial(var):
            q = sp.factor(q)
            coeff, qn = q.as_coeff_Mul()
            qn = qn if qn.has(var) else q
            ok_sides = all(
                sp.rem(sp.expand(sp.numer(sp.together(ev(s)))), sp.expand(qn), var) == 0
                for s in (pL, pR)
            )
            if ok_sides:
                return {"f": _fmt(qn), "division": True}
    except Exception:  # noqa: BLE001
        pass
    return {}


def classify_expr_step(prev_u: sp.Expr, cur_u: sp.Expr):
    prev, cur = ev(prev_u), ev(cur_u)
    for node, wrong in _fsu_variants(prev_u):
        try:
            wrong_prev = ev(prev_u.xreplace({node: wrong}))
        except Exception:  # noqa: BLE001
            continue
        if expr_equal(wrong_prev, cur):
            return "fsu", {"f": _fmt(ev(node))}
    for t in _terms(cur_u):
        if expr_equal(cur - 2 * ev(t), prev):
            return "sign", {"term": _fmt(ev(t))}
    for t in _terms(prev_u):
        if expr_equal(prev - 2 * ev(t), cur):
            return "sign", {"term": _fmt(ev(t))}
    syms = sorted((prev - cur).free_symbols, key=str)
    rng = random.Random(7)
    pt = {s: sp.Rational(rng.randint(2, 9), rng.randint(1, 3)) for s in syms}
    found = []
    for path, num in _numeric_paths(cur_u):
        try:
            ck = ev(_replace_at(cur_u, path, K))
            cands = sp.solve(sp.Eq((ck - prev).subs(pt), 0), K)
        except Exception:  # noqa: BLE001
            continue
        for kv in cands[:3]:
            if kv.is_real and sp.simplify(kv - num) != 0 and expr_equal(ck.subs(K, kv), prev):
                found.append((_calc_score(kv, path), num, kv))
    if found:
        found.sort(key=lambda t: -t[0])
        _, num, kv = found[0]
        return "calc", {"num": _fmt(num), "expected": _fmt(kv)}
    return "other", {}


# --------------------------------------------------------------------------
# 5. Разбор одной строки уравнения
# --------------------------------------------------------------------------

@dataclass
class LineResult:
    no: int
    raw: str
    norm: str = ""
    kind: str = "text"       # eq | eqs | root | disc | vieta | numeric | answer | domain | rejected | check | expr | text | unparsed
    status: str = "info"     # ok | error | after | info | unparsed
    note: str = ""
    values: list = field(default_factory=list)


@dataclass
class CheckResult:
    kind: str                     # equation | expression
    lines: list
    first_error: Optional[dict]
    methods: list
    habits: list
    final: Optional[list]
    true_answer: Optional[list]
    correct: Optional[bool]
    solution_lines: int


def _split_parts(text: str) -> list:
    """«x1 = 3, x2 = 2» / «x=0 | x-5=0» → части."""
    parts = []
    for chunk in text.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        sub = re.split(r"[;,]\s*(?=[A-Za-z](?:_\d)?\s*=)", chunk)
        parts.extend(p.strip() for p in sub if p.strip())
    return parts


def _expand_pm(text: str) -> list:
    if "±" in text or "+-" in text.replace(" ", ""):
        t = text.replace("+-", "±")
        return [t.replace("±", "+"), t.replace("±", "-")]
    return [text]


def _numeric_chain(segs: list, subs: dict):
    """Последовательные числовые части «= a = b = c»: (значения, ok)."""
    vals = []
    for seg in segs:
        seg = _cmp_split(seg)
        if not seg:
            continue
        try:
            e = ev(parse(seg, evaluate=False)).subs(subs)
        except ParseError:
            continue
        if e.free_symbols:
            continue
        try:
            vals.append(sp.nsimplify(e) if e.is_Float else sp.simplify(e))
        except Exception:  # noqa: BLE001
            vals.append(e)
    ok = True
    for a, b in zip(vals, vals[1:]):
        try:
            if abs(complex(a) - complex(b)) > 1e-9 * (1 + abs(complex(a))):
                ok = False
        except TypeError:
            ok = False
    return vals, ok


def _parse_values(text: str) -> list:
    vals = []
    for chunk in re.split(r"[;|]|,(?!\d)", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        for variant in _expand_pm(chunk):
            rhs = variant.split("=")[-1].strip()
            if not rhs:
                continue
            try:
                e = ev(parse(rhs, evaluate=False))
            except ParseError:
                continue
            if not e.free_symbols:
                try:
                    vals.append(float(e))
                except TypeError:
                    pass
    return vals


def _quad_coeffs(diff: sp.Expr, var=X):
    try:
        num = sp.numer(sp.together(diff))
        P = sp.Poly(sp.expand(num), var)
        if P.degree() == 2:
            a, b, c = P.all_coeffs()
            return a, b, c
    except Exception:  # noqa: BLE001
        pass
    return None


def _is_factored_zero(L, R, var=X) -> bool:
    for A, B in ((L, R), (R, L)):
        if ev(B) == 0 and isinstance(A, sp.Mul):
            factors = [f for f in A.args if ev(f).has(var)]
            if len(factors) >= 2:
                return True
    return False


def _pnames(p: int) -> dict:
    return {
        "P_acc": "условие" if p == 0 else f"строку {p}",
        "P_gen": "условия" if p == 0 else f"строки {p}",
        "P_nom_kk": "шарт" if p == 0 else f"{p}-жол",
        "P_abl_kk": "шарттан" if p == 0 else f"{p}-жолдан",
    }


def check_equation(statement: str, raw_lines: list, reference_len: Optional[int] = None) -> CheckResult:
    st = normalize(statement).text
    sL, sR = (parse(p, evaluate=False) for p in st.split("=", 1))
    var = X if X in (ev(sL) - ev(sR)).free_symbols else sorted((ev(sL) - ev(sR)).free_symbols, key=str)[0]
    d0 = ev(sL) - ev(sR)
    excl = domain_exclusions(d0, var)
    T = solve(d0, var)
    true_vals = finite_vals(T)
    quadratic = _quad_coeffs(d0, var) is not None

    prev = (sL, sR)
    prev_no = 0
    prev_raw = statement
    results: list[LineResult] = []
    first_error = None
    group: list = []          # [(line_no, value)]
    group_start = None
    rejected: list = []
    answer = None
    methods: set = set()
    habits: set = set()
    seen_roots = False
    last_D = None
    n_solution = 0

    def err(line: LineResult, tag: str, detail: dict, p_no: int, p_raw: str):
        nonlocal first_error
        if first_error is None:
            first_error = {"line": line.no, "tag": tag, "detail": detail, "prev_line": p_no,
                           "evidence": f"{p_raw.strip()}  →  {line.raw.strip()}", **_pnames(p_no)}
            line.status = "error"
        else:
            line.status = "error"
            line.note = (line.note + " ещё одна ошибка").strip()

    def close_group():
        nonlocal group, group_start
        if not group:
            return
        G = []
        for _, v in group:
            if not any(abs(v - u) < 1e-9 for u in G):
                G.append(v)
        P = finite_vals(solve(ev(prev[0]) - ev(prev[1]), var))
        line = next(r for r in results if r.no == group_start)
        if P is not None and not vals_eq(G, P):
            if vals_eq(G, [-v for v in P]):
                tag, det = "sign", {}
            elif vals_subset(G, P):
                gpoly = sp.Mul(*[(var - sp.nsimplify(v)) for v in G]) if G else sp.Integer(1)
                det = _division_detail(prev[0], prev[1], ev(prev[0]) - ev(prev[1]), gpoly, var)
                tag = "lost_root"
            elif vals_subset(P, G):
                tag, det = "extra_root", {}
            else:
                tag, det = "calc", {}
            if first_error is None:
                err(line, tag, det, prev_no, prev_raw)
            else:
                line.status = "error"
        group = []
        group_start = None

    for i, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            continue
        nm = normalize(raw)
        lr = LineResult(no=i, raw=raw, norm=nm.text)
        results.append(lr)
        if nm.vieta_kw:
            methods.add("vieta")
        txt = nm.text

        # --- метки
        if nm.label == "domain":
            lr.kind, lr.status = "domain", "info"
            habits.add("domain_noted")
            continue
        if nm.label == "rejected":
            close_group()
            lr.kind, lr.status = "rejected", "info"
            lr.values = _parse_values(txt)
            rejected.extend(lr.values)
            continue
        if nm.label == "answer" or (nm.empty and nm.label is None):
            close_group()
            lr.kind = "answer"
            lr.values = [] if nm.empty else _parse_values(txt)
            answer = (i, lr.values)
            lr.status = "ok"
            continue
        if nm.label == "check" or (nm.check_mark and seen_roots):
            lr.kind, lr.status = "check", "info"
            habits.add("check_done")
            if "=" in txt:
                vals, ok = _numeric_chain(txt.split("="), {})
                lr.note = "✓" if ok and len(vals) >= 2 else ("✗ проверка не сошлась" if len(vals) >= 2 else "")
            continue
        if not txt:
            lr.kind, lr.status = "text", "info"
            continue

        # --- части строки
        parts = []
        for p in _split_parts(txt):
            parts.extend(_expand_pm(p))
        try:
            parsed = []
            for p in parts:
                segs = [s for s in p.split("=")]
                parsed.append([_cmp_split(s) for s in segs])
        except Exception:  # noqa: BLE001
            parsed = []

        def is_root_part(segs) -> bool:
            if len(segs) < 2:
                return False
            try:
                head = parse(segs[0], evaluate=False)
            except ParseError:
                return False
            if not (isinstance(head, sp.Symbol) and (head == var or str(head).startswith(str(var) + "_"))):
                return False
            for s in segs[1:]:
                try:
                    e = ev(parse(s, evaluate=False))
                except ParseError:
                    return False
                if e.free_symbols - {sp.Symbol("D")}:
                    return False
            return True

        try:
            # дискриминант
            if len(parsed) == 1 and len(parsed[0]) >= 2 and re.fullmatch(r"D(_1)?|D/4", parsed[0][0].replace(" ", "")):
                lr.kind = "disc"
                methods.add("disc")
                n_solution += 1
                vals, ok = _numeric_chain(parsed[0][1:], {})
                qc = _quad_coeffs(ev(prev[0]) - ev(prev[1]), var)
                if not ok:
                    if first_error is None:
                        err(lr, "calc", {"where": "D"}, prev_no, prev_raw)
                    lr.status = "error"
                elif vals and qc:
                    a, b, c = qc
                    trueD = [b**2 - 4 * a * c, (b / 2) ** 2 - a * c]
                    if not any(abs(float(vals[-1]) - float(t)) < 1e-9 for t in trueD):
                        if first_error is None:
                            err(lr, "calc", {"num": _fmt(vals[-1]), "where": "D"}, prev_no, prev_raw)
                        lr.status = "error"
                    else:
                        lr.status = "ok" if first_error is None else "after"
                else:
                    lr.status = "ok" if first_error is None else "after"
                if vals:
                    last_D = vals[-1]
                continue

            # теорема Виета: x1 + x2 = s, x1·x2 = p
            if len(parsed) == 1 and len(parsed[0]) >= 2:
                head = ev(parse(parsed[0][0], evaluate=False))
                x1, x2 = LOCALS[f"{var}_1"] if f"{var}_1" in LOCALS else None, LOCALS.get(f"{var}_2")
                if x1 is not None and head.free_symbols == {x1, x2}:
                    lr.kind = "vieta"
                    methods.add("vieta")
                    n_solution += 1
                    vals, ok = _numeric_chain(parsed[0][1:], {})
                    qc = _quad_coeffs(ev(prev[0]) - ev(prev[1]), var)
                    good = ok
                    if ok and vals and qc:
                        a, b, c = qc
                        target = -b / a if sp.expand(head - (x1 + x2)) == 0 else (c / a if sp.expand(head - x1 * x2) == 0 else None)
                        if target is not None and abs(float(vals[-1]) - float(target)) > 1e-9:
                            good = False
                    if not good:
                        if first_error is None:
                            err(lr, "calc", {"num": _fmt(vals[-1]) if vals else "", "where": "vieta"}, prev_no, prev_raw)
                        lr.status = "error"
                    else:
                        lr.status = "ok" if first_error is None else "after"
                    continue

            # корни: x = 3; x1 = (5+1)/2 = 3; x = 0 или x = 5
            if parsed and all(is_root_part(s) for s in parsed):
                lr.kind = "root"
                n_solution += 1
                seen_roots = True
                if group_start is None:
                    group_start = i
                chain_ok = True
                for segs in parsed:
                    vals, ok = _numeric_chain(segs[1:], {sp.Symbol("D"): last_D} if last_D is not None else {})
                    chain_ok = chain_ok and ok
                    if vals:
                        v = float(vals[-1])
                        lr.values.append(v)
                        group.append((i, v))
                if not chain_ok:
                    if first_error is None:
                        err(lr, "calc", {}, prev_no, prev_raw)
                    lr.status = "error"
                else:
                    lr.status = "ok" if first_error is None else "after"
                continue

            # уравнение (или совокупность «… или …»)
            eqs = []
            for segs in parsed:
                if len(segs) < 2:
                    continue
                L = parse(segs[-2], evaluate=False)
                R = parse(segs[-1], evaluate=False)
                eqs.append((L, R))
            if eqs and any((ev(L) - ev(R)).has(var) for L, R in eqs):
                close_group()
                n_solution += 1
                if len(eqs) == 1:
                    lr.kind = "eq"
                    cur = eqs[0]
                    d_cur = ev(cur[0]) - ev(cur[1])
                    if quadratic and _is_factored_zero(cur[0], cur[1], var):
                        methods.add("factoring")
                else:
                    lr.kind = "eqs"
                    if quadratic:
                        methods.add("factoring")
                    d_cur = sp.Mul(*[ev(L) - ev(R) for L, R in eqs])
                    cur = (d_cur, sp.Integer(0))
                d_prev = ev(prev[0]) - ev(prev[1])
                if eq_equiv(d_prev, d_cur, excl, var):
                    lr.status = "ok" if first_error is None else "after"
                else:
                    if first_error is None:
                        tag, det = classify_eq_step(prev, cur, excl, var)
                        err(lr, tag, det, prev_no, prev_raw)
                    else:
                        lr.status = "error"
                prev, prev_no, prev_raw = cur, i, raw
                continue

            # числовая строка без x: проверка подстановкой (после корней)
            if len(parsed) == 1 and len(parsed[0]) >= 2:
                vals, ok = _numeric_chain(parsed[0], {})
                if len(vals) >= 2:
                    lr.kind = "numeric"
                    if seen_roots:
                        lr.kind = "check"
                        habits.add("check_done")
                        lr.note = "✓" if ok else "✗ проверка не сошлась"
                    lr.status = "info"
                    continue
            lr.kind, lr.status = "text", "info"
        except ParseError:
            lr.kind, lr.status = "unparsed", "unparsed"
            lr.note = "не удалось разобрать строку — исправьте распознавание"

    close_group()

    # итоговый ответ
    if answer is not None:
        final = answer[1]
        ans_line_no = answer[0]
    elif group or any(r.kind == "root" for r in results):
        roots = [v for r in results if r.kind == "root" for v in r.values]
        final = [v for v in roots if not any(abs(v - q) < 1e-9 for q in rejected)]
        ans_line_no = max(r.no for r in results if r.kind == "root")
    else:
        final, ans_line_no = None, None
    # дубликаты
    if final is not None:
        uniq = []
        for v in final:
            if not any(abs(v - u) < 1e-9 for u in uniq):
                uniq.append(v)
        final = uniq

    correct = None
    if final is not None and true_vals is not None:
        correct = vals_eq(final, true_vals)
        if not correct and first_error is None:
            line = next(r for r in results if r.no == ans_line_no)
            if vals_subset(final, true_vals):
                tag = "lost_root"
            elif vals_subset(true_vals, final):
                tag = "extra_root"
            else:
                tag = "calc"
            det = {"domain": [_fmt(e) for e in excl]} if excl and tag == "extra_root" else {}
            err(line, tag, det, 0, statement)

    if correct and reference_len and n_solution * 2 < reference_len:
        habits.add("skip_steps")

    return CheckResult(
        kind="equation", lines=results, first_error=first_error,
        methods=sorted(methods) if quadratic else [], habits=sorted(habits),
        final=final, true_answer=true_vals, correct=correct, solution_lines=n_solution,
    )


def check_expression(statement: str, raw_lines: list, reference_len: Optional[int] = None,
                     reference_answer: Optional[str] = None) -> CheckResult:
    prev_u = parse(normalize(statement).text, evaluate=False)
    start = ev(prev_u)
    prev_no, prev_raw = 0, statement
    results = []
    first_error = None
    n_solution = 0
    last = None
    for i, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            continue
        nm = normalize(raw)
        lr = LineResult(no=i, raw=raw, norm=nm.text)
        results.append(lr)
        if nm.label == "check" or not nm.text:
            lr.kind, lr.status = ("check" if nm.label == "check" else "text"), "info"
            continue
        lr.kind = "answer" if nm.label == "answer" else "expr"
        try:
            segs = [parse(_cmp_split(s), evaluate=False) for s in nm.text.split("=") if s.strip()]
        except ParseError:
            lr.kind, lr.status = "unparsed", "unparsed"
            lr.note = "не удалось разобрать строку — исправьте распознавание"
            continue
        if not segs:
            continue
        n_solution += 1 if lr.kind == "expr" else 0
        status = "ok" if first_error is None else "after"
        for seg in segs:
            if not expr_equal(ev(seg), ev(prev_u)):
                if first_error is None:
                    tag, det = classify_expr_step(prev_u, seg)
                    first_error = {"line": i, "tag": tag, "detail": det, "prev_line": prev_no,
                                   "evidence": f"{prev_raw.strip()}  →  {raw.strip()}", **_pnames(prev_no)}
                status = "error"
            prev_u = seg
            last = seg
        lr.status = status
        prev_no, prev_raw = i, raw
    correct = None
    if last is not None:
        correct = expr_equal(ev(last), start)
        if correct and reference_answer:
            try:
                ref = ev(parse(normalize(reference_answer).text, evaluate=False))
                if sp.count_ops(ev(last)) > sp.count_ops(ref) + 2:
                    correct = None  # верно, но не упрощено до конца
            except ParseError:
                pass
    habits = []
    if correct and reference_len and n_solution * 2 < reference_len:
        habits.append("skip_steps")
    return CheckResult(
        kind="expression", lines=results, first_error=first_error, methods=[], habits=habits,
        final=[_fmt(ev(last))] if last is not None else None, true_answer=None, correct=correct,
        solution_lines=n_solution,
    )


def check_problem(kind: str, statement: str, raw_lines: list, reference_len=None, reference_answer=None) -> CheckResult:
    if kind == "expression":
        return check_expression(statement, raw_lines, reference_len, reference_answer)
    return check_equation(statement, raw_lines, reference_len)

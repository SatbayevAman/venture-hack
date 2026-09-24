"""Неравенства: линейные, квадратные и дробно-рациональные (метод интервалов).

Соседние строки-неравенства сравниваются по множеству решений на R (на ОДЗ
условия); первая строка, где множества разошлись, — место ошибки. Итоговый
ответ сравнивается с решением условия уже без поправки на ОДЗ, поэтому
выколотая точка знаменателя в ответе — ошибка «граница».

Виды строк:
  * неравенство, двойное неравенство, совокупность «x < 2 или x ≥ 3»;
  * запись промежутков «(−∞; 2) ∪ [3; +∞)», «x ∈ [1; 5)», «R», «∅»;
  * связанное уравнение и корни — критические точки (проверяются, но не
    меняют «предыдущую» строку);
  * знаки на промежутках «+ − +» — информационные;
  * метки «Ответ», «Проверка», «ОДЗ» — как в `checker.normalize`.

Строки со скобками промежутков и «∞» не проходят через `checker.normalize`,
поэтому промежутки разбираются по исходному тексту строки.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import sympy as sp

from .. import checker as C

VAR = C.X

OPS = {"<": sp.StrictLessThan, ">": sp.StrictGreaterThan, "<=": sp.LessThan, ">=": sp.GreaterThan}
FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<="}
_REL_SPLIT = re.compile(r"(<=|>=|<|>)")
_OR = re.compile(r"(?i)\s*(?:\bили\b|\bнемесе\b|\bor\b|∨)\s*")
_UNI = (("≤", "<="), ("⩽", "<="), ("≦", "<="), ("≥", ">="), ("⩾", ">="), ("≧", ">="),
        ("−", "-"), ("–", "-"), ("—", "-"), ("‒", "-"), ("＜", "<"), ("＞", ">"))


# --------------------------------------------------------------------------
# 1. Множества и их сравнение
# --------------------------------------------------------------------------

_cache: dict = {}


def solve_rel(expr: sp.Expr, op: str):
    """Множество решений expr op 0 на R (или None, если SymPy не справился)."""
    key = (sp.srepr(expr), op)
    if key not in _cache:
        try:
            S = sp.solveset(OPS[op](expr, 0), VAR, sp.S.Reals)
            if isinstance(S, (sp.ConditionSet, sp.ImageSet)) or S.has(sp.ConditionSet):
                S = None
        except Exception:  # noqa: BLE001
            S = None
        _cache[key] = S
    return _cache[key]


def breaks(S) -> Optional[list]:
    """Конечные граничные точки множества, по возрастанию."""
    if S is None:
        return None
    try:
        B = S.boundary
    except Exception:  # noqa: BLE001
        return None
    if B == sp.S.EmptySet:
        return []
    if not isinstance(B, sp.FiniteSet):
        return None
    try:
        return sorted((b for b in B if b.is_finite), key=float)
    except TypeError:
        return None


def contains(S, p) -> Optional[bool]:
    try:
        r = S.contains(p)
    except Exception:  # noqa: BLE001
        return None
    if r == sp.true:
        return True
    if r == sp.false:
        return False
    return None


def _near(a, b) -> bool:
    return abs(float(a) - float(b)) < 1e-9 * (1 + abs(float(a)))


def _merge(*lists) -> list:
    out: list = []
    for p in sorted((p for ls in lists for p in ls), key=float):
        if not out or not _near(out[-1], p):
            out.append(p)
    return out


def _samples(bps: list) -> list:
    if not bps:
        return [sp.Integer(0)]
    out = [bps[0] - 1]
    out += [(a + b) / 2 for a, b in zip(bps, bps[1:])]
    out.append(bps[-1] + 1)
    return out


@dataclass
class Cmp:
    regions: list   # [(в A, в B)] во внутренних точках промежутков между границами
    points: list    # [(точка, в A, в B)] в самих границах (кроме пропущенных)

    @property
    def same_regions(self) -> bool:
        return all(a == b for a, b in self.regions)

    @property
    def opposite_regions(self) -> bool:
        return all(a != b for a, b in self.regions)

    @property
    def diff_points(self) -> list:
        return [p for p, a, b in self.points if a != b]

    @property
    def equal(self) -> bool:
        return self.same_regions and not self.diff_points


def compare(A, B, skip=()) -> Optional[Cmp]:
    """Сравнить два объединения промежутков по точкам: границы обоих множеств,
    середины между ними и точки снаружи. Для таких множеств это точное сравнение.
    skip — точки, в которых сравнение не ведётся (выколотые точки ОДЗ)."""
    ba, bb = breaks(A), breaks(B)
    if ba is None or bb is None:
        return None
    bps = _merge(ba, bb)
    regions, points = [], []
    for p in _samples(bps):
        a, b = contains(A, p), contains(B, p)
        if a is None or b is None:
            return None
        regions.append((a, b))
    for p in bps:
        if any(_near(p, s) for s in skip):
            continue
        a, b = contains(A, p), contains(B, p)
        if a is None or b is None:
            return None
        points.append((p, a, b))
    return Cmp(regions, points)


def set_eq(A, B, skip=()) -> bool:
    c = compare(A, B, skip)
    return bool(c and c.equal)


def _pattern(S, bps: list) -> list:
    """Принадлежность S по порядку: снаружи слева, граница, середина, граница, …"""
    out = []
    smp = _samples(bps)
    for i, s in enumerate(smp):
        out.append(contains(S, s))
        if i < len(bps):
            out.append(contains(S, bps[i]))
    return out


def one_boundary_off(S_cur, S_ref) -> Optional[tuple]:
    """Отличается ровно одна граница, а вид множества тот же → (неверная, верная)."""
    bc, br = breaks(S_cur), breaks(S_ref)
    if bc is None or br is None or len(bc) != len(br) or not bc:
        return None
    diff = [i for i, (a, b) in enumerate(zip(bc, br)) if not _near(a, b)]
    if len(diff) != 1 or _pattern(S_cur, bc) != _pattern(S_ref, br):
        return None
    i = diff[0]
    return bc[i], br[i]


def _fmt_num(v) -> str:
    if v == sp.oo:
        return "+∞"
    if v == -sp.oo:
        return "−∞"
    return C._fmt(v)


def fmt_set(S) -> str:
    if S is None:
        return "?"
    if S == sp.S.Reals:
        return "(−∞; +∞)"
    if S == sp.S.EmptySet:
        return "∅"
    parts = S.args if isinstance(S, sp.Union) else (S,)
    out = []
    for p in parts:
        if isinstance(p, sp.Interval):
            lb = "(" if p.left_open or p.start == -sp.oo else "["
            rb = ")" if p.right_open or p.end == sp.oo else "]"
            out.append(f"{lb}{_fmt_num(p.start)}; {_fmt_num(p.end)}{rb}")
        elif isinstance(p, sp.FiniteSet):
            out.append("{" + "; ".join(_fmt_num(v) for v in sorted(p, key=float)) + "}")
        else:
            out.append(str(p))
    return " ∪ ".join(out)


# --------------------------------------------------------------------------
# 2. Разбор строк
# --------------------------------------------------------------------------

def _uni(s: str) -> str:
    for a, b in _UNI:
        s = s.replace(a, b)
    return s


def _strip_label(raw: str) -> str:
    s = re.sub(rf"^[\s{C.CYR}.]+:", " ", raw.strip())
    s = re.sub(r"^\s*[xхXХ]\s*∈", " ", s)
    return s.replace("∈", " ").strip().rstrip(".").strip()


@dataclass
class Rel:
    """Строка-неравенство: совокупность (или) цепочек (и) отношений L op R."""
    disj: list  # [[(L, op, R), …], …] — L, R невычисленные выражения SymPy

    @property
    def single(self) -> Optional[tuple]:
        if len(self.disj) == 1 and len(self.disj[0]) == 1:
            return self.disj[0][0]
        return None

    def solve(self):
        U = sp.S.EmptySet
        for chain in self.disj:
            I = sp.S.Reals
            for L, op, R in chain:
                S = solve_rel(C.ev(L) - C.ev(R), op)
                if S is None:
                    return None
                I = sp.Intersection(I, S)
            U = sp.Union(U, I)
        return U


def _expr(seg: str) -> sp.Expr:
    return C.parse(C.normalize(seg).text, evaluate=False)


def parse_relation(body: str) -> Optional[Rel]:
    """«2x − 3 > 5», «−1 < x ≤ 2», «x < 2 или x ≥ 3» → Rel; None — не неравенство.
    ParseError — неравенство, но части не разобрались."""
    s = _uni(body)
    if not _REL_SPLIT.search(s):
        return None
    disj = []
    for chunk in _OR.split(s):
        chunk = chunk.strip().strip(",;")
        if not chunk:
            continue
        toks = _REL_SPLIT.split(chunk)
        if len(toks) < 3:
            raise C.ParseError("неполное неравенство")
        exprs = [_expr(t) for t in toks[0::2]]
        ops = toks[1::2]
        disj.append([(exprs[i], ops[i], exprs[i + 1]) for i in range(len(ops))])
    if not disj:
        raise C.ParseError("пусто")
    return Rel(disj)


_INF = re.compile(r"^([+-]?)\s*(∞|oo|inf|infinity)$", re.I)
_PIECE = re.compile(r"\s*(?:([\(\[])([^()\[\]]*)([\)\]])|\{([^{}]*)\}|(ℝ|R)(?![A-Za-z])|(∅))\s*")
_UNION = re.compile(r"\s*(?:∪|⋃|U|u)\s*")


def _endpoint(t: str):
    t = t.strip()
    m = _INF.match(t.replace(" ", ""))
    if m:
        return -sp.oo if m.group(1) == "-" else sp.oo
    e = C.ev(C.parse(C.normalize(t).text, evaluate=False))
    if e.free_symbols or not e.is_real:
        raise C.ParseError("граница не число")
    return sp.nsimplify(e) if e.is_Float else e


def _split_inner(inner: str) -> Optional[tuple]:
    if ";" in inner:
        parts = inner.split(";")
        return (parts[0], parts[1]) if len(parts) == 2 else None
    commas = [i for i, ch in enumerate(inner) if ch == ","]
    if len(commas) == 1:
        i = commas[0]
    elif ", " in inner:
        i = inner.index(", ")
    elif len(commas) == 3:  # «(2,5,3,5)» — десятичные запятые с обеих сторон
        i = commas[1]
    else:
        return None
    a, b = inner[:i], inner[i + 1:]
    return a.replace(",", "."), b.replace(",", ".")


def parse_intervals(body: str):
    """Запись промежутков → множество SymPy; None — это не запись промежутков."""
    s = _uni(body).strip().strip(".")
    if not s:
        return None
    pos, pieces, need_piece = 0, [], True
    while pos < len(s):
        if not need_piece:
            m = _UNION.match(s, pos)
            if not m or m.end() == pos:
                return None
            pos, need_piece = m.end(), True
            continue
        m = _PIECE.match(s, pos)
        if not m:
            return None
        pos, need_piece = m.end(), False
        try:
            if m.group(1):
                ab = _split_inner(m.group(2))
                if ab is None:
                    return None
                a, b = (_endpoint(t) for t in ab)
                if (a - b).is_positive:
                    return None
                pieces.append(sp.Interval(a, b, m.group(1) == "(" or a == -sp.oo,
                                          m.group(3) == ")" or b == sp.oo))
            elif m.group(4) is not None:
                vals = [v for v in re.split(r"[;,]", m.group(4)) if v.strip()]
                pieces.append(sp.FiniteSet(*(_endpoint(v) for v in vals)))
            elif m.group(5):
                pieces.append(sp.S.Reals)
            else:
                pieces.append(sp.S.EmptySet)
        except C.ParseError:
            return None
    if need_piece or not pieces:
        return None
    return sp.Union(*pieces)


def _is_sign_chart(body: str) -> bool:
    toks = _uni(body).replace("|", " ").split()
    signs = [t for t in toks if t in ("+", "-")]
    rest = [t for t in toks if t not in ("+", "-")]
    return len(signs) >= 2 and all(re.fullmatch(r"-?[0-9.,/]+|[xх]", t) for t in rest)


def _is_factored(rel: Rel) -> bool:
    one = rel.single
    if not one:
        return False
    L, _, R = one
    for A, B in ((L, R), (R, L)):
        if C.ev(B) == 0:
            n, d = sp.fraction(sp.together(C.ev(A)))
            if d.has(VAR):
                return True
            if isinstance(A, sp.Mul) and sum(1 for f in A.args if C.ev(f).has(VAR)) >= 2:
                return True
    return False


def critical_points(d: sp.Expr) -> tuple:
    """Нули числителя и знаменателя d (после приведения к общему знаменателю)."""
    n, den = sp.fraction(sp.together(d))
    out = []
    for e in (n, den):
        vals = C.finite_vals(C.solve(e, VAR)) if e.has(VAR) else []
        out.append(vals or [])
    return out[0], out[1]


# --------------------------------------------------------------------------
# 3. Классификация ошибок
# --------------------------------------------------------------------------

@dataclass
class Item:
    """Строка, у которой есть множество решений: неравенство или промежутки."""
    rel: Optional[Rel]
    S: object


def _flipped(rel: Rel):
    """Варианты: сменить все знаки отношения или один из них."""
    allf = Rel([[(L, FLIP[op], R) for L, op, R in ch] for ch in rel.disj])
    yield allf
    if sum(len(ch) for ch in rel.disj) > 1:
        for ci, ch in enumerate(rel.disj):
            for ri in range(len(ch)):
                disj = [list(c) for c in rel.disj]
                L, op, R = disj[ci][ri]
                disj[ci][ri] = (L, FLIP[op], R)
                yield Rel(disj)


def _linear(rel: Optional[Rel]) -> bool:
    one = rel.single if rel else None
    if not one:
        return False
    d = C.ev(one[0]) - C.ev(one[2])
    try:
        return d.is_polynomial(VAR) and sp.Poly(d, VAR).degree() == 1
    except Exception:  # noqa: BLE001
        return False


def _div_var(prev: Rel, cur: Rel) -> Optional[dict]:
    p, c = prev.single, cur.single
    if not p or not c:
        return None
    pL, _, pR = p
    d_prev = C.ev(pL) - C.ev(pR)
    d_cur = C.ev(c[0]) - C.ev(c[2])
    if not d_cur.has(VAR):
        return None
    det = C._division_detail(pL, pR, d_prev, d_cur, VAR)
    if det:
        return det
    try:
        q = sp.cancel(sp.together(d_prev) / sp.together(d_cur))
    except Exception:  # noqa: BLE001
        return None
    if q.has(VAR) and not q.is_polynomial(VAR) and sp.cancel(1 / q).is_polynomial(VAR):
        # обе части умножены на знаменатель с x
        return {"f": C._fmt(sp.factor(sp.cancel(1 / q))), "mul": True}
    return None


def _calc_rel(cur: Rel, S_prev, skip) -> Optional[dict]:
    one = cur.single
    bps = breaks(S_prev)
    if not one or not bps:
        return None
    cL, op, cR = one
    found = []
    for side_i, E in enumerate((cL, cR)):
        for path, num in C._numeric_paths(E):
            try:
                Ek = C.ev(C._replace_at(E, path, C.K))
                other = C.ev(cR) if side_i == 0 else C.ev(cL)
                dk = (Ek - other) if side_i == 0 else (other - Ek)
                cands = sp.solve(sp.Eq(dk.subs(VAR, bps[0]), 0), C.K)
            except Exception:  # noqa: BLE001
                continue
            for kv in cands[:3]:
                if not kv.is_real or sp.simplify(kv - num) == 0:
                    continue
                if set_eq(solve_rel(dk.subs(C.K, kv), op), S_prev, skip):
                    found.append((C._calc_score(kv, path), num, kv))
    if not found:
        return None
    found.sort(key=lambda t: -t[0])
    _, num, kv = found[0]
    return {"num": C._fmt(num), "expected": C._fmt(kv)}


def classify(prev: Item, cur: Item, skip=(), domain=()) -> tuple:
    """Почему множество строки cur не совпало с prev. → (tag, detail)."""
    cmp = compare(cur.S, prev.S, skip)
    if cmp is None:
        return "other", {}
    # 1. знак неравенства не сменён при умножении / делении на отрицательное
    if cur.rel is not None:
        for v in _flipped(cur.rel):
            if set_eq(v.solve(), prev.S, skip):
                return "ineq_flip", {}
    if _linear(prev.rel) and cmp.opposite_regions:
        return "ineq_flip", {}
    # 2. деление (умножение) на выражение с x
    if prev.rel is not None and cur.rel is not None:
        det = _div_var(prev.rel, cur.rel)
        if det:
            return "ineq_div_var", det
    # 3. выбран не тот промежуток: всё, кроме концов, — дополнение верного
    if cmp.opposite_regions:
        return "interval_choice", {}
    # 4. граница: множества отличаются конечным числом точек
    if cmp.same_regions and cmp.diff_points:
        dom = [C._fmt(p) for p in cmp.diff_points if any(_near(p, q) for q in domain)]
        return "boundary", ({"domain": dom} if dom else {})
    p1 = prev.rel.single if prev.rel else None
    c1 = cur.rel.single if cur.rel else None
    # 5. ФСУ в предыдущей строке
    if p1 and c1:
        pL, pop, pR = p1
        for side_i, E in enumerate((pL, pR)):
            for node, wrong in C._fsu_variants(E):
                try:
                    E2 = C.ev(E.xreplace({node: wrong}))
                except Exception:  # noqa: BLE001
                    continue
                d = (E2 - C.ev(pR)) if side_i == 0 else (C.ev(pL) - E2)
                if set_eq(solve_rel(d, pop), cur.S, skip):
                    return "fsu", {"f": C._fmt(C.ev(node))}
    # 6. знак одного слагаемого
    if c1:
        cL, cop, cR = c1
        for (L2, R2), t in C._flip_variants(cL, cR):
            if set_eq(solve_rel(L2 - R2, cop), prev.S, skip):
                return "sign", {"term": C._fmt(C.ev(t))}
    if p1:
        pL, pop, pR = p1
        for (L2, R2), t in C._flip_variants(pL, pR):
            if set_eq(solve_rel(L2 - R2, pop), cur.S, skip):
                return "sign", {"term": C._fmt(C.ev(t))}
    # 7. одно неверное число
    if cur.rel is not None:
        det = _calc_rel(cur.rel, prev.S, skip)
        if det:
            return "calc", det
    off = one_boundary_off(cur.S, prev.S)
    if off:
        return "calc", {"num": C._fmt(off[0]), "expected": C._fmt(off[1])}
    return "other", {}


# --------------------------------------------------------------------------
# 4. Проверка решения
# --------------------------------------------------------------------------

def parses(raw: str) -> bool:
    """Разбирается ли строка разборщиками этого вида — для ocr.score_lines(kind="inequality").
    False — там, где check пометил бы строку «unparsed»; метки, строка знаков «+ − +»,
    промежутки «x ∈ [−2; 3]» и текст без «=» формулами с ошибкой не считаются."""
    nm = C.normalize(raw)
    body = _strip_label(raw)
    ub = _uni(body)
    if nm.label in ("domain", "rejected", "check") or nm.empty or _is_sign_chart(body) \
            or (_REL_SPLIT.search(ub) and re.search(r"(?<![<>])=", ub)):
        return True
    try:
        if parse_relation(body) is not None or parse_intervals(body) is not None or "=" not in nm.text:
            return True
        for p in C._split_parts(nm.text):
            for q in C._expand_pm(p):
                for seg in q.split("="):
                    C.parse(C._cmp_split(seg), evaluate=False)
    except C.ParseError:
        return False
    return True


def check(statement: str, raw_lines: list, reference_len: Optional[int] = None,
          reference_answer: Optional[str] = None) -> C.CheckResult:
    st_rel = parse_relation(_strip_label(statement))
    if st_rel is None:
        raise C.ParseError("условие — не неравенство")
    T = st_rel.solve()
    excl = []
    for chain in st_rel.disj:
        for L, _, R in chain:
            excl += C.domain_exclusions(C.ev(L) - C.ev(R), VAR)
    excl = _merge([sp.nsimplify(e) for e in excl]) if excl else []
    nonlinear = not _linear(st_rel)

    prev = Item(st_rel, T)
    prev_no, prev_raw = 0, statement
    crit_d = C.ev(st_rel.single[0]) - C.ev(st_rel.single[2]) if st_rel.single else None
    results: list = []
    first_error = None
    methods: set = set()
    habits: set = set()
    answer: Optional[tuple] = None   # (line_no, Item)
    last_item: Optional[tuple] = None
    n_solution = 0
    last_D = None

    def err(line, tag, detail, p_no, p_raw):
        nonlocal first_error
        line.status = "error"
        if first_error is None:
            first_error = {"line": line.no, "tag": tag, "detail": detail, "prev_line": p_no,
                           "evidence": f"{p_raw.strip()}  →  {line.raw.strip()}", **C._pnames(p_no)}

    def ok(line):
        line.status = "ok" if first_error is None else "after"

    def crit_ok(values: list) -> bool:
        if crit_d is None:
            return True
        N, D = critical_points(crit_d)
        return all(any(_near(v, c) for c in N + D) for v in values)

    for i, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            continue
        nm = C.normalize(raw)
        lr = C.LineResult(no=i, raw=raw, norm=nm.text)
        results.append(lr)
        body = _strip_label(raw)

        if nm.label == "domain":
            lr.kind, lr.status = "domain", "info"
            habits.add("domain_noted")
            continue
        if nm.label == "rejected":
            lr.kind, lr.status = "rejected", "info"
            continue
        if nm.label == "check":
            lr.kind, lr.status = "check", "info"
            habits.add("check_done")
            continue
        if _is_sign_chart(body):
            lr.kind, lr.status = "signs", "info"
            methods.add("interval_method")
            n_solution += 1
            continue

        ub = _uni(body)
        if _REL_SPLIT.search(ub) and re.search(r"(?<![<>])=", ub):
            # «x = 0: −1/2 < 0» — знак выражения в пробной точке промежутка
            lr.kind, lr.status = "signs", "info"
            methods.add("interval_method")
            continue

        try:
            rel = parse_relation(body)
            S = None
            if rel is not None:
                S = rel.solve()
                if S is None:
                    lr.kind, lr.status = "ineq", "info"
                    lr.note = "не удалось проверить строку"
                    continue
                lr.kind = "ineq"
                if _is_factored(rel):
                    methods.add("interval_method")
            elif nm.empty:
                S = sp.S.EmptySet
                lr.kind = "interval"
            else:
                S = parse_intervals(body)
                if S is not None:
                    lr.kind = "interval"

            if S is not None:
                item = Item(rel, S)
                if nm.label == "answer":
                    lr.kind = "answer"
                    answer = (i, item, raw)
                else:
                    n_solution += 1
                last_item = (i, item, raw)
                if set_eq(S, prev.S, excl):
                    ok(lr)
                elif first_error is None:
                    tag, det = classify(prev, item, excl, excl)
                    err(lr, tag, det, prev_no, prev_raw)
                else:
                    lr.status = "error"
                prev, prev_no, prev_raw = item, i, raw
                if rel is not None and rel.single:
                    crit_d = C.ev(rel.single[0]) - C.ev(rel.single[2])
                continue

            # --- критические точки: связанное уравнение, D, корни
            txt = nm.text
            if "=" not in txt:
                lr.kind, lr.status = "text", "info"
                continue
            parts = []
            for p in C._split_parts(txt):
                parts.extend(C._expand_pm(p))
            parsed = [[C._cmp_split(s) for s in p.split("=")] for p in parts]
            if len(parsed) == 1 and re.fullmatch(r"D(_1)?|D/4", parsed[0][0].replace(" ", "")):
                lr.kind = "disc"
                n_solution += 1
                vals, good = C._numeric_chain(parsed[0][1:], {})
                qc = C._quad_coeffs(crit_d, VAR) if crit_d is not None else None
                if good and vals and qc:
                    a, b, c = qc
                    good = any(abs(float(vals[-1]) - float(t)) < 1e-9 for t in (b**2 - 4 * a * c, (b / 2) ** 2 - a * c))
                if good:
                    ok(lr)
                else:
                    err(lr, "calc", {"num": C._fmt(vals[-1]), "where": "D"} if vals else {"where": "D"},
                        prev_no, prev_raw)
                if vals:
                    last_D = vals[-1]
                continue

            def is_root(segs) -> bool:
                if len(segs) < 2:
                    return False
                head = C.parse(segs[0], evaluate=False)
                if not (isinstance(head, sp.Symbol) and (head == VAR or str(head).startswith("x_"))):
                    return False
                return all(not (C.ev(C.parse(s, evaluate=False)).free_symbols - {sp.Symbol("D")}) for s in segs[1:])

            if parsed and all(is_root(s) for s in parsed):
                lr.kind = "root"
                methods.add("interval_method")
                n_solution += 1
                chain_ok = True
                for segs in parsed:
                    vals, good = C._numeric_chain(segs[1:], {sp.Symbol("D"): last_D} if last_D is not None else {})
                    chain_ok = chain_ok and good
                    if vals:
                        lr.values.append(float(vals[-1]))
                if chain_ok and crit_ok(lr.values):
                    ok(lr)
                else:
                    N, _ = critical_points(crit_d)
                    tag = "sign" if N and C.vals_eq(lr.values, [-v for v in N]) else "calc"
                    err(lr, tag, {}, prev_no, prev_raw)
                continue

            eqs = [(C.parse(s[-2], evaluate=False), C.parse(s[-1], evaluate=False)) for s in parsed if len(s) >= 2]
            if eqs and any((C.ev(L) - C.ev(R)).has(VAR) for L, R in eqs):
                lr.kind = "eq" if len(eqs) == 1 else "eqs"
                methods.add("interval_method")
                n_solution += 1
                d_eq = sp.Mul(*[C.ev(L) - C.ev(R) for L, R in eqs])
                vals = C.finite_vals(C.solve(d_eq, VAR))
                if vals is not None and crit_ok(vals):
                    ok(lr)
                elif first_error is None and crit_d is not None:
                    n, den = sp.fraction(sp.together(crit_d))
                    one = prev.rel.single if prev.rel else None
                    base = (one[0], one[2]) if one and not den.has(VAR) else (n, sp.Integer(0))
                    cur = eqs[0] if len(eqs) == 1 else (d_eq, sp.Integer(0))
                    tag, det = C.classify_eq_step(base, cur, [], VAR)
                    err(lr, tag, det, prev_no, prev_raw)
                else:
                    lr.status = "error"
                continue
            lr.kind, lr.status = "text", "info"
        except C.ParseError:
            lr.kind, lr.status = "unparsed", "unparsed"
            lr.note = "не удалось разобрать строку — исправьте распознавание"

    # итоговый ответ: строка «Ответ» или последняя строка с множеством решений
    fin = answer or last_item
    final, correct = None, None
    if fin is not None and T is not None:
        ans_no, item, ans_raw = fin
        final = [fmt_set(item.S)]
        correct = set_eq(item.S, T)
        if not correct and first_error is None:
            line = next(r for r in results if r.no == ans_no)
            tag, det = classify(Item(st_rel, T), item, (), excl)
            err(line, tag, det, 0, statement)

    if correct and reference_len and n_solution * 2 < reference_len:
        habits.add("skip_steps")

    return C.CheckResult(
        kind="inequality", lines=results, first_error=first_error,
        methods=sorted(methods) if nonlinear else [], habits=sorted(habits),
        final=final, true_answer=None, correct=correct, solution_lines=n_solution,
    )

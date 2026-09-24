"""Системы двух уравнений с x и y: линейные и «одно квадратное + одно линейное».

Условие — два уравнения через «;»: «x + y = 5; x − y = 1». Σ — множество
решений условия. Каждая строка-уравнение должна быть следствием системы
(выполняться во всех точках Σ), строка-система — равносильна условию,
значения «x = 3» — принадлежать проекции Σ. Первая строка, где это нарушено, —
место ошибки. Итоговый ответ сравнивается с Σ.

Виды строк: система в одной строке через «;» или двумя строками с «{», «⎧»,
«⎩»; одиночные уравнения; выражения «y = 5 − x»; значения «x = 3»;
ответ «(3; 2)», «x = 3, y = 2», «нет решений», «бесконечно много решений».
"""
from __future__ import annotations

import re
from typing import Optional

import sympy as sp

from .. import checker as C
from .inequality import _endpoint, _split_inner

X, Y = C.X, C.LOCALS["y"]
VARS = (X, Y)
_INDEXED = {C.LOCALS["x_1"]: X, C.LOCALS["x_2"]: X, C.LOCALS["y_1"]: Y, C.LOCALS["y_2"]: Y}
_BRACE_OPEN = "{⎧⎨"
_BRACE_ANY = "{⎧⎨⎩⎪|}"
KW_INFINITE = ("бесконечно много", "бесконечное множество", "любая пара", "шексіз көп", "шексіз", "infinitely")


# --------------------------------------------------------------------------
# 1. Множество решений и следствия
# --------------------------------------------------------------------------

def _eq(text: str) -> tuple:
    """«2x + y = 7» → (L, R) невычисленные; последняя пара частей цепочки."""
    segs = [C._cmp_split(s) for s in text.split("=")]
    if len(segs) < 2:
        raise C.ParseError("не уравнение")
    return C.parse(segs[-2], evaluate=False), C.parse(segs[-1], evaluate=False)


def _d(LR: tuple) -> sp.Expr:
    return C.ev(LR[0]) - C.ev(LR[1])


def solve_system(ds: list) -> Optional[list]:
    """Действительные решения системы d_i = 0: список словарей (возможно, с параметром)."""
    try:
        sols = sp.solve(ds, list(VARS), dict=True)
    except Exception:  # noqa: BLE001
        return None
    out = []
    for s in sols:
        if any(v.free_symbols == set() and v.is_real is False for v in s.values()):
            continue
        out.append(s)
    return out


def finite_points(sols: list) -> Optional[list]:
    """[(x, y), …] если решений конечно много, иначе None."""
    pts = []
    for s in sols:
        if set(s) != set(VARS) or any(v.free_symbols for v in s.values()):
            return None
        pts.append((s[X], s[Y]))
    return pts


def _zero(v) -> bool:
    try:
        if sp.simplify(v) == 0:
            return True
        return abs(complex(v)) < 1e-9
    except (TypeError, ValueError):
        return False


def holds(d: sp.Expr, sols: list, mode: str = "all") -> bool:
    """Выполняется ли d = 0 во всех (mode='all') или хотя бы в одной ('any') точке Σ."""
    if not sols:
        return mode == "all"
    res = [_zero(d.subs(s)) for s in sols]
    return all(res) if mode == "all" else any(res)


def same_eq(d1: sp.Expr, d2: sp.Expr) -> bool:
    """Одно и то же уравнение с точностью до множителя и тождественных преобразований."""
    try:
        e1, e2 = sp.expand(d1), sp.expand(d2)
        if sp.expand(e1 - e2) == 0:
            return True
        if e2 != 0:
            q = sp.cancel(e1 / e2)
            if q.is_number and q != 0:
                return True
    except Exception:  # noqa: BLE001
        return False
    fs = (d1.free_symbols | d2.free_symbols) & set(VARS)
    if len(fs) == 1:
        v = fs.pop()
        return C.set_equal(C.solve(d1, v), C.solve(d2, v))
    return False


def points_equal(a: list, b: list) -> bool:
    def close(p, q):
        return all(abs(complex(u) - complex(w)) < 1e-9 for u, w in zip(p, q))
    return len(a) == len(b) and all(any(close(p, q) for q in b) for p in a) and all(any(close(q, p) for p in a) for q in b)


def points_subset(a: list, b: list) -> bool:
    """a ⊂ b строго."""
    return len(a) < len(b) and all(any(abs(complex(p[0]) - complex(q[0])) < 1e-9 and
                                       abs(complex(p[1]) - complex(q[1])) < 1e-9 for q in b) for p in a)


def fmt_points(pts) -> str:
    if pts == "inf":
        return "бесконечно много решений"
    if not pts:
        return "∅"
    return ", ".join(f"({C._fmt(a)}; {C._fmt(b)})" for a, b in pts)


# --------------------------------------------------------------------------
# 2. Разбор строк
# --------------------------------------------------------------------------

def _var_of(head) -> Optional[sp.Symbol]:
    if head in VARS:
        return head
    return _INDEXED.get(head)


def _value_part(part: str) -> Optional[tuple]:
    """«x₁ = (5 + 1)/2 = 3» → (x, 3, chain_ok); иначе None."""
    segs = [C._cmp_split(s) for s in part.split("=")]
    if len(segs) < 2:
        return None
    try:
        head = C.parse(segs[0], evaluate=False)
    except C.ParseError:
        return None
    v = _var_of(head) if isinstance(head, sp.Symbol) else None
    if v is None:
        return None
    vals, ok = C._numeric_chain(segs[1:], {})
    if not vals or len(vals) != len(segs) - 1:
        return None
    return v, head, vals[-1], ok


def parse_pairs(body: str) -> Optional[list]:
    """«(3; 2)», «(3; 4), (4; 3)» → [(3, 2), …]; None — не пары."""
    s = body.replace("−", "-").replace("–", "-")
    found = list(re.finditer(r"\(([^()]*)\)", s))
    if not found:
        return None
    rest = re.sub(r"\(([^()]*)\)", " ", s)
    if re.sub(r"[\s,;.иandжәне]", "", rest):
        return None
    pts = []
    for m in found:
        ab = _split_inner(m.group(1))
        if ab is None:
            return None
        try:
            pts.append(tuple(_endpoint(t) for t in ab))
        except C.ParseError:
            return None
    return pts


def _strip_label(raw: str) -> str:
    return re.sub(rf"^[\s{C.CYR}.]+:", " ", raw.strip()).strip()


# --------------------------------------------------------------------------
# 3. Классификация ошибок
# --------------------------------------------------------------------------

def _subst_variants(E: sp.Expr):
    """Подстановка без скобок: сменён знак одного слагаемого подставляемого выражения."""
    ts = C._terms(E)
    if len(ts) < 2:
        return
    for t in ts:
        yield E - 2 * t


def _coef_variants(G: sp.Expr, v: sp.Symbol, E: sp.Expr):
    """«3y» при y = 5 − x → «3·5 − x»: коэффициент умножен только на одно слагаемое."""
    ts = C._terms(E)
    if len(ts) < 2:
        return
    for gt in C._terms(sp.expand(G)):
        c = sp.cancel(gt / v)
        if c.has(v) or c.free_symbols or c in (1, -1) or not gt.has(v):
            continue
        for t in ts:
            yield sp.expand(G) - gt + c * t + (E - t)


def classify(d_cur: sp.Expr, LR: tuple, sols: list, ctx: dict, mode: str = "all") -> tuple:
    """Почему строка d_cur = 0 не следует из системы. → (tag, detail)."""
    # 1. подстановка без скобок — пока подстановка ещё не выполнена верно
    for v, E, _ in ([] if ctx["subst_done"] else ctx["subst"]):
        for G in ctx["eqs"]:
            if not G.has(v):
                continue
            cands = [G.subs(v, E2) for E2 in _subst_variants(E)] + list(_coef_variants(G, v, E))
            if any(same_eq(d_cur, c) for c in cands):
                return "subst", {"expr": C._fmt(E), "var": str(v)}
    # 2. ФСУ в предыдущей строке-уравнении
    prev = ctx.get("prev_eq")
    if prev is not None:
        pL, pR = prev
        for side_i, Eside in enumerate((pL, pR)):
            for node, wrong in C._fsu_variants(Eside):
                try:
                    E2 = C.ev(Eside.xreplace({node: wrong}))
                except Exception:  # noqa: BLE001
                    continue
                d2 = (E2 - C.ev(pR)) if side_i == 0 else (C.ev(pL) - E2)
                if same_eq(d2, d_cur):
                    return "fsu", {"f": C._fmt(C.ev(node))}
    # 3. знак одного слагаемого
    cL, cR = LR
    for (L2, R2), t in C._flip_variants(cL, cR):
        if not _zero(L2 - R2 - d_cur) and holds(L2 - R2, sols, mode):
            return "sign", {"term": C._fmt(C.ev(t))}
    # 4. одно неверное число
    pts = finite_points(sols) if sols else None
    if pts:
        p0 = {X: pts[0][0], Y: pts[0][1]}
        found = []
        for side_i, Eside in enumerate((cL, cR)):
            for path, num in C._numeric_paths(Eside):
                try:
                    Ek = C.ev(C._replace_at(Eside, path, C.K))
                    other = C.ev(cR) if side_i == 0 else C.ev(cL)
                    dk = (Ek - other) if side_i == 0 else (other - Ek)
                    cands = sp.solve(sp.Eq(dk.subs(p0), 0), C.K)
                except Exception:  # noqa: BLE001
                    continue
                for kv in cands[:3]:
                    if not kv.is_real or sp.simplify(kv - num) == 0:
                        continue
                    if holds(dk.subs(C.K, kv), sols, mode):
                        found.append((C._calc_score(kv, path), num, kv))
        if found:
            found.sort(key=lambda t: -t[0])
            _, num, kv = found[0]
            return "calc", {"num": C._fmt(num), "expected": C._fmt(kv)}
    return "other", {}


def _addition(d: sp.Expr, d1: sp.Expr, d2: sp.Expr) -> bool:
    """d = c₁·E₁ + c₂·E₂ с c₁, c₂ ≠ 0."""
    c1, c2 = sp.symbols("c1_ c2_")
    try:
        P = sp.Poly(sp.expand(d - c1 * d1 - c2 * d2), *VARS)
        sol = sp.solve(P.coeffs(), [c1, c2], dict=True)
    except Exception:  # noqa: BLE001
        return False
    return len(sol) == 1 and all(sol[0].get(c, 0) != 0 and not sol[0][c].free_symbols for c in (c1, c2))


def _vars_in(d: sp.Expr) -> set:
    return d.free_symbols & set(VARS)


# --------------------------------------------------------------------------
# 4. Проверка решения
# --------------------------------------------------------------------------

def parses(raw: str) -> bool:
    """Разбирается ли строка разборщиками этого вида — для ocr.score_lines(kind="system").
    False — там, где check пометил бы строку «unparsed»; пары «(3; 2)», строки системы
    с фигурной скобкой «⎧ … / ⎩ …» и ответ словами формулами с ошибкой не считаются."""
    nm = C.normalize(re.sub(rf"^\s*[{_BRACE_ANY}]\s*", "", raw))
    txt = nm.text
    if nm.label in ("check", "domain", "rejected") or nm.empty or not txt or any(k in raw.lower() for k in KW_INFINITE) \
            or parse_pairs(_strip_label(raw)) is not None:
        return True
    try:  # те же ветки, что в check: значения → строка системы → одиночное уравнение
        eq_parts = [p for p in txt.split(";") if "=" in p]
        parts = [q for p in C._split_parts(txt) for q in C._expand_pm(p)]
        if parts and all(_value_part(p) for p in parts):
            return True
        if (len(eq_parts) == 2 and ";" in txt) or raw.lstrip()[:1] in _BRACE_OPEN:
            for p in eq_parts or [txt]:
                _eq(p)
            return True
        for p in parts:
            if "=" in p:
                _eq(p)
    except C.ParseError:
        return False
    return True


def check(statement: str, raw_lines: list, reference_len: Optional[int] = None,
          reference_answer: Optional[str] = None) -> C.CheckResult:
    st_parts = [p for p in C.normalize(statement).text.split(";") if p.strip()]
    if len(st_parts) != 2:
        raise C.ParseError("условие системы — два уравнения через «;»")
    st_LR = [_eq(p) for p in st_parts]
    ds = [_d(lr) for lr in st_LR]
    sols = solve_system(ds)
    if sols is None:
        raise C.ParseError("не удалось решить систему")
    pts = finite_points(sols)
    truth = pts if pts is not None else "inf"

    results: list = []
    first_error = None
    ctx = {"subst": [], "eqs": list(ds), "prev_eq": None, "subst_done": False}
    ref_sols = sols           # после первой ошибки — решения «ошибочной» системы
    methods: set = set()
    habits: set = set()
    seen_subst_src = False
    single_var_seen = False
    addition_seen = False
    answer = None             # (line_no, points | "inf")
    point_line = None
    values = {X: [], Y: []}
    n_solution = 0
    pending = None            # первая строка системы «⎧ …»

    def err(line, tag, detail):
        nonlocal first_error
        line.status = "error"
        if first_error is None:
            first_error = {"line": line.no, "tag": tag, "detail": detail, "prev_line": 0,
                           "evidence": f"{statement.strip()}  →  {line.raw.strip()}", **C._pnames(0)}

    def after_error(d_err, LR_err):
        """Решения, относительно которых проверяются строки после ошибки."""
        nonlocal ref_sols
        partner = ctx["subst"][-1][2] if ctx["subst"] else None
        if partner is None:
            partner = next((d for d in ds if _vars_in(d) - _vars_in(d_err)), ds[0])
        s2 = solve_system([d_err, partner]) if _vars_in(d_err) else None
        ref_sols = s2 if s2 else None

    def status_for(ok_main: bool, d=None, mode="all"):
        if first_error is None:
            return "ok" if ok_main else None
        if ok_main:
            return "ok"
        if ref_sols is None or d is None:
            return "after"
        return "after" if holds(d, ref_sols, mode) else "error"

    def check_eq(lr, LR, d, mode="all"):
        """Одно уравнение: следствие системы?"""
        good = holds(d, sols, mode)
        stt = status_for(good, d, mode)
        if stt is not None:
            lr.status = stt
            return
        tag, det = classify(d, LR, sols, ctx, mode)
        err(lr, tag, det)
        if mode == "all":
            after_error(d, LR)

    lines = [(i, raw) for i, raw in enumerate(raw_lines, start=1) if raw.strip()]
    for i, raw in lines:
        opens = raw.lstrip()[:1] in _BRACE_OPEN
        nm = C.normalize(re.sub(rf"^\s*[{_BRACE_ANY}]\s*", "", raw))
        lr = C.LineResult(no=i, raw=raw, norm=nm.text)
        results.append(lr)
        low = raw.lower()
        txt = nm.text

        if nm.label == "check":
            lr.kind, lr.status = "check", "info"
            habits.add("check_done")
            continue
        if nm.label == "domain" or nm.label == "rejected":
            lr.kind, lr.status = nm.label, "info"
            continue

        # --- ответ словами
        if any(k in low for k in KW_INFINITE):
            lr.kind = "answer" if nm.label == "answer" else "text"
            answer = (i, "inf")
            lr.status = "ok" if first_error is None else "after"
            continue
        if nm.empty:
            lr.kind = "answer" if nm.label == "answer" else "text"
            answer = (i, [])
            lr.status = "ok" if first_error is None else "after"
            continue

        body = _strip_label(raw)
        pairs = parse_pairs(body)
        if pairs is not None:
            lr.kind = "answer" if nm.label == "answer" else "point"
            answer = (i, pairs) if nm.label == "answer" else answer
            if nm.label != "answer":
                point_line = (i, pairs)
            lr.status = "ok" if first_error is None else "after"
            continue
        if not txt:
            lr.kind, lr.status = "text", "info"
            continue

        try:
            # --- строка системы: «a; b» или две строки с «⎧ … / ⎩ …»
            eq_parts = [p for p in txt.split(";") if "=" in p]
            parts = []
            for p in C._split_parts(txt):
                parts.extend(C._expand_pm(p))
            vparts = [_value_part(p) for p in parts]

            if parts and all(vparts):  # значения «x = 3», «x₁ = 3, x₂ = 4», точки «x = 3, y = 2»
                lr.kind = "root" if nm.label != "answer" else "answer"
                n_solution += nm.label != "answer"
                vs = {vp[0] for vp in vparts}
                chain_ok = all(vp[3] for vp in vparts)
                if vs == {X, Y} and len(parts) in (2, 4):
                    # «x = 3, y = 2» или «x₁ = 3, y₁ = 4, x₂ = 4, y₂ = 3» — точки
                    by_idx: dict = {}
                    for vp in vparts:
                        idx = str(vp[1])[-1] if str(vp[1])[-1] in "12" else "0"
                        by_idx.setdefault(idx, {})[vp[0]] = vp[2]
                    ps = [(d[X], d[Y]) for d in by_idx.values() if X in d and Y in d]
                    if nm.label == "answer":
                        answer = (i, ps)
                    else:
                        point_line = (i, ps)
                    if not chain_ok:
                        if first_error is None:
                            err(lr, "calc", {})
                        else:
                            lr.status = "error"
                    else:
                        lr.status = "ok" if first_error is None else "after"
                    continue
                if len(vs) == 1:
                    v = vs.pop()
                    new_vals = [vp[2] for vp in vparts]
                    values[v].extend(new_vals)
                    single_var_seen = True
                    d = sp.Mul(*[v - val for val in new_vals])
                    if not chain_ok:
                        if first_error is None:
                            err(lr, "calc", {})
                        else:
                            lr.status = "error"
                        continue
                    if len(new_vals) == 1 and len(parts) == 1:
                        check_eq(lr, _eq(parts[0]), d, "any")
                    else:
                        good = all(holds(v - val, sols, "any") for val in new_vals)
                        stt = status_for(good, d, "all")
                        if stt is not None:
                            lr.status = stt
                        else:
                            N = [p[0 if v == X else 1] for p in (pts or [])]
                            tag = "sign" if N and all(any(_zero(val + n) for n in N) for val in new_vals) else "calc"
                            err(lr, tag, {})
                    continue

            if (len(eq_parts) == 2 and ";" in txt) or opens or pending is not None:
                if opens and len(eq_parts) < 2 and pending is None:
                    pending = (lr, _eq(txt))
                    lr.kind, lr.status = "system", "info"
                    n_solution += 1
                    continue
                if pending is not None:
                    comp = [pending, (lr, _eq(eq_parts[0] if eq_parts else txt))]
                    pending = None
                else:
                    comp = [(lr, _eq(p)) for p in eq_parts]
                lr.kind = "system"
                n_solution += 1
                sys_ds = [_d(LR) for _, LR in comp]
                bad = None
                for (ln, LR), d in zip(comp, sys_ds):
                    ln.kind = "system"
                    if not holds(d, sols) and bad is None:
                        bad = (ln, LR, d)
                    if isinstance(LR[0], sp.Symbol) and _var_of(LR[0]) and _vars_in(C.ev(LR[1])) - {LR[0]}:
                        ctx["subst"].append((LR[0], C.ev(LR[1]), d))
                        seen_subst_src = True
                ctx["eqs"] = list(ds) + sys_ds
                if seen_subst_src and any(len(_vars_in(d)) == 1 for d in sys_ds):
                    methods.add("substitution")
                if bad is not None:
                    ln, LR, d = bad
                    if first_error is None:
                        tag, det = classify(d, LR, sols, ctx)
                        err(ln, tag, det)
                        after_error(d, LR)
                    else:
                        ln.status = "error"
                    for other, _ in comp:
                        if other is not ln and other.status == "info":
                            other.status = "ok" if first_error is None else "after"
                    continue
                s2 = solve_system(sys_ds)
                p2 = finite_points(s2) if s2 is not None else None
                equiv = (p2 is not None and pts is not None and points_equal(p2, pts)) or \
                        (p2 is None and pts is None and s2 is not None and all(holds(d0, s2) for d0 in ds))
                for ln, _ in comp:
                    ln.status = "ok" if first_error is None else "after"
                if not equiv and first_error is None:
                    err(comp[-1][0], "other", {})
                continue

            # --- одиночное уравнение (или «x = 0 или x = 5»)
            eqs = [_eq(p) for p in parts if "=" in p]
            if not eqs:
                lr.kind, lr.status = "text", "info"
                continue
            d = sp.Mul(*[_d(LR) for LR in eqs])
            vs = _vars_in(d)
            if not vs:
                lr.kind, lr.status = "numeric", "info"
                continue
            lr.kind = "eq"
            n_solution += 1
            LR = eqs[0] if len(eqs) == 1 else (d, sp.Integer(0))
            if len(eqs) == 1 and isinstance(LR[0], sp.Symbol) and _var_of(LR[0]) and _vars_in(C.ev(LR[1])) - {LR[0]}:
                ctx["subst"].append((LR[0], C.ev(LR[1]), d))
                seen_subst_src = True
            elif len(vs) == 1:
                if seen_subst_src:
                    methods.add("substitution")
                elif not single_var_seen and _addition(d, ds[0], ds[1]):
                    addition_seen = True
                single_var_seen = True
            check_eq(lr, LR, d)
            if len(vs) == 1 and ctx["subst"] and lr.status == "ok":
                ctx["subst_done"] = True
            ctx["prev_eq"] = LR if len(eqs) == 1 else None
        except C.ParseError:
            lr.kind, lr.status = "unparsed", "unparsed"
            lr.note = "не удалось разобрать строку — исправьте распознавание"

    if pending is not None:  # незакрытая «⎧ …» — проверяем как одиночное уравнение
        ln, LR = pending
        ln.kind = "eq"
        if holds(_d(LR), sols):
            ln.status = "ok" if first_error is None else "after"
        elif first_error is None:
            tag, det = classify(_d(LR), LR, sols, ctx)
            err(ln, tag, det)
        else:
            ln.status = "error"
    if addition_seen and "substitution" not in methods:
        methods.add("addition")

    # итоговый ответ: «Ответ», строка с точками или по одному значению x и y
    fin = answer or point_line
    if fin is None and len(values[X]) == 1 and len(values[Y]) == 1:
        last = max(r.no for r in results if r.kind == "root")
        fin = (last, [(values[X][0], values[Y][0])])
    final, correct = None, None
    if fin is not None:
        ans_no, got = fin
        final = [fmt_points(got)]
        if got == "inf" or truth == "inf":
            correct = got == truth
        else:
            correct = points_equal(got, truth)
        line = next(r for r in results if r.no == ans_no)
        if not correct:
            if first_error is None:
                if got == "inf" or truth == "inf":
                    tag = "other"
                elif points_equal([(b, a) for a, b in got], truth):
                    tag = "swap_xy"
                elif points_subset(got, truth):
                    tag = "lost_root"
                elif points_subset(truth, got):
                    tag = "extra_root"
                else:
                    tag = "calc"
                err(line, tag, {})
            elif line.status == "after" and ref_sols is not None and got != "inf":
                rp = finite_points(ref_sols)
                if rp is not None and not points_equal(got, rp):
                    line.status = "error"

    if correct and reference_len and n_solution * 2 < reference_len:
        habits.add("skip_steps")

    return C.CheckResult(
        kind="system", lines=results, first_error=first_error, methods=sorted(methods),
        habits=sorted(habits), final=final, true_answer=None, correct=correct, solution_lines=n_solution,
    )

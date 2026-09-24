"""Биквадратные уравнения ax⁴ + bx² + c = 0 с заменой переменной t = x².

Порядок проверки:
  1. строки в x до замены — `checker.check_equation` по условию;
  2. первое уравнение в t после подстановки t = x² должно совпасть с текущим
     уравнением в x как многочлен (с точностью до множителя), иначе — `subst`;
  3. строки в t — `checker.check_equation` (переменную функция определит сама);
  4. обратная замена («x² = 1 или x² = 4») и итоговые корни сравниваются
     с найденными t и с решением условия.

Ошибки обратной замены: забыт ± → `lost_root` с `detail.pm`; корни из x² = t
при t < 0 → `neg_t`; потерянные корни → `lost_root`.
Без строки замены решение целиком проверяется как обычное уравнение.
"""
from __future__ import annotations

import re
from typing import Optional

import sympy as sp

from .. import checker as C

X = C.X
_SUB_IDX = {"₁": "1", "₂": "2", "₃": "3", "₄": "4"}


def _pre(raw: str, tv: Optional[str]) -> str:
    """Индексы корней до нормализации: x₃, x3,4, t₁, t1,2 → x_3, x, t_1, t."""
    s = raw
    for a, b in _SUB_IDX.items():
        s = re.sub(rf"(?<![A-Za-z])([xх{tv or ''}])\s*{a}", rf"\g<1>{b}", s)
    s = re.sub(r"(?<![A-Za-z])x\s*_?\s*3\s*,\s*4(?![0-9])", "x", s)
    s = re.sub(r"(?<![A-Za-z])x\s*_?\s*([34])(?![0-9])", r"x_\1", s)
    if tv:
        s = re.sub(rf"(?<![A-Za-z]){tv}\s*_?\s*1\s*,\s*2(?![0-9])", tv, s)
        s = re.sub(rf"(?<![A-Za-z]){tv}\s*_?\s*([12])(?![0-9])", rf"{tv}_\1", s)
    return s


_SUBST_RE = (re.compile(r"(?<![A-Za-z^_])([a-wzA-Z])\s*=\s*x\s*\^\s*2(?![0-9])"),
             re.compile(r"(?<![A-Za-z])x\s*\^\s*2\s*=\s*([a-wzA-Z])(?![A-Za-z0-9^(])"))


def _subst_var(text: str) -> Optional[str]:
    """«t = x²», «пусть x² = t» → «t»."""
    for rx in _SUBST_RE:
        m = rx.search(text)
        if m and m.group(1) not in ("D",):
            rest = rx.sub(" ", text, count=1)
            if "=" not in re.sub(r"[<>≥≤]=?\s*0", "", rest):
                return m.group(1)
    return None


def _eq_d(text: str) -> Optional[sp.Expr]:
    segs = [C._cmp_split(s) for s in text.split("=")]
    if len(segs) < 2:
        return None
    try:
        return C.ev(C.parse(segs[-2], evaluate=False)) - C.ev(C.parse(segs[-1], evaluate=False))
    except C.ParseError:
        return None


def _has(sym: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z]){sym}(?![A-Za-z])", text) is not None


def _in(v, vals, tol=1e-9) -> bool:
    return any(abs(float(v) - float(u)) < tol * (1 + abs(float(u))) for u in vals)


def _segment(raw_lines: list, keep: set) -> list:
    """Строки только из keep (номера сохраняются: остальное — пустые строки)."""
    return [raw if i in keep else "" for i, raw in enumerate(raw_lines, start=1)]


def check(statement: str, raw_lines: list, reference_len: Optional[int] = None,
          reference_answer: Optional[str] = None) -> C.CheckResult:
    st_d = _eq_d(C.normalize(statement).text)
    if st_d is None:
        raise C.ParseError("условие — не уравнение")
    true_x = C.finite_vals(C.solve(st_d, X)) or []

    # --- где замена
    sub_no, tv = None, None
    for i, raw in enumerate(raw_lines, start=1):
        if raw.strip():
            v = _subst_var(C.normalize(_pre(raw, None)).text)
            if v:
                sub_no, tv = i, v
                break
    if sub_no is None:
        r = C.check_equation(statement, [_pre(l, None) for l in raw_lines], reference_len)
        for lr, raw in zip(r.lines, [raw_lines[lr.no - 1] for lr in r.lines]):
            lr.raw = raw
        r.kind = "biquadratic"
        return r

    T = sp.Symbol(tv)
    pre = [_pre(l, tv) for l in raw_lines]
    nonblank = [i for i, l in enumerate(raw_lines, start=1) if l.strip()]
    before = {i for i in nonblank if i < sub_no}
    first_t, t_seg, t_rej, back = None, set(), set(), set()
    for i in nonblank:
        if i <= sub_no:
            continue
        nm = C.normalize(pre[i - 1])
        txt = nm.text
        if first_t is None:
            if _has(tv, txt) and "=" in txt and not _has("x", txt):
                first_t = i
                continue
            back.add(i)
            continue
        if back or nm.label == "answer" or (_has("x", txt) and not _has(tv, txt)):
            back.add(i)
        elif nm.label == "rejected":
            t_rej.add(i)   # «t = −3 не подходит»: отбросить отрицательное t — верный шаг
        else:
            t_seg.add(i)

    results: dict = {}
    first_error: Optional[dict] = None
    methods: set = set()
    habits: set = set()
    n_solution = 0

    def take(res: C.CheckResult, fix_prev: Optional[tuple] = None):
        """Строки сегмента → общий результат; первая ошибка — только самая ранняя."""
        nonlocal first_error, n_solution
        habits.update(h for h in res.habits if h != "skip_steps")
        n_solution += res.solution_lines
        fe = res.first_error
        if fe and fix_prev and fe["prev_line"] == 0:
            no, praw = fix_prev
            fe = dict(fe, prev_line=no, evidence=f"{praw.strip()}  →  {raw_lines[fe['line'] - 1].strip()}",
                      **C._pnames(no))
        for lr in res.lines:
            lr.raw = raw_lines[lr.no - 1]
            if first_error is not None and lr.status == "error" and fe and lr.no == fe["line"]:
                lr.note = (lr.note + " ещё одна ошибка").strip()
            results[lr.no] = lr
        if fe and first_error is None:
            first_error = fe

    def err(lr, tag, detail, p_no, p_raw):
        nonlocal first_error
        lr.status = "error"
        if first_error is None:
            first_error = {"line": lr.no, "tag": tag, "detail": detail, "prev_line": p_no,
                           "evidence": f"{p_raw.strip()}  →  {lr.raw.strip()}", **C._pnames(p_no)}

    # 1. строки в x до замены
    cur_d, cur_no, cur_raw = st_d, 0, statement
    if before:
        r1 = C.check_equation(statement, _segment(pre, before))
        take(r1)
        for lr in r1.lines:
            if lr.kind == "eq":
                d = _eq_d(lr.norm)
                if d is not None:
                    cur_d, cur_no, cur_raw = d, lr.no, raw_lines[lr.no - 1]

    # 2. строка замены и первое уравнение в t
    lr = C.LineResult(no=sub_no, raw=raw_lines[sub_no - 1], norm=C.normalize(pre[sub_no - 1]).text,
                      kind="subst", status="info")
    results[sub_no] = lr
    methods.add("var_change")
    t_vals: list = []
    if first_t is not None:
        n_solution += 1
        d_t = _eq_d(C.normalize(pre[first_t - 1]).text)
        lr_t = C.LineResult(no=first_t, raw=raw_lines[first_t - 1], norm=C.normalize(pre[first_t - 1]).text, kind="eq")
        results[first_t] = lr_t
        if d_t is None:
            lr_t.kind, lr_t.status = "unparsed", "unparsed"
            lr_t.note = "не удалось разобрать строку — исправьте распознавание"
        else:
            back_x = sp.expand(d_t.subs(T, X**2))
            q = sp.cancel(sp.together(cur_d) / back_x) if back_x != 0 else sp.nan
            if q.is_number and q != 0 and q.is_finite:
                lr_t.status = "ok" if first_error is None else "after"
            else:
                err(lr_t, "subst", {"t": tv, "change": True}, cur_no, cur_raw)
            t_all = C.finite_vals(C.solve(d_t, T)) or []
            # 3. строки в t
            if t_seg:
                r3 = C.check_equation(pre[first_t - 1], _segment(pre, t_seg))
                take(r3, fix_prev=(first_t, raw_lines[first_t - 1]))
                methods.update(r3.methods)
                for x in r3.lines:
                    if x.kind == "root":
                        t_vals.extend(x.values)
            for i in sorted(t_rej):
                rl = C.LineResult(no=i, raw=raw_lines[i - 1], norm=C.normalize(pre[i - 1]).text,
                                  kind="rejected", status="info")
                rl.values = C._parse_values(rl.norm)
                results[i] = rl
            t_vals = t_vals or t_all
            t_known = t_vals + [v for v in t_all if not _in(v, t_vals)]
    else:
        t_known = []

    # 4. обратная замена и корни
    final_vals: Optional[list] = None
    rejected: list = []
    roots: list = []
    ans_no = None
    good_x = [r for r in true_x]

    def x_ok(v) -> str:
        """ok | neg_t | wrong_t | calc для одного корня в x."""
        if _in(v, good_x):
            return "ok"
        sq = float(v) ** 2
        if any(t < 0 and abs(sq + t) < 1e-9 * (1 + abs(t)) for t in t_known):
            return "neg_t"
        if _in(sq, [t for t in t_known if t >= 0]):
            return "wrong_t"   # согласовано с неверно найденным t
        return "calc"

    for i in sorted(back):
        raw = pre[i - 1]
        nm = C.normalize(raw)
        lr = C.LineResult(no=i, raw=raw_lines[i - 1], norm=nm.text)
        results[i] = lr
        txt = nm.text
        if nm.label == "check":
            lr.kind, lr.status = "check", "info"
            habits.add("check_done")
            continue
        if nm.label == "rejected":
            lr.kind, lr.status = "rejected", "info"
            lr.values = C._parse_values(txt)
            rejected.extend(lr.values)
            continue
        if nm.label == "answer":
            lr.kind = "answer"
            lr.values = [] if nm.empty else C._parse_values(txt)
            final_vals, ans_no = lr.values, i
            lr.status = "ok" if first_error is None else "after"
            continue
        if not txt or "=" not in txt:
            lr.kind, lr.status = "text", "info"
            continue
        n_solution += 1
        try:
            parts = []
            for p in C._split_parts(txt):
                parts.extend(C._expand_pm(p))
            bad: Optional[str] = None
            kinds = set()
            for p in parts:
                segs = [C._cmp_split(s) for s in p.split("=")]
                head = C.parse(segs[0], evaluate=False)
                is_root = isinstance(head, sp.Symbol) and (head == X or str(head).startswith("x_")) and all(
                    not C.ev(C.parse(s, evaluate=False)).free_symbols for s in segs[1:])
                if is_root:
                    kinds.add("root")
                    vals, chain_ok = C._numeric_chain(segs[1:], {})
                    if not vals:
                        continue
                    v = float(vals[-1])
                    lr.values.append(v)
                    st = x_ok(v) if chain_ok else "calc"
                    if st != "ok" and bad is None:
                        bad = st
                    continue
                kinds.add("eq")
                d = _eq_d(p)
                if d is None or not d.has(X):
                    continue
                P = sp.Poly(sp.expand(sp.numer(sp.together(d))), X) if d.is_polynomial(X) else None
                if P is not None and P.degree() == 2 and P.coeff_monomial(X) == 0:
                    c = -P.coeff_monomial(1) / P.coeff_monomial(X**2)
                    if not _in(c, t_known) and bad is None:
                        bad = "calc"
                else:
                    sols = C.finite_vals(C.solve(d, X)) or []
                    if any(not _in(float(s) ** 2, t_known) for s in sols) and bad is None:
                        bad = "calc"
            lr.kind = "root" if kinds == {"root"} else "eqs" if len(parts) > 1 else "eq"
            roots.extend(lr.values)
            if bad is None:
                lr.status = "ok" if first_error is None else "after"
            elif bad == "wrong_t" and first_error is not None:
                lr.status = "after"
            elif first_error is None:
                err(lr, "neg_t" if bad == "neg_t" else "calc", {"t": tv} if bad == "neg_t" else {},
                    first_t or sub_no, raw_lines[(first_t or sub_no) - 1])
            else:
                lr.status = "error"
        except C.ParseError:
            lr.kind, lr.status = "unparsed", "unparsed"
            lr.note = "не удалось разобрать строку — исправьте распознавание"

    # итоговые корни
    if final_vals is None and roots:
        final_vals = [v for v in roots if not _in(v, rejected)] if rejected else roots
        ans_no = max(n for n, r in results.items() if r.kind in ("root", "eq", "eqs") and n in back)
    final, correct = None, None
    if final_vals is not None:
        uniq: list = []
        for v in final_vals:
            if not _in(v, uniq):
                uniq.append(v)
        final = uniq
        correct = C.vals_eq(final, true_x)
        if not correct and first_error is None:
            missing = [v for v in true_x if not _in(v, final)]
            extra = [v for v in final if not _in(v, true_x)]
            line = results[ans_no]
            if extra and all(x_ok(v) == "neg_t" for v in extra):
                tag, det = "neg_t", {"t": tv}
            elif missing and not extra:
                tag = "lost_root"
                det = {"pm": True} if all(_in(-v, final) for v in missing) else {}
            elif extra and not missing:
                tag, det = "extra_root", {}
            else:
                tag, det = "calc", {}
            err(line, tag, det, 0, statement)

    if correct and reference_len and n_solution * 2 < reference_len:
        habits.add("skip_steps")

    lines = [results[k] for k in sorted(results)]
    return C.CheckResult(
        kind="biquadratic", lines=lines, first_error=first_error, methods=sorted(methods),
        habits=sorted(habits), final=final, true_answer=true_x, correct=correct, solution_lines=n_solution,
    )

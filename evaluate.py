"""Оценка точности на реальных работах — две цифры для слайда.

1. Доля верно распознанных строк (фото → строки, нужна языковая модель).
2. Доля верно найденных первых ошибок (строка и тег), отдельно:
   — на эталонных строках (качество проверки SymPy),
   — на распознанных строках (весь конвейер целиком).

Разметка: eval/labels.csv (UTF-8), по строке на задачу:
    photo            — путь к фото относительно папки eval/ (можно пусто: тогда только проверка)
    kind             — equation | expression
    statement        — условие, например  x² = 5x
    lines            — эталонные строки решения через « | » (как написано в тетради)
    error_line       — номер первой неверной строки (0, если решение верное)
    error_tag        — тег ошибки (lost_root, extra_root, sign, fsu, calc, other) или пусто

Запуск:
    python evaluate.py                    # только проверка на эталонных строках
    ANTHROPIC_API_KEY=... python evaluate.py --ocr   # плюс распознавание фото
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from core import llm
from core.checker import check_problem, normalize

ROOT = Path(__file__).parent / "eval"


def norm_line(s: str) -> str:
    return normalize(s).text.replace(" ", "").lower()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(ROOT / "labels.csv"))
    ap.add_argument("--ocr", action="store_true", help="распознавать фото через языковую модель")
    args = ap.parse_args()
    cfg = llm.config_from_env()
    if args.ocr and not cfg.ready:
        sys.exit("Нужен ключ: ANTHROPIC_API_KEY или OPENAI_API_KEY (см. README)")

    rows = list(csv.DictReader(open(args.labels, encoding="utf-8-sig")))
    n_lines = ok_lines = 0
    n_err = ok_err_line = ok_err_tag = 0
    n_e2e = ok_e2e = 0
    for i, r in enumerate(rows, 1):
        truth = [l.strip() for l in r["lines"].split("|") if l.strip()]
        want_line = int(r.get("error_line") or 0)
        want_tag = (r.get("error_tag") or "").strip()
        res = check_problem(r["kind"], r["statement"], truth, None)
        got_line = res.first_error["line"] if res.first_error else 0
        got_tag = res.first_error["tag"] if res.first_error else ""
        n_err += 1
        ok_err_line += got_line == want_line
        ok_err_tag += got_line == want_line and (not want_tag or got_tag == want_tag)
        mark = "✓" if got_line == want_line else "✗"
        print(f"{i:>3} {mark} {r['statement']:<32} эталон: {want_line}/{want_tag or '-':<10} проверка: {got_line}/{got_tag or '-'}")

        if args.ocr and r.get("photo"):
            data = (ROOT / r["photo"]).read_bytes()
            rec = llm.recognize(cfg, data, [(1, r["statement"])]).get(1, [])
            for k, t in enumerate(truth):
                n_lines += 1
                ok_lines += k < len(rec) and norm_line(rec[k]) == norm_line(t)
            res2 = check_problem(r["kind"], r["statement"], rec, None)
            got2 = res2.first_error["line"] if res2.first_error else 0
            n_e2e += 1
            ok_e2e += got2 == want_line
            print(f"      распознано {len(rec)} строк из {len(truth)}; первая ошибка по распознанному: {got2}")

    print("\n=== Итог ===")
    print(f"Первая ошибка (строка) на эталонных строках: {ok_err_line}/{n_err} = {ok_err_line / max(n_err, 1):.0%}")
    print(f"Первая ошибка (строка и тег):               {ok_err_tag}/{n_err} = {ok_err_tag / max(n_err, 1):.0%}")
    if args.ocr:
        print(f"Верно распознанных строк:                   {ok_lines}/{n_lines} = {ok_lines / max(n_lines, 1):.0%}")
        print(f"Первая ошибка по распознанному (весь путь): {ok_e2e}/{n_e2e} = {ok_e2e / max(n_e2e, 1):.0%}")


if __name__ == "__main__":
    main()

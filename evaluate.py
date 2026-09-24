"""Оценка точности на реальных работах — цифры для слайда.

1. Распознавание (фото → строки, нужна языковая модель):
   — доля строк, совпавших с эталоном после нормализации, без ручной правки (цель G1 ≥ 90 %);
   — CER — доля посимвольных ошибок по нормализованным строкам;
   — флаги «требует проверки»: сколько строк помечено и сколько из них действительно
     ошибочны; какая доля ошибочных строк попала под флаг (калибровка ocr.UNSURE);
     строки оцениваются с учётом вида задачи: ocr.score_lines(lines, kind);
   — время распознавания одного фото.
2. Первая ошибка (строка и тег):
   — на эталонных строках (качество проверки SymPy; quality.reference_accuracy — та же функция,
     что у страницы «Качество»),
   — на распознанных строках (весь путь целиком, цель G2 ≥ 80 %).

Разметка: eval/labels.csv (UTF-8), по строке на задачу; подробно — eval/README.md:
    photo            — путь к фото относительно папки eval/ (можно пусто: тогда только проверка)
    problem          — номер задачи на фото (необязательно, по умолчанию 1): для фото с несколькими задачами
    kind             — equation | expression | inequality | system | biquadratic (core/kinds, названия — tags.KIND_NAMES)
    statement        — условие, например  x² = 5x;  у системы уравнения через «;»:  2x + y = 7; x − y = 2
    lines            — эталонные строки решения через « | » (как написано в тетради)
    error_line       — номер первой неверной строки (0, если решение верное)
    error_tag        — тег ошибки из tags.TAGS (kind == "error") или пусто: lost_root, extra_root, sign, fsu,
                       calc, other, ineq_flip, ineq_div_var, interval_choice, boundary, subst, swap_xy,
                       neg_t, cancel_terms
    status           — необязательно; draft — черновик из --draft, человек ещё не проверил эталон;
                       черновики пропускаются в точности и считаются

Запуск:
    python evaluate.py                                   # только проверка на эталонных строках
    ANTHROPIC_API_KEY=... python evaluate.py --ocr       # плюс распознавание фото
    python evaluate.py --ocr --passes 2                  # два прочтения
    python evaluate.py --ocr --out eval/results.md       # таблица для слайда
    python evaluate.py --draft photos/07.jpg --statement "x² = 5x" [--kind equation] [--problem 1]
    --labels ПУТЬ — другой файл разметки (по умолчанию eval/labels.csv)
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

from core import llm, ocr, quality
from core.checker import check_problem, normalize
from core.kinds import KINDS

ROOT = Path(__file__).parent / "eval"
FIELDS = ["photo", "problem", "kind", "statement", "lines", "error_line", "error_tag", "status"]
THRESHOLDS = (0.35, 0.45, 0.55, 0.8, 0.96)


def norm_line(s: str) -> str:
    return normalize(s).text.replace(" ", "").lower()


def levenshtein(a: str, b: str) -> int:
    """Расстояние редактирования (вставка, удаление, замена — по 1)."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(truth: list, rec: list) -> tuple[int, int]:
    """Посимвольные ошибки по нормализованным строкам, строки сопоставлены по порядку.
    Лишняя строка распознавания — вставки, пропущенная — удаления. → (ошибки, символов в эталоне)"""
    errs = chars = 0
    for k in range(max(len(truth), len(rec))):
        t = norm_line(truth[k]) if k < len(truth) else ""
        r = norm_line(rec[k]) if k < len(rec) else ""
        errs += levenshtein(t, r)
        chars += len(t)
    return errs, chars


def calibration(pairs: list, thresholds=THRESHOLDS) -> list:
    """pairs: [(confidence, строка ошибочна)] → [(порог, полнота, ложные тревоги, помечено)].
    Полнота — доля ошибочных строк, попавших в «требуют проверки»; ложные тревоги — доля
    помеченных строк, которые распознаны верно."""
    wrong = sum(w for _, w in pairs)
    out = []
    for th in thresholds:
        flagged = [(c, w) for c, w in pairs if c < th]
        hit = sum(w for _, w in flagged)
        out.append((th, hit / wrong if wrong else 1.0, (len(flagged) - hit) / len(flagged) if flagged else 0.0,
                    len(flagged)))
    return out


def read_labels(path) -> tuple[list, list]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return list(rd), list(rd.fieldnames or [])


def recognize_photo(cfg, photo: str, rows: list, passes: int) -> tuple[dict, float]:
    """Одно фото → {номер задачи: [строки с confidence и flags]}, секунды."""
    data = (ROOT / photo).read_bytes()
    problems = sorted({(int(r.get("problem") or 1), r["statement"]) for r in rows})
    kinds = {int(r.get("problem") or 1): r.get("kind") for r in rows}
    t0 = time.perf_counter()
    det = llm.recognize_detailed(cfg, data, problems, passes=passes)
    secs = time.perf_counter() - t0
    out = {}
    for idx, lines in det.items():
        out[idx] = ocr.score_lines([{**l, "text": ocr.clean_text(l["text"])} for l in lines], kinds.get(idx))
    return out, secs


def draft(args, cfg) -> None:
    photo = Path(args.draft)
    try:
        rel = str(photo.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        rel = str(photo)
    src = photo if photo.exists() else ROOT / photo
    det = llm.recognize_detailed(cfg, src.read_bytes(), [(args.problem, args.statement)], passes=args.passes)
    lines = [ocr.clean_text(l["text"]) for l in det.get(args.problem, [])]
    res = check_problem(args.kind, args.statement, lines, None)
    fe = res.first_error
    rows, fields = read_labels(args.labels) if Path(args.labels).exists() else ([], [])
    fields = fields + [f for f in FIELDS if f not in fields]
    rows.append({"photo": rel, "problem": str(args.problem), "kind": args.kind, "statement": args.statement,
                 "lines": " | ".join(lines), "error_line": str(fe["line"] if fe else 0),
                 "error_tag": fe["tag"] if fe else "", "status": "draft"})
    with open(args.labels, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) or "" for k in fields})
    print(f"Черновик добавлен в {args.labels}: {len(lines)} строк распознано.")
    for k, l in enumerate(lines, 1):
        print(f"  {k}. {l}")
    print("Проверьте эталон по фото: строки ровно как в тетради, error_line, error_tag — и уберите пометку draft.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(ROOT / "labels.csv"))
    ap.add_argument("--ocr", action="store_true", help="распознавать фото через языковую модель")
    ap.add_argument("--passes", type=int, default=1, choices=(1, 2), help="прочтений на фото")
    ap.add_argument("--out", help="записать таблицу результатов в markdown (например, eval/results.md)")
    ap.add_argument("--draft", metavar="PHOTO", help="распознать фото и дописать черновую строку в labels.csv")
    ap.add_argument("--statement", help="условие задачи для --draft")
    ap.add_argument("--kind", default="equation", choices=("equation", "expression", *KINDS))
    ap.add_argument("--problem", type=int, default=1, help="номер задачи на фото для --draft")
    args = ap.parse_args()
    cfg = llm.config_from_env()
    if (args.ocr or args.draft) and not cfg.ready:
        sys.exit("Нужен ключ: ANTHROPIC_API_KEY или OPENAI_API_KEY (см. README)")
    if args.draft:
        if not args.statement:
            sys.exit("Для --draft нужно --statement \"условие\"")
        draft(args, cfg)
        return

    all_rows, _ = read_labels(args.labels)
    ref = quality.reference_accuracy(all_rows)  # та же функция, что у страницы «Качество»: черновики пропущены
    rows, n_draft = [x["label"] for x in ref["rows"]], ref["n_draft"]

    by_photo: dict = {}
    times: list = []
    if args.ocr:
        for r in rows:
            if r.get("photo"):
                by_photo.setdefault(r["photo"], []).append(r)
        for photo, prs in by_photo.items():
            try:
                rec, secs = recognize_photo(cfg, photo, prs, args.passes)
            except (llm.LLMError, OSError) as ex:
                print(f"  ! {photo}: {ex}")
                rec, secs = None, None
            by_photo[photo] = rec
            if secs is not None:
                times.append(secs)

    n_lines = ok_lines = 0
    cer_err = cer_chars = 0
    conf_pairs: list = []
    n_err, ok_err_line, ok_err_tag = ref["n"], ref["ok_line"], ref["ok_tag"]
    n_e2e = ok_e2e = ok_e2e_tag = 0
    for i, x in enumerate(ref["rows"], 1):
        r, truth = x["label"], x["truth"]
        want_line, want_tag, got_line, got_tag = x["want_line"], x["want_tag"], x["got_line"], x["got_tag"]
        mark = "✓" if x["ok_line"] else "✗"
        print(f"{i:>3} {mark} {r['statement']:<32} эталон: {want_line}/{want_tag or '-':<10} проверка: {got_line}/{got_tag or '-'}")

        if args.ocr and r.get("photo") and by_photo.get(r["photo"]) is not None:
            scored = by_photo[r["photo"]].get(int(r.get("problem") or 1), [])
            rec = [l["text"] for l in scored]
            wrong_here = 0
            for k in range(max(len(truth), len(rec))):
                ok = k < len(truth) and k < len(rec) and norm_line(rec[k]) == norm_line(truth[k])
                if k < len(truth):
                    n_lines += 1
                    ok_lines += ok
                if k < len(rec):  # у каждой распознанной строки есть уверенность
                    conf_pairs.append((scored[k]["confidence"], not ok))
                wrong_here += not ok
            e, c = cer(truth, rec)
            cer_err, cer_chars = cer_err + e, cer_chars + c
            res2 = check_problem(r["kind"], r["statement"], rec, None)
            got2 = res2.first_error["line"] if res2.first_error else 0
            tag2 = res2.first_error["tag"] if res2.first_error else ""
            n_e2e += 1
            ok_e2e += got2 == want_line
            ok_e2e_tag += got2 == want_line and (not want_tag or tag2 == want_tag)
            n_flag = sum(ocr.needs_review(l) for l in scored)
            print(f"      распознано {len(rec)} строк из {len(truth)}, неверных {wrong_here}, помечено ⚠️ {n_flag}; "
                  f"первая ошибка по распознанному: {got2}/{tag2 or '-'}")

    pct = lambda a, b: f"{a}/{b} = {a / max(b, 1):.0%}"  # noqa: E731
    flagged = [(c, w) for c, w in conf_pairs if c < ocr.UNSURE]
    flagged_wrong = sum(w for _, w in flagged)
    total_wrong = sum(w for _, w in conf_pairs)
    summary = [
        ("Задач в разметке (без черновиков)", str(len(rows))),
        ("Первая ошибка (строка) на эталонных строках", pct(ok_err_line, n_err)),
        ("Первая ошибка (строка и тег) на эталонных строках", pct(ok_err_tag, n_err)),
    ]
    if args.ocr:
        n_photos = sum(v is not None for v in by_photo.values())
        summary += [
            ("Фото распознано", str(n_photos)),
            ("Строк в эталоне", str(n_lines)),
            ("Строк верно без правки (G1, цель ≥ 90 %)", pct(ok_lines, n_lines)),
            ("CER по нормализованным строкам", f"{cer_err}/{cer_chars} = {cer_err / max(cer_chars, 1):.1%}"),
            ("Помечено «требует проверки»", f"{len(flagged)} строк, из них действительно ошибочны {flagged_wrong}"),
            ("Ошибочных строк под флагом (полнота)", pct(flagged_wrong, total_wrong)),
            ("Первая ошибка по распознанному (G2, цель ≥ 80 %)", pct(ok_e2e, n_e2e)),
            ("Первая ошибка и тег по распознанному", pct(ok_e2e_tag, n_e2e)),
            ("Время на одно фото", f"{sum(times) / len(times):.1f} с (медиана {sorted(times)[len(times) // 2]:.1f} с)"
                                   if times else "—"),
        ]
    print("\n=== Итог ===")
    for k, v in summary:
        print(f"{k:<52} {v}")
    if n_draft:
        print(f"(пропущено черновиков: {n_draft} — проверьте эталон и уберите пометку draft)")
    calib = calibration(conf_pairs) if conf_pairs else []
    if calib:
        print("\nПорог UNSURE → полнота → ложные тревоги (помечено строк)")
        for th, rc, fa, n in calib:
            print(f"  {th:.2f} → {rc:.0%} → {fa:.0%} ({n})" + ("   ← текущий" if abs(th - ocr.UNSURE) < 1e-9 else ""))

    if args.out:
        md = [f"# Точность распознавания и проверки — {date.today():%d.%m.%Y}", "",
              f"Модель: {cfg.label() if args.ocr else '— (только проверка на эталонных строках)'}"
              + (f", прочтений: {args.passes}" if args.ocr else ""),
              "Данные: реальные работы из `eval/labels.csv` (без черновиков).", "",
              "| Метрика | Значение |", "|---|---|"]
        md += [f"| {k} | {v} |" for k, v in summary]
        if calib:
            md += ["", f"Калибровка порога «требует проверки» (сейчас UNSURE = {ocr.UNSURE}):", "",
                   "| Порог | Полнота | Ложные тревоги | Помечено строк |", "|---|---|---|---|"]
            md += [f"| {th:.2f} | {rc:.0%} | {fa:.0%} | {n} |" for th, rc, fa, n in calib]
        Path(args.out).write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"\nТаблица записана: {args.out}")


if __name__ == "__main__":
    main()

"""Качество выводов: цифры для слайда — только из настоящих отметок и замеров.

1. Точность каждого тега по отметкам учителя (верно / отмечено), частые замены,
   статус «требует проверки» (точность < 80 % при ≥ 5 отметках).
2. Точность проверки на размеченных работах eval/labels.csv — та же цифра,
   что даёт `python evaluate.py` (без распознавания фото).
3. Время проверки в приложении: от «Проверить» до «Записать в журнал»;
   сравнение с ручной проверкой — только если учитель ввёл свою цифру.
"""
from __future__ import annotations

import csv
import io
import statistics
from collections import Counter
from pathlib import Path
from typing import Optional

from . import db, review, tags as T
from .checker import check_problem

PRECISION_MIN = 0.8     # ниже — «требует проверки»
REVIEWS_MIN = 5         # … но только если отметок хватает
LABELS = Path(__file__).resolve().parent.parent / "eval" / "labels.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS check_timings (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER,
    student_id INTEGER,
    seconds REAL NOT NULL,
    n_problems INTEGER,
    n_lines INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quality_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def ensure_schema(conn) -> None:
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)


# ---------------------------------------------------------------- 1. точность тегов

def _reviewed(conn, source: str) -> list:
    """Наблюдения источника source с последней отметкой: [(observation, review)]."""
    review.ensure_schema(conn)
    rows = db.q(conn, """SELECT o.*, r.verdict, r.new_tag, r.comment AS review_comment, r.reviewer,
                                r.created_at AS reviewed_at
                         FROM observations o JOIN reviews r ON r.observation_id = o.id
                         WHERE o.source=? AND r.id = (SELECT MAX(id) FROM reviews WHERE observation_id = o.id)""",
                (source,))
    return [dict(r) for r in rows]


def tag_precision(conn, source: str = "auto") -> list[dict]:
    """По каждому тегу с отметками: отмечено, верно, неверно, другой тег, точность, частая замена, статус."""
    totals = Counter(r["tag"] for r in db.q(conn, "SELECT tag FROM observations WHERE source=?", (source,)))
    by_tag: dict = {}
    for r in _reviewed(conn, source):
        by_tag.setdefault(r["tag"], []).append(r)
    out = []
    for tag, items in by_tag.items():
        c = Counter(r["verdict"] for r in items)
        n = len(items)
        repl = Counter(r["new_tag"] for r in items if r["verdict"] == "retag").most_common(1)
        prec = c["confirm"] / n
        out.append({
            "tag": tag, "kind": T.TAGS.get(tag, {}).get("kind", ""), "observations": totals.get(tag, 0),
            "reviewed": n, "confirm": c["confirm"], "reject": c["reject"], "retag": c["retag"],
            "precision": prec, "top_replacement": repl[0][0] if repl else None,
            "top_replacement_n": repl[0][1] if repl else 0,
            "needs_review": n >= REVIEWS_MIN and prec < PRECISION_MIN,
        })
    out.sort(key=lambda d: (-d["needs_review"], -d["reviewed"], d["tag"]))
    return out


def tag_status(conn, tag: str, source: str = "auto") -> Optional[dict]:
    return next((d for d in tag_precision(conn, source) if d["tag"] == tag), None)


def overall_precision(rows: list[dict]) -> Optional[dict]:
    n = sum(d["reviewed"] for d in rows)
    if not n:
        return None
    ok = sum(d["confirm"] for d in rows)
    return {"reviewed": n, "confirm": ok, "precision": ok / n}


# ---------------------------------------------------------------- кандидаты на исправление правил

def rejected_cases(conn, tags: Optional[list] = None, source: str = "auto") -> list[dict]:
    """Отклонённые и перетегированные наблюдения с доказательствами — материал для доработки правил."""
    rows = [r for r in _reviewed(conn, source) if r["verdict"] in ("reject", "retag")
            and (tags is None or r["tag"] in tags)]
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    ctx = {r["id"]: dict(r) for r in db.q(conn, f"""
        SELECT o.id, a.number AS work_no, p.idx AS problem_idx, p.statement, st.alias
        FROM observations o JOIN students st ON st.id = o.student_id
        LEFT JOIN submissions s ON s.id = o.submission_id
        LEFT JOIN assignments a ON a.id = s.assignment_id
        LEFT JOIN problems p ON p.id = o.problem_id
        WHERE o.id IN ({','.join('?' * len(ids))})""", ids)}
    out = [dict(r, **ctx.get(r["id"], {})) for r in rows]
    out.sort(key=lambda r: (r["tag"], r["reviewed_at"]))
    return out


def candidates_markdown(conn, lang: str = "ru", tags: Optional[list] = None) -> str:
    """Markdown: по тегу — отклонённые случаи (работа, задача, строка, цитата, комментарий учителя)."""
    ru = lang != "kk"
    cases = rejected_cases(conn, tags)
    lines = ["# " + ("Кандидаты на исправление правил" if ru else "Ережелерді түзетуге үміткерлер"), ""]
    if not cases:
        lines.append("Отклонённых выводов нет." if ru else "Қабылданбаған қорытынды жоқ.")
        return "\n".join(lines) + "\n"
    stats = {d["tag"]: d for d in tag_precision(conn)}
    for tag in sorted({c["tag"] for c in cases}):
        d = stats.get(tag)
        head = f"## {T.name(tag, 'ru' if ru else 'kk')} (`{tag}`)"
        if d:
            head += (f" — {'точность' if ru else 'дәлдік'} {round(d['precision'] * 100)} %, "
                     f"{d['reviewed']} {'отметок' if ru else 'белгі'}")
        lines += [head, ""]
        for c in (c for c in cases if c["tag"] == tag):
            where = " · ".join(x for x in [
                f"{'ДЗ' if ru else 'ҮТ'} №{c['work_no']}" if c.get("work_no") else "",
                f"{'задача' if ru else 'есеп'} {c['problem_idx']}" if c.get("problem_idx") else "",
                f"{'строка' if ru else 'жол'} {c['line_no']}" if c.get("line_no") else ""] if x)
            verdict = ("неверно" if ru else "дұрыс емес") if c["verdict"] == "reject" else \
                f"{'другой тег' if ru else 'басқа тег'} → `{c['new_tag']}`"
            lines.append(f"- **{verdict}** · {where} · {c.get('alias', '')}")
            if c.get("statement"):
                lines.append(f"  - {'условие' if ru else 'шарт'}: `{c['statement']}`")
            lines.append(f"  - {'доказательство' if ru else 'дәлел'}: `{c['evidence']}`")
            if c.get("review_comment"):
                lines.append(f"  - {'комментарий учителя' if ru else 'мұғалім пікірі'}: {c['review_comment']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 2. точность на размеченных работах

def eval_accuracy(path: str | Path = LABELS) -> dict:
    """Прогон check_problem по эталонным строкам eval/labels.csv — как `python evaluate.py` без --ocr."""
    path = Path(path)
    if not path.is_absolute() and not path.exists():
        path = LABELS.parent.parent / path
    if not path.exists():
        return {"available": False, "path": str(path), "n": 0, "ok_line": 0, "ok_tag": 0, "rows": []}
    with open(path, encoding="utf-8-sig", newline="") as f:
        labels = list(csv.DictReader(f))
    rows = []
    for r in labels:
        truth = [l.strip() for l in (r.get("lines") or "").split("|") if l.strip()]
        want_line = int(r.get("error_line") or 0)
        want_tag = (r.get("error_tag") or "").strip()
        res = check_problem(r["kind"], r["statement"], truth, None)
        got_line = res.first_error["line"] if res.first_error else 0
        got_tag = res.first_error["tag"] if res.first_error else ""
        ok_line = got_line == want_line
        rows.append({"statement": r["statement"], "kind": r["kind"], "want_line": want_line, "want_tag": want_tag,
                     "got_line": got_line, "got_tag": got_tag, "ok_line": ok_line,
                     "ok_tag": ok_line and (not want_tag or got_tag == want_tag)})
    n = len(rows)
    return {"available": True, "path": str(path), "n": n,
            "ok_line": sum(r["ok_line"] for r in rows), "ok_tag": sum(r["ok_tag"] for r in rows), "rows": rows}


# ---------------------------------------------------------------- 3. время проверки

def record_timing(conn, submission_id: Optional[int], student_id: Optional[int], seconds: float,
                  n_problems: Optional[int] = None, n_lines: Optional[int] = None) -> int:
    ensure_schema(conn)
    if seconds is None or seconds < 0:
        raise ValueError("время проверки должно быть неотрицательным")
    cur = conn.execute(
        """INSERT INTO check_timings (submission_id, student_id, seconds, n_problems, n_lines, created_at)
           VALUES (?,?,?,?,?,?)""", (submission_id, student_id, float(seconds), n_problems, n_lines, db.now()))
    conn.commit()
    return cur.lastrowid


def get_setting(conn, key: str) -> Optional[str]:
    ensure_schema(conn)
    r = db.q1(conn, "SELECT value FROM quality_settings WHERE key=?", (key,))
    return r["value"] if r else None


def set_setting(conn, key: str, value: Optional[str]) -> None:
    ensure_schema(conn)
    if value is None or str(value).strip() == "":
        conn.execute("DELETE FROM quality_settings WHERE key=?", (key,))
    else:
        conn.execute("INSERT OR REPLACE INTO quality_settings (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()


def manual_minutes(conn) -> Optional[float]:
    v = get_setting(conn, "manual_minutes_per_work")
    try:
        return float(v) if v is not None and float(v) > 0 else None
    except ValueError:
        return None


def set_manual_minutes(conn, minutes: Optional[float]) -> None:
    if minutes is not None and minutes <= 0:
        raise ValueError("минуты должны быть больше нуля")
    set_setting(conn, "manual_minutes_per_work", None if minutes is None else f"{float(minutes):g}")


def timing_summary(conn) -> dict:
    """Медиана секунд на работу и число работ; экономия — только если учитель ввёл время ручной проверки."""
    ensure_schema(conn)
    secs = [r["seconds"] for r in db.q(conn, "SELECT seconds FROM check_timings")]
    med = statistics.median(secs) if secs else None
    manual = manual_minutes(conn)
    saved = speedup = None
    if manual is not None and med is not None:
        saved = manual - med / 60
        speedup = manual * 60 / med if med > 0 else None
    return {"n": len(secs), "median_seconds": med, "manual_minutes": manual,
            "saved_minutes_per_work": saved, "speedup": speedup}


# ---------------------------------------------------------------- выгрузка для слайда

def metrics_csv(conn) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["metric", "tag", "value", "n"])
    for d in tag_precision(conn):
        w.writerow(["tag_precision", d["tag"], f"{d['precision']:.3f}", d["reviewed"]])
    ov = overall_precision(tag_precision(conn))
    if ov:
        w.writerow(["overall_precision", "", f"{ov['precision']:.3f}", ov["reviewed"]])
    ev = eval_accuracy()
    if ev["available"] and ev["n"]:
        w.writerow(["eval_first_error_line", "", f"{ev['ok_line'] / ev['n']:.3f}", ev["n"]])
        w.writerow(["eval_first_error_line_tag", "", f"{ev['ok_tag'] / ev['n']:.3f}", ev["n"]])
    t = timing_summary(conn)
    if t["median_seconds"] is not None:
        w.writerow(["check_median_seconds", "", f"{t['median_seconds']:.1f}", t["n"]])
    if t["manual_minutes"] is not None:
        w.writerow(["manual_minutes_per_work", "", f"{t['manual_minutes']:g}", ""])
    rs = review.rating_summary(conn)
    if rs["mean"] is not None:
        w.writerow(["portrait_similarity_mean", "", f"{rs['mean']:.2f}", rs["n_students"]])
    return buf.getvalue()


def metrics_markdown(conn, lang: str = "ru") -> str:
    ru = lang != "kk"
    L = (lambda a, b: a) if ru else (lambda a, b: b)
    out = ["# " + L("Качество выводов — цифры для слайда", "Қорытындылар сапасы — слайдқа арналған сандар"), ""]
    rows = tag_precision(conn)
    ov = overall_precision(rows)
    out.append("## " + L("Точность правил по отметкам учителя", "Мұғалім белгілері бойынша ережелер дәлдігі"))
    if ov:
        out.append(L(f"Всего отмечено {ov['reviewed']} выводов, верных {ov['confirm']} — {round(ov['precision'] * 100)} %.",
                     f"Барлығы {ov['reviewed']} қорытынды белгіленді, дұрысы {ov['confirm']} — {round(ov['precision'] * 100)} %."))
        out += ["", "| " + L("Тег | Отмечено | Верно | Неверно | Другой тег | Точность | Статус",
                              "Тег | Белгіленді | Дұрыс | Дұрыс емес | Басқа тег | Дәлдік | Күйі") + " |",
                "|---|---|---|---|---|---|---|"]
        for d in rows:
            st = L("требует проверки", "тексеруді қажет етеді") if d["needs_review"] else ""
            out.append(f"| {T.name(d['tag'], lang)} | {d['reviewed']} | {d['confirm']} | {d['reject']} | {d['retag']} | "
                       f"{round(d['precision'] * 100)} % | {st} |")
    else:
        out.append(L("Отметок пока нет.", "Әзірге белгі жоқ."))
    out.append("")
    ev = eval_accuracy()
    out.append("## " + L("Точность на размеченных работах", "Белгіленген жұмыстардағы дәлдік"))
    if ev["available"] and ev["n"]:
        out.append(L(f"Первая ошибка (строка): {ev['ok_line']}/{ev['n']} = {round(ev['ok_line'] / ev['n'] * 100)} %; "
                     f"строка и тег: {ev['ok_tag']}/{ev['n']} = {round(ev['ok_tag'] / ev['n'] * 100)} %.",
                     f"Алғашқы қате (жол): {ev['ok_line']}/{ev['n']} = {round(ev['ok_line'] / ev['n'] * 100)} %; "
                     f"жол және тег: {ev['ok_tag']}/{ev['n']} = {round(ev['ok_tag'] / ev['n'] * 100)} %."))
    else:
        out.append(L("Нет размеченных работ (eval/labels.csv).", "Белгіленген жұмыс жоқ (eval/labels.csv)."))
    out.append("")
    t = timing_summary(conn)
    out.append("## " + L("Время проверки", "Тексеру уақыты"))
    if t["median_seconds"] is not None:
        line = L(f"Медиана {t['median_seconds']:.0f} с на работу ({t['n']} работ)",
                 f"Бір жұмысқа медиана {t['median_seconds']:.0f} с ({t['n']} жұмыс)")
        if t["manual_minutes"] is not None:
            line += L(f" против {t['manual_minutes']:g} мин вручную (по опросу)",
                      f", қолмен — {t['manual_minutes']:g} мин (сауалнама бойынша)")
        out.append(line + ".")
    else:
        out.append(L("Замеров пока нет.", "Әзірге өлшеу жоқ."))
    rs = review.rating_summary(conn)
    if rs["mean"] is not None:
        out += ["", "## " + L("Похожесть портрета", "Портреттің ұқсастығы"),
                L(f"Средняя оценка {rs['mean']:.1f} из 5 ({rs['n_students']} учеников, {rs['n']} оценок).",
                  f"Орташа баға 5-тен {rs['mean']:.1f} ({rs['n_students']} оқушы, {rs['n']} баға).")]
    return "\n".join(out) + "\n"

"""Сборка портрета ученика и карты класса — только правила по журналу.

1. Где слаб: взвешенная доля работ с ошибкой (свежие весят больше, w = 0.8^k);
   слабое место — если случаев ≥ 3 и доля ≥ 40 %.
2. Как решает: доли методов по квадратным уравнениям и частота привычек.
3. Что говорит учитель: теги из комментариев с цитатами; совпадение с
   автопроверкой — «подтверждено дважды».
4. Как работать: готовые советы из словаря тегов, привязанные к работам.
5. У каждого пункта — ссылки на работу и строку; < 3 наблюдений — «мало данных».
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from . import db, tags as T

DECAY = 0.8
MIN_CASES = 3
MIN_SHARE = 0.4


# ---------------------------------------------------------------- текст

def works_word(n: int, lang: str) -> str:
    if lang == "kk":
        return "жұмыста"
    return "работе" if n % 10 == 1 and n % 100 != 11 else "работах"


def share_text(count: int, total: int, lang: str) -> str:
    if lang == "kk":
        return f"{count}/{total} жұмыста"
    return f"в {count} {works_word(count, lang)} из {total}"


def comments_text(n: int, lang: str) -> str:
    if lang == "kk":
        return f"{n} пікірде"
    return f"в {n} комментари{'и' if n % 10 == 1 and n % 100 != 11 else 'ях'}"


DETAIL_TEXT = {
    "division": {"ru": "при делении обеих частей на выражение с x",
                 "kk": "екі жағын x-і бар өрнекке бөлгенде"},
    "D": {"ru": "в вычислении дискриминанта", "kk": "дискриминантты есептеуде"},
    "vieta": {"ru": "в теореме Виета", "kk": "Виет теоремасында"},
    "domain": {"ru": "корни вне ОДЗ остаются в ответе", "kk": "ММЖ-дан тыс түбірлер жауапта қалады"},
}


# ---------------------------------------------------------------- данные

def _works(conn, sid: int) -> list:
    """Работы ученика, новые первыми, с k — «сколько работ назад»."""
    rows = db.q(conn, """SELECT s.*, a.number, a.title FROM submissions s
                         JOIN assignments a ON a.id = s.assignment_id
                         WHERE s.student_id=? ORDER BY s.submitted_at DESC""", (sid,))
    return [dict(r, k=i) for i, r in enumerate(rows)]


def _skills_per_work(conn, sid: int) -> dict:
    rows = db.q(conn, """SELECT DISTINCT at.submission_id, p.skill FROM attempts at
                         JOIN problems p ON p.id = at.problem_id
                         JOIN submissions s ON s.id = at.submission_id
                         WHERE s.student_id=?""", (sid,))
    out = defaultdict(set)
    for r in rows:
        out[r["skill"]].add(r["submission_id"])
    return out


def _obs(conn, sid: int) -> list:
    return [dict(r) for r in db.q(conn, """
        SELECT o.*, a.number AS work_no, p.idx AS problem_idx, p.statement
        FROM observations o
        LEFT JOIN submissions s ON s.id = o.submission_id
        LEFT JOIN assignments a ON a.id = s.assignment_id
        LEFT JOIN problems p ON p.id = o.problem_id
        WHERE o.student_id=? ORDER BY o.created_at""", (sid,))]


def _ref(o: dict) -> dict:
    return {"sub_id": o["submission_id"], "work_no": o["work_no"], "problem_idx": o["problem_idx"],
            "line_no": o["line_no"], "evidence": o["evidence"], "statement": o.get("statement")}


def weighted_share(works: list, hit_ids: set) -> float:
    if not works:
        return 0.0
    num = sum(DECAY ** i for i, w in enumerate(works) if w in hit_ids)
    den = sum(DECAY ** i for i, _ in enumerate(works))
    return num / den


# ---------------------------------------------------------------- портрет

def build(conn, sid: int, lang: str = "ru") -> dict:
    student = dict(db.q1(conn, "SELECT * FROM students WHERE id=?", (sid,)))
    works = _works(conn, sid)
    order = [w["id"] for w in works]  # новые первыми
    n = len(works)
    by_skill = _skills_per_work(conn, sid)
    obs = _obs(conn, sid)
    auto = [o for o in obs if o["source"] == "auto"]
    teacher = [o for o in obs if o["source"] == "teacher"]

    # ---- 1. ошибки по (навык × тег)
    groups = defaultdict(list)
    for o in auto:
        if o["kind"] == "error":
            groups[(o["skill"], o["tag"])].append(o)
    errors = []
    for (skill, tag), items in groups.items():
        skill_works = [w for w in order if w in by_skill.get(skill, set())]
        hit = {o["submission_id"] for o in items}
        p = weighted_share(skill_works, hit)
        dets = [json.loads(o["detail"] or "{}") for o in items]
        detail_key = None
        if sum(1 for d in dets if d.get("division")) * 2 > len(dets):
            detail_key = "division"
        elif sum(1 for d in dets if d.get("where") == "D") * 2 > len(dets):
            detail_key = "D"
        elif sum(1 for d in dets if d.get("domain")) * 2 > len(dets):
            detail_key = "domain"
        errors.append({
            "skill": skill, "tag": tag, "count": len(hit), "total": len(skill_works), "p": p,
            "weak": len(hit) >= MIN_CASES and p >= MIN_SHARE,
            "low_data": len(hit) < MIN_CASES,
            "detail": DETAIL_TEXT[detail_key][lang] if detail_key else None,
            "refs": [_ref(o) for o in sorted(items, key=lambda o: o["created_at"], reverse=True)],
        })
    errors.sort(key=lambda e: (-e["weak"], -e["p"], -e["count"]))

    # ---- сильные стороны: навык почти без ошибок
    strengths = []
    for skill, sub_ids in by_skill.items():
        tot = len(sub_ids)
        bad = {o["submission_id"] for o in auto if o["kind"] == "error" and o["skill"] == skill}
        if tot >= MIN_CASES and len(bad) / tot <= 0.2:
            strengths.append({"skill": skill, "clean": tot - len(bad), "total": tot})

    # ---- 2. методы (квадратные уравнения) и привычки
    quad_works = [w for w in order if w in by_skill.get("quadratic", set())]
    mcount = Counter()
    mrefs = defaultdict(list)
    for o in auto:
        if o["kind"] == "method":
            mcount[(o["tag"], o["submission_id"])] = 1
            mrefs[o["tag"]].append(_ref(o))
    methods = []
    for m in ("disc", "vieta", "factoring"):
        c = len({s for (t, s) in mcount if t == m})
        methods.append({"tag": m, "count": c, "total": len(quad_works), "refs": mrefs[m]})

    habits = {}
    for h in ("check_done", "skip_steps", "late", "domain_noted"):
        items = [o for o in auto if o["kind"] == "habit" and o["tag"] == h]
        hit = {o["submission_id"] for o in items}
        habits[h] = {"tag": h, "count": len(hit), "total": n, "p": weighted_share(order, hit),
                     "refs": [_ref(o) for o in items]}
    check_rate = habits["check_done"]["count"] / n if n else 0

    def auto_supports(key: str) -> bool:
        if key == "check_done:low":
            return n >= MIN_CASES and check_rate <= 1 / 3
        if any(e["tag"] == key and e["count"] >= 2 for e in errors):
            return True
        return key in habits and habits[key]["count"] >= 2

    # ---- 3. что говорит учитель
    tgroups = defaultdict(list)
    for o in teacher:
        tgroups[o["tag"]].append(o)
    teacher_items = []
    for tag, items in tgroups.items():
        confirmed = any(auto_supports(k) for k in T.CONFIRMS.get(tag, set()))
        teacher_items.append({
            "tag": tag, "count": len({o["submission_id"] for o in items}),
            "quotes": [{"text": o["evidence"], "work_no": o["work_no"],
                        "comment": json.loads(o["detail"] or "{}").get("comment")} for o in items],
            "confirmed": confirmed,
        })
    teacher_items.sort(key=lambda t: -t["count"])
    teacher_tags = set(tgroups)
    for e in errors:
        e["confirmed"] = e["count"] >= 2 and (e["tag"] in teacher_tags or any(
            e["tag"] in T.CONFIRMS.get(t, set()) for t in teacher_tags))

    # ---- 4. как работать
    recs = []
    for e in errors:
        if e["weak"] and e["tag"] in T.TAGS:
            t = T.TAGS[e["tag"]]
            recs.append({
                "key": f"{e['skill']}:{e['tag']}", "tag": e["tag"],
                "title": f"{t[lang]} — {T.skill_name(e['skill'], lang).lower()}",
                "why": share_text(e["count"], e["total"], lang) + (f" ({e['detail']})" if e["detail"] else ""),
                "teacher": t.get(f"teacher_{lang}"), "student": t.get(f"student_{lang}"),
                "refs": e["refs"][:4], "score": e["p"] + (0.1 if e["confirmed"] else 0),
                "confirmed": e["confirmed"],
            })
    if n >= MIN_CASES and check_rate <= 1 / 3:
        t = T.TAGS["check_done"]
        recs.append({
            "key": "habit:check_done", "tag": "check_done",
            "title": {"ru": "Редко проверяет ответ", "kk": "Жауапты сирек тексереді"}[lang],
            "why": share_text(habits["check_done"]["count"], n, lang),
            "teacher": t[f"teacher_{lang}"], "student": t[f"student_{lang}"],
            # доказательство отсутствия привычки — работы без строки проверки
            "refs": [{"sub_id": w["id"], "work_no": w["number"], "problem_idx": None, "line_no": None,
                      "evidence": {"ru": "нет проверки ответа", "kk": "жауап тексерілмеген"}[lang], "statement": None}
                     for w in works if w["id"] not in {r["sub_id"] for r in habits["check_done"]["refs"]}][:4],
            "score": 0.55 - 0.3 * check_rate + (0.1 if {"no_check", "rushes"} & teacher_tags else 0),
            "confirmed": bool({"no_check", "rushes"} & teacher_tags),
        })
    for h in ("skip_steps", "late"):
        hb = habits[h]
        need = MIN_CASES if h == "skip_steps" else 2
        if hb["count"] >= need and (h == "late" or hb["p"] >= MIN_SHARE):
            t = T.TAGS[h]
            recs.append({
                "key": f"habit:{h}", "tag": h, "title": t[lang],
                "why": share_text(hb["count"], n, lang),
                "teacher": t[f"teacher_{lang}"], "student": t[f"student_{lang}"],
                "refs": hb["refs"][:4], "score": hb["p"] * (0.6 if h == "late" else 1),
                "confirmed": any(h in T.CONFIRMS.get(tt, set()) for tt in teacher_tags),
            })
    used = [m for m in methods if m["count"]]
    if len(quad_works) >= 4 and len(used) == 1 and used[0]["count"] / len(quad_works) >= 0.8:
        mname = T.name(used[0]["tag"], lang)
        recs.append({
            "key": "method:mono", "tag": used[0]["tag"],
            "title": {"ru": f"Один способ для всех квадратных: {mname}",
                      "kk": f"Барлық квадрат теңдеуге бір тәсіл: {mname}"}[lang],
            "why": share_text(used[0]["count"], len(quad_works), lang),
            "teacher": T.METHOD_MONO[f"teacher_{lang}"].format(m=mname),
            "student": T.METHOD_MONO[f"student_{lang}"],
            "refs": used[0]["refs"][:3], "score": 0.35, "confirmed": False,
        })
    recs.sort(key=lambda r: -r["score"])

    return {
        "student": student, "n_works": n, "works": works,
        "errors": errors, "strengths": strengths, "methods": methods, "habits": habits,
        "check_rate": check_rate, "teacher": teacher_items, "recs": recs,
        "n_obs": len(obs), "has_live": any(w["source"] == "live" for w in works),
    }


def snapshot(p: dict) -> dict:
    """Сжатое состояние портрета — чтобы показать, что изменилось после проверки."""
    snap = {f"err:{e['skill']}:{e['tag']}": (e["count"], e["total"], round(e["p"], 3), e["weak"]) for e in p["errors"]}
    for h, v in p["habits"].items():
        snap[f"habit:{h}"] = (v["count"], v["total"], round(v["p"], 3), None)
    for t in p["teacher"]:
        snap[f"teacher:{t['tag']}"] = (t["count"], None, 0, None)
    snap["recs"] = [r["key"] for r in p["recs"]]
    return snap


def diff(before: dict, after: dict, lang: str = "ru") -> list[str]:
    out = []
    for key, val in after.items():
        if key == "recs":
            continue
        old = before.get(key)
        if old == val:
            continue
        _, *rest = key.split(":")
        if key.startswith("teacher:"):
            label = ("Учитель пишет «" if lang == "ru" else "Мұғалім жазады: «") + T.name(rest[0], lang) + "»"
            out.append(f"{label}: {old[0] if old else 0} → {val[0]}")
            continue
        if key.startswith("err:"):
            skill, tag = rest
            label = f"{T.name(tag, lang)} ({T.skill_name(skill, lang).lower()})"
        else:
            label = T.name(rest[0], lang)
        was = f"{old[0]}/{old[1]}" if old else "—"
        now = f"{val[0]}/{val[1]}"
        mark = ""
        if val[3] and not (old and old[3]):
            mark = " · " + ("стало слабым местом" if lang == "ru" else "әлсіз тұсқа айналды")
        if old and val[0] == old[0]:
            continue  # случаев не прибавилось — вырос только знаменатель; не шумим
        share = f" ({round(old[2] * 100) if old else 0}% → {round(val[2] * 100)}%)" if key.startswith("err:") else ""
        out.append(f"{label}: {was} → {now}{share}{mark}")
    b, a = before.get("recs", []), after.get("recs", [])
    for i, k in enumerate(a):
        if k in b and b.index(k) > i:
            out.append(("Рекомендация поднялась выше: " if lang == "ru" else "Ұсыныс жоғары көтерілді: ")
                       + _rec_label(k, lang) + f" (#{b.index(k) + 1} → #{i + 1})")
        elif k not in b:
            out.append(("Новая рекомендация: " if lang == "ru" else "Жаңа ұсыныс: ") + _rec_label(k, lang))
    return out


def _rec_label(key: str, lang: str) -> str:
    kind, *rest = key.split(":")
    if kind in ("habit", "method"):
        return T.name(rest[0], lang) if kind == "habit" else ("способ решения" if lang == "ru" else "шешу тәсілі")
    return f"{T.name(rest[0], lang)} ({T.skill_name(kind, lang).lower()})"


# ---------------------------------------------------------------- карта класса

def class_map(conn, lang: str = "ru", student_ids=None) -> list[dict]:
    """student_ids — необязательный фильтр видимости; None — все ученики."""
    rows = []
    for s in db.q(conn, "SELECT * FROM students ORDER BY id"):
        if student_ids is not None and s["id"] not in student_ids:
            continue
        p = build(conn, s["id"], lang)
        cells = {}
        order = [w["id"] for w in p["works"]]
        by_skill = _skills_per_work(conn, s["id"])
        auto_err = defaultdict(set)
        for e in p["errors"]:
            for r in e["refs"]:
                auto_err[e["skill"]].add(r["sub_id"])
        for skill in T.SKILLS:
            sw = [w for w in order if w in by_skill.get(skill, set())]
            hit = auto_err.get(skill, set())
            cells[skill] = {"bad": len(hit & set(sw)), "total": len(sw), "p": weighted_share(sw, hit)}
        top = next((r for r in p["recs"]), None)
        watch = next((e for e in p["errors"] if e["low_data"]), None)
        rows.append({
            "watch": (f"{T.name(watch['tag'], lang)} — {watch['count']}/{watch['total']}" if watch else None),
            "id": s["id"], "alias": s["alias"], "n_works": p["n_works"], "cells": cells,
            "check": (p["habits"]["check_done"]["count"], p["n_works"]),
            "late": p["habits"]["late"]["count"],
            "top": top["title"] if top else None,
            "top_why": top["why"] if top else None,
        })
    return rows

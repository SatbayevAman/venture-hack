"""Эпсилон: отметки учителя, портрет с отметками, страница «Качество»."""
import pytest

from core import db, portrait, quality, review, seed


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("eps") / "base.db"
    seed.build(db.connect(path))
    return path


@pytest.fixture
def conn(seeded, tmp_path):
    """Свежая копия засеянной базы на каждый тест (засев один раз на модуль)."""
    src = db.connect(seeded)
    dst = db.connect(tmp_path / "t.db")
    src.backup(dst)
    src.close()
    return dst


def lost_root_obs(conn, sid=1):
    return [r["id"] for r in db.q(conn, """SELECT id FROM observations WHERE student_id=? AND tag='lost_root'
                                           AND source='auto' ORDER BY created_at""", (sid,))]


def error(p, tag):
    return next((e for e in p["errors"] if e["tag"] == tag), None)


# ---------------------------------------------------------------- apply

def test_apply_reject_retag_confirm(conn):
    ids = lost_root_obs(conn)
    rows = [dict(r) for r in db.q(conn, f"SELECT * FROM observations WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id", ids)]
    review.add(conn, ids[0], "reject")
    review.add(conn, ids[1], "retag", new_tag="calc", comment="это арифметика")
    review.add(conn, ids[2], "confirm")
    out = {r["id"]: r for r in review.apply(conn, rows)}
    assert ids[0] not in out
    assert out[ids[1]]["tag"] == "calc" and out[ids[1]]["orig_tag"] == "lost_root"
    assert out[ids[2]]["reviewed"] == "confirm" and out[ids[2]]["tag"] == "lost_root"
    assert out[ids[3]] is next(r for r in rows if r["id"] == ids[3])  # без отметки — та же строка
    # в журнале остаётся всё
    assert len(lost_root_obs(conn)) == 4


def test_latest_mark_wins(conn):
    oid = lost_root_obs(conn)[0]
    rows = [dict(db.q1(conn, "SELECT * FROM observations WHERE id=?", (oid,)))]
    review.add(conn, oid, "reject")
    assert review.apply(conn, rows) == []
    review.add(conn, oid, "confirm")
    assert review.apply(conn, rows)[0]["reviewed"] == "confirm"
    review.add(conn, oid, "retag", new_tag="sign")
    assert review.apply(conn, rows)[0]["tag"] == "sign"
    assert review.latest(conn, [oid])[oid]["verdict"] == "retag"


def test_add_validates_tag(conn):
    oid = lost_root_obs(conn)[0]
    with pytest.raises(ValueError):
        review.add(conn, oid, "retag", new_tag="check_done")   # привычка вместо ошибки
    with pytest.raises(ValueError):
        review.add(conn, oid, "retag", new_tag="no_such_tag")
    with pytest.raises(ValueError):
        review.add(conn, oid, "retag")                         # без тега
    with pytest.raises(ValueError):
        review.add(conn, oid, "maybe")
    with pytest.raises(ValueError):
        review.add(conn, 10 ** 9, "reject")
    assert review.latest(conn, [oid]) == {}


def test_apply_on_fresh_db_without_table(tmp_path):
    conn = db.connect(tmp_path / "fresh.db")
    db.init(conn)
    rows = [{"id": 1, "tag": "sign"}]
    assert review.apply(conn, rows) is rows
    assert db.q1(conn, "SELECT name FROM sqlite_master WHERE name='reviews'")


def test_apply_one_query_per_batch(conn):
    ids = lost_root_obs(conn)
    review.add(conn, ids[0], "reject")
    rows = [dict(r) for r in db.q(conn, "SELECT * FROM observations")]
    selects = []
    conn.set_trace_callback(lambda s: selects.append(s) if "FROM reviews" in s else None)
    review.apply(conn, rows)
    conn.set_trace_callback(None)
    assert len(rows) < review.CHUNK and len(selects) == 1


# ---------------------------------------------------------------- портрет

def test_reject_one_lost_root_gives_3_of_6(conn):
    p0 = portrait.build(conn, 1, "ru")
    assert (error(p0, "lost_root")["count"], error(p0, "lost_root")["total"]) == (4, 6)
    ids = lost_root_obs(conn)
    review.add(conn, ids[-1], "reject", comment="это не ошибка")
    p = portrait.build(conn, 1, "ru")
    e = error(p, "lost_root")
    assert (e["count"], e["total"]) == (3, 6)
    assert e["weak"] == (e["count"] >= portrait.MIN_CASES and e["p"] >= portrait.MIN_SHARE)
    assert e["p"] < error(p0, "lost_root")["p"]
    assert ids[-1] not in {r["obs_id"] for r in e["refs"]}
    # ещё одно «неверно» — уже мало данных, не слабое место
    review.add(conn, ids[-2], "reject")
    e2 = error(portrait.build(conn, 1, "ru"), "lost_root")
    assert (e2["count"], e2["weak"], e2["low_data"]) == (2, False, True)
    assert portrait.build(conn, 1, "ru")["recs"][0]["tag"] != "lost_root"


def test_retag_moves_observation(conn):
    ids = lost_root_obs(conn)
    review.add(conn, ids[0], "retag", new_tag="calc")
    p = portrait.build(conn, 1, "ru")
    assert error(p, "lost_root")["count"] == 3
    calc = [e for e in p["errors"] if e["tag"] == "calc" and ids[0] in {r["obs_id"] for r in e["refs"]}]
    assert calc


def test_without_marks_portrait_is_unchanged(conn, monkeypatch):
    review.ensure_schema(conn)
    with_module = {sid: portrait.build(conn, sid, "ru") for sid in range(1, 9)}
    cmap = portrait.class_map(conn, "ru")
    monkeypatch.setattr(review, "apply", lambda c, rows: rows)  # как до появления модуля
    before = {sid: portrait.build(conn, sid, "ru") for sid in range(1, 9)}
    assert with_module == before
    assert cmap == portrait.class_map(conn, "ru")
    # единственное добавленное поле — ссылка на наблюдение в доказательствах
    refs = [r for e in before[1]["errors"] for r in e["refs"]]
    assert refs and all(isinstance(r["obs_id"], int) for r in refs)


def test_confirm_does_not_change_numbers(conn):
    snap = portrait.snapshot(portrait.build(conn, 1, "ru"))
    for oid in lost_root_obs(conn):
        review.add(conn, oid, "confirm")
    assert portrait.snapshot(portrait.build(conn, 1, "ru")) == snap


def test_reject_teacher_tag_removes_it_from_portrait(conn):
    t = db.q1(conn, "SELECT * FROM observations WHERE student_id=1 AND source='teacher' ORDER BY id")
    before = {x["tag"]: x["count"] for x in portrait.build(conn, 1, "ru")["teacher"]}
    review.add(conn, t["id"], "reject", comment="модель ошиблась")
    after = {x["tag"]: x["count"] for x in portrait.build(conn, 1, "ru")["teacher"]}
    assert after.get(t["tag"], 0) == before[t["tag"]] - 1 or (t["tag"] not in after and before[t["tag"]] == 1)


def test_not_an_error_before_record(conn):
    from core import pipeline
    aid = seed.live_assignment_id(conn)
    answers = {pr["id"]: seed.LIVE_DEMO_TEXT[pr["idx"]].split("\n") for pr in pipeline.problems_of(conn, aid)}
    sub_id, res = pipeline.record_submission(conn, 1, aid, answers, "2026-09-24 08:00:00", source="live")
    pids = [pid for pid, r in res.items() if r.first_error and r.first_error["tag"] == "lost_root"]
    assert pids
    assert review.reject_errors(conn, sub_id, pids, comment="это не ошибка") == len(pids)
    e = error(portrait.build(conn, 1, "ru"), "lost_root")
    assert (e["count"], e["total"]) == (4, 7)
    marks = [o["review"]["verdict"] for o in review.for_submission(conn, sub_id, "auto") if o["problem_id"] in pids
             and o["kind"] == "error"]
    assert marks == ["reject"] * len(pids)


# ---------------------------------------------------------------- качество

def test_tag_precision(conn):
    ids = [r["id"] for r in db.q(conn, "SELECT id FROM observations WHERE tag='lost_root' AND source='auto' ORDER BY id")]
    assert len(ids) == 6
    review.add(conn, ids[0], "confirm")
    review.add(conn, ids[1], "confirm")
    review.add(conn, ids[2], "confirm")
    review.add(conn, ids[3], "reject")
    review.add(conn, ids[4], "retag", new_tag="calc")
    review.add(conn, ids[5], "retag", new_tag="calc")
    review.add(conn, ids[5], "retag", new_tag="calc")  # повтор не считается дважды
    d = quality.tag_status(conn, "lost_root")
    assert (d["reviewed"], d["confirm"], d["reject"], d["retag"]) == (6, 3, 1, 2)
    assert d["precision"] == pytest.approx(0.5)
    assert (d["top_replacement"], d["top_replacement_n"]) == ("calc", 2)
    assert d["needs_review"]
    assert d["observations"] == 6
    ov = quality.overall_precision(quality.tag_precision(conn))
    assert (ov["reviewed"], ov["confirm"]) == (6, 3)
    md = quality.candidates_markdown(conn, "ru", ["lost_root"])
    assert md.count("- **") == 3 and "`calc`" in md


def test_needs_review_requires_enough_marks(conn):
    ids = lost_root_obs(conn)
    for oid in ids:
        review.add(conn, oid, "reject")
    d = quality.tag_status(conn, "lost_root")
    assert d["precision"] == 0 and d["reviewed"] == 4 and not d["needs_review"]


def test_eval_accuracy_matches_evaluate_py():
    ev = quality.eval_accuracy()
    assert ev["available"] and (ev["ok_line"], ev["ok_tag"], ev["n"]) == (4, 4, 4)
    missing = quality.eval_accuracy("no/such/labels.csv")
    assert not missing["available"] and missing["n"] == 0


def test_timing_summary_median(conn):
    t = quality.timing_summary(conn)
    assert t["n"] == 0 and t["median_seconds"] is None and t["saved_minutes_per_work"] is None
    for s in (40, 10, 30, 1000):
        quality.record_timing(conn, None, 1, s, 3, 12)
    t = quality.timing_summary(conn)
    assert t["n"] == 4 and t["median_seconds"] == 35
    assert t["manual_minutes"] is None and t["saved_minutes_per_work"] is None  # без опроса — не выдумываем
    quality.set_manual_minutes(conn, 7)
    t = quality.timing_summary(conn)
    assert t["manual_minutes"] == 7 and t["saved_minutes_per_work"] == pytest.approx(7 - 35 / 60)
    quality.set_manual_minutes(conn, None)
    assert quality.timing_summary(conn)["manual_minutes"] is None


def test_ratings_and_export(conn):
    review.add_rating(conn, 1, 2, ["err:quadratic:lost_root"])
    review.add_rating(conn, 1, 4, [])         # последняя оценка ученика заменяет прежнюю
    review.add_rating(conn, 2, 5, ["habit:late"])
    rs = review.rating_summary(conn)
    assert rs["n"] == 3 and rs["n_students"] == 2 and rs["mean"] == pytest.approx(4.5)
    assert rs["wrong"] == [("habit:late", 1)]
    with pytest.raises(ValueError):
        review.add_rating(conn, 1, 6)
    review.add(conn, lost_root_obs(conn)[0], "confirm")
    quality.record_timing(conn, None, 1, 42)
    md = quality.metrics_markdown(conn, "ru")
    assert "100 %" in md and "42 с" in md and "4.5" in md
    assert "tag_precision,lost_root,1.000,1" in quality.metrics_csv(conn)
    assert "Дәлдік" in quality.metrics_markdown(conn, "kk")

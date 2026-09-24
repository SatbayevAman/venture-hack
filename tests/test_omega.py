"""Стыки после слияния шести направлений (агент Омега, пункты O1–O15 из agents/OMEGA.md)."""
import pytest

from core import db, ocr_store, pipeline, portrait, practice, quality, review, seed


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Синтетический класс строится один раз; тесты работают на копии базы."""
    path = tmp_path_factory.mktemp("omega") / "seed.db"
    conn = db.connect(path)
    seed.build(conn)
    conn.close()
    return path


@pytest.fixture
def conn(seeded, tmp_path):
    src = db.connect(seeded)
    dst = db.connect(tmp_path / "t.db")
    src.backup(dst)
    src.close()
    yield dst
    dst.close()


def _aigerim(conn) -> int:
    return db.q1(conn, "SELECT id FROM students WHERE alias=? ORDER BY id LIMIT 1", (seed.STUDENTS[0],))["id"]


def _live_demo(conn, sid: int, aid: int | None = None, text: dict | None = None) -> int:
    """Живая проверка, как в page_check: прежняя живая работа того же задания удаляется."""
    aid = aid or seed.live_assignment_id(conn)
    text = text or seed.LIVE_DEMO_TEXT
    answers = {p["id"]: text.get(p["idx"], "").splitlines() for p in pipeline.problems_of(conn, aid)}
    for o in db.q(conn, "SELECT id FROM submissions WHERE student_id=? AND assignment_id=? AND source='live'", (sid, aid)):
        pipeline.delete_submission(conn, o["id"])
    sub_id, _ = pipeline.record_submission(conn, sid, aid, answers, "2026-09-24 10:00:00", source="live")
    return sub_id


def _lost_root(p: dict) -> tuple:
    e = next(e for e in p["errors"] if e["tag"] == "lost_root")
    return e["count"], e["total"]


def dangling(conn) -> list[str]:
    """Строки, которые ссылаются на несуществующие работы или наблюдения."""
    refs = {"submission_id": "submissions", "source_submission_id": "submissions", "observation_id": "observations"}
    out = []
    for t, cols in db.tables(conn).items():
        for col, target in refs.items():
            if col in cols:
                n = db.q1(conn, f"""SELECT COUNT(*) c FROM {db.qi(t)} WHERE {col} IS NOT NULL
                                    AND {col} NOT IN (SELECT id FROM {target})""")["c"]
                if n:
                    out.append(f"{t}.{col}: {n}")
    return out


# ---------------------------------------------------------------- O1. повторная проверка работы

def test_recheck_leaves_no_traces(conn):
    sid = _aigerim(conn)
    sub = _live_demo(conn, sid)
    assert _lost_root(portrait.build(conn, sid)) == (5, 7)
    err = db.q1(conn, "SELECT id, problem_id FROM observations WHERE submission_id=? AND kind='error'", (sub,))
    review.add(conn, err["id"], "reject", comment="не ошибка")
    assert _lost_root(portrait.build(conn, sid)) == (4, 7)
    # следы других направлений: распознавание, замер времени, тренажёр по этой работе
    pid = err["problem_id"]
    ocr_store.save(conn, sub, {pid: [{"text": "x = 7", "confidence": 0.9}, {"text": "Ответ: 7", "confidence": 0.9}]},
                   {pid: ["x = 7", "Ответ: 7"]})
    quality.record_timing(conn, sub, sid, 42.0)
    item = practice.start_fix_own(conn, sid, sub, pid)
    r = practice.submit_attempt(conn, item, ["x² − 7x = 0", "x(x − 7) = 0", "x = 0 или x = 7"], 1)
    assert r["outcome"] == "self_fixed"

    new = _live_demo(conn, sid)  # повторная проверка той же работы, теперь текстом
    marks = review.latest(conn, [o["id"] for o in db.q(conn, "SELECT id FROM observations WHERE submission_id=?", (new,))])
    assert marks == {}  # старая отметка «неверно» не прирастает к новой ошибке
    assert _lost_root(portrait.build(conn, sid)) == (5, 7)
    assert db.q1(conn, "SELECT COUNT(*) c FROM ocr_lines WHERE submission_id=?", (new,))["c"] == 0
    assert db.q1(conn, "SELECT COUNT(*) c FROM check_timings WHERE submission_id=?", (new,))["c"] == 0
    assert practice.summary(conn, sid)["outcomes"]["self_fixed"] == 1  # исход тренажёра остался в журнале
    assert db.q1(conn, "SELECT source_submission_id s FROM practice_items WHERE id=?", (item,))["s"] is None
    assert dangling(conn) == []


def test_recheck_twice_same_numbers(conn):
    sid = _aigerim(conn)
    _live_demo(conn, sid)
    before = portrait.snapshot(portrait.build(conn, sid))
    _live_demo(conn, sid)
    assert portrait.snapshot(portrait.build(conn, sid)) == before
    assert dangling(conn) == []

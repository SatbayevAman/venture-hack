"""Сквозной сценарий демо: синтетическая история → живая проверка → портрет меняется."""
from core import db, pipeline, portrait, seed
from core.tags import tag_comment_keywords


def test_demo_scenario(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed.build(conn)
    p = portrait.build(conn, 1, "ru")  # Айгерим
    lost = next(e for e in p["errors"] if e["tag"] == "lost_root")
    assert (lost["count"], lost["total"], lost["weak"], lost["confirmed"]) == (4, 6, True, True)
    assert p["habits"]["check_done"]["count"] == 1
    assert p["recs"][0]["tag"] == "lost_root"
    before = portrait.snapshot(p)

    aid = seed.live_assignment_id(conn)
    answers = {pr["id"]: seed.LIVE_DEMO_TEXT[pr["idx"]].split("\n") for pr in pipeline.problems_of(conn, aid)}
    sub_id, res = pipeline.record_submission(conn, 1, aid, answers, "2026-09-24 08:00:00", source="live")
    pipeline.record_comment(conn, sub_id, seed.LIVE_DEMO_COMMENT, tag_comment_keywords(seed.LIVE_DEMO_COMMENT), "keywords")
    p2 = portrait.build(conn, 1, "ru")
    lost2 = next(e for e in p2["errors"] if e["tag"] == "lost_root")
    assert (lost2["count"], lost2["total"]) == (5, 7)
    assert any("4/6 → 5/7" in line for line in portrait.diff(before, portrait.snapshot(p2), "ru"))


def test_every_conclusion_has_evidence(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed.build(conn)
    for s in db.q(conn, "SELECT id FROM students"):
        p = portrait.build(conn, s["id"], "ru")
        for e in p["errors"]:
            assert e["refs"] and all(r["work_no"] and r["line_no"] for r in e["refs"])
            if e["count"] < portrait.MIN_CASES:
                assert not e["weak"] and e["low_data"]
        for r in p["recs"]:
            assert r["refs"], r["key"]

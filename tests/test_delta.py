"""Динамика: интервал, тренд, эффект советов, BKT, тренды на карте класса."""
import pytest

from core import db, interventions as I, knowledge as K, pipeline, portrait, seed

FACTOR_OK = "x² − 7x = 0\nx(x − 7) = 0\nx = 0 или x = 7\nОтвет: 0; 7"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("PORTRAIT_MODEL", raising=False)
    c = db.connect(tmp_path / "t.db")
    seed.build(c)
    return c


def _live_ok(conn, sid: int, submitted_at: str) -> int:
    """Живая сдача ДЗ №7: всё верно, «x² = 7x» — разложением на множители."""
    aid = seed.live_assignment_id(conn)
    text = {**seed.LIVE_DEMO_TEXT, 3: FACTOR_OK}
    answers = {pr["id"]: text[pr["idx"]].split("\n") for pr in pipeline.problems_of(conn, aid)}
    sub_id, _ = pipeline.record_submission(conn, sid, aid, answers, submitted_at, source="live")
    return sub_id


# ---------------------------------------------------------------- интервал Уилсона

def test_wilson_known_values():
    # учебные 95-процентные интервалы
    lo, hi = K.wilson(5, 10, z=1.96)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)
    lo, hi = K.wilson(0, 10, z=1.96)
    assert lo == 0 and hi == pytest.approx(0.2775, abs=1e-4)
    # 80 % по умолчанию: уже, чем 95 %, и содержит точечную оценку
    lo, hi = K.wilson(4, 6)
    assert lo == pytest.approx(0.4094, abs=1e-4) and hi == pytest.approx(0.8523, abs=1e-4)
    lo95, hi95 = K.wilson(4, 6, z=1.96)
    assert lo95 < lo < 4 / 6 < hi < hi95
    assert K.wilson(6, 6)[1] == pytest.approx(1.0)


def test_wilson_low_data():
    assert K.wilson(0, 0) is None
    assert K.wilson(2, 2) is None
    assert K.wilson(1, 3) is not None


# ---------------------------------------------------------------- тренд

@pytest.mark.parametrize("seq,expected", [
    ([1, 1, 1], None),                 # меньше 4 работ
    ([], None),
    ([1, 1, 0, 0], "down"),
    ([0, 0, 1, 1], "up"),
    ([1, 0, 1, 0], "flat"),
    ([1, 0, 0, 0], "down"),            # 4 работы: −1 случай и −50 п. п.
    ([1, 0, 1, 0, 0], "down"),         # 5 работ: средняя не участвует
    ([0, 0, 1, 0, 1], "up"),
    ([1, 1, 1, 0, 1, 0], "down"),      # 6 работ: 3 → 1
    ([1, 1, 0, 1, 0, 0], "flat"),      # 6 работ: 2 → 1 — мало для вывода
    ([0, 1, 0, 1, 1, 1], "up"),
    ([1, 0, 1, 1, 0, 1], "flat"),      # Айгерим, «потеря корня», ДЗ №1–6
    ([1, 0, 1, 1, 0, 1, 0, 0, 0], "down"),
])
def test_trend(seq, expected):
    assert K.trend(seq) == expected
    assert K.trend([{"error": x} for x in seq]) == expected


def test_trend_thresholds_are_constants():
    assert (K.MIN_WORKS_TREND, K.MIN_DIFF, K.MIN_DIFF_SMALL, K.MIN_SHARE_DIFF_SMALL) == (4, 2, 1, 0.30)


# ---------------------------------------------------------------- последовательности

def test_sequence_matches_portrait(conn):
    seq = K.sequence(conn, 1, "quadratic", "lost_root")
    assert [s["work_no"] for s in seq] == [1, 2, 3, 4, 5, 6]
    assert K.values(seq) == [1, 0, 1, 1, 0, 1]
    assert [s["submitted_at"] for s in seq] == sorted(s["submitted_at"] for s in seq)
    lost = next(e for e in portrait.build(conn, 1)["errors"] if e["tag"] == "lost_root")
    assert (sum(K.values(seq)), len(seq)) == (lost["count"], lost["total"])
    # привычка «проверка ответа» — по всем работам
    chk = K.habit_sequence(conn, 1, "check_done")
    assert len(chk) == 6 and sum(K.values(chk)) == 1
    assert K.cumulative([1, 0, 1, 1]) == [1.0, 0.5, pytest.approx(2 / 3), 0.75]


# ---------------------------------------------------------------- эффект советов

def test_effect_before_after(conn):
    key = "quadratic:lost_root"
    assert I.effect(conn, 1, key) is None  # не отмечен
    seq = K.sequence(conn, 1, "quadratic", "lost_root")
    applied = seq[3]["submitted_at"][:10]  # перед ДЗ №4
    assert seq[2]["submitted_at"][:10] < applied
    I.mark_applied(conn, 1, key, applied, "разобрали разложение на множители")
    eff = I.effect(conn, 1, key)
    assert (eff["e_before"], eff["n_before"], eff["e_after"], eff["n_after"]) == (2, 3, 2, 3)
    assert eff["p_before"] == pytest.approx(2 / 3) and eff["p_after"] == pytest.approx(2 / 3)
    assert eff["ready"] and eff["need_more"] == 0 and eff["delta"] == pytest.approx(0)

    # более поздняя отметка — после неё только 2 работы
    I.mark_applied(conn, 1, key, seq[4]["submitted_at"][:10], None)
    eff = I.effect(conn, 1, key)
    assert (eff["n_before"], eff["n_after"], eff["ready"], eff["need_more"]) == (4, 2, False, 1)
    assert eff["delta"] is None
    assert [m["applied_at"] for m in I.list_for(conn, 1)] == sorted(
        (m["applied_at"] for m in I.list_for(conn, 1)), reverse=True)

    # отметка «сегодня» без новых работ — ждём 3 работы
    I.mark_applied(conn, 1, "habit:check_done", "2026-09-24", None)
    eff = I.effect(conn, 1, "habit:check_done")
    assert eff["kind"] == "habit" and (eff["e_before"], eff["n_before"]) == (1, 6)
    assert (eff["n_after"], eff["ready"], eff["need_more"]) == (0, False, 3)

    # у «одного способа» нет доли ошибок — эффект не считается
    I.mark_applied(conn, 1, "method:mono", "2026-09-24", None)
    assert I.effect(conn, 1, "method:mono")["supported"] is False


def test_interventions_schema_idempotent(conn):
    I.ensure_schema(conn)
    I.ensure_schema(conn)
    iid = I.mark_applied(conn, 2, "linear:sign", "2026-09-20", "  ")
    row = I.list_for(conn, 2)[0]
    assert row["id"] == iid and row["note"] is None and row["applied_at"] == "2026-09-20"
    I.remove(conn, iid)
    assert I.list_for(conn, 2) == []


# ---------------------------------------------------------------- BKT

def test_bkt_single_update_by_hand():
    prm = K.BKTParams(0.3, 0.15, 0.1, 0.2)
    # верно: 0.3·0.9 / (0.3·0.9 + 0.7·0.2) = 0.27 / 0.41; затем + (1 − ·)·0.15
    post = 0.27 / 0.41
    assert K.bkt_trajectory([0], prm)[0] == pytest.approx(post + (1 - post) * 0.15)
    # ошибка: 0.3·0.1 / (0.3·0.1 + 0.7·0.8) = 0.03 / 0.59
    post = 0.03 / 0.59
    assert K.bkt_trajectory([1], prm)[0] == pytest.approx(post + (1 - post) * 0.15)


def test_bkt_grows_after_correct_series():
    traj = K.bkt_trajectory([1, 1, 0, 0, 0, 0], K.BKT_DEFAULT)
    assert all(b > a for a, b in zip(traj[1:], traj[2:]))
    assert traj[-1] > 0.9


def test_bkt_fit_bounds(conn):
    assert K.fit_bkt([[1, 0], [0]]) == K.BKT_DEFAULT  # меньше 10 наблюдений
    prm = K.fit_for(conn, "quadratic", "lost_root")
    assert 0.1 <= prm.L0 <= 0.9 and 0.05 <= prm.T <= 0.4
    assert 0.05 <= prm.S <= 0.3 and 0.1 <= prm.G <= 0.4
    assert prm.S < 0.5 and prm.G < 0.5
    # подобранные параметры правдоподобнее значений по умолчанию
    seqs = K.class_sequences(conn, "quadratic", "lost_root")
    assert sum(len(s) for s in seqs) == 48
    assert _loglik(seqs, prm) >= _loglik(seqs, K.BKT_DEFAULT)


def _loglik(seqs, prm):
    import math
    ll = 0.0
    for s in seqs:
        L = prm.L0
        for err in s:
            p_ok = L * (1 - prm.S) + (1 - L) * prm.G
            ll += math.log(1 - p_ok if err else p_ok)
            L = K.bkt_step(L, not err, prm)
    return ll


# ---------------------------------------------------------------- карта класса и модель портрета

def test_class_map_has_trend(conn):
    rows = portrait.class_map(conn, "ru")
    for r in rows:
        for skill, cell in r["cells"].items():
            assert set(cell) == {"bad", "total", "p", "trend"}
            assert cell["trend"] in (None, "down", "up", "flat")
    aig = rows[0]["cells"]["quadratic"]
    assert (aig["bad"], aig["total"], aig["trend"]) == (4, 6, "flat")
    arman = next(r for r in rows if r["alias"] == "Арман")["cells"]["quadratic"]
    assert (arman["bad"], arman["total"], arman["trend"]) == (2, 6, "down")


def test_default_model_keeps_demo_numbers(conn, monkeypatch):
    p = portrait.build(conn, 1, "ru")
    lost = next(e for e in p["errors"] if e["tag"] == "lost_root")
    assert (lost["count"], lost["total"], lost["weak"]) == (4, 6, True)
    assert "mastery" not in lost and p["recs"][0]["tag"] == "lost_root"
    monkeypatch.setenv("PORTRAIT_MODEL", "weighted")
    assert portrait.build(conn, 1, "ru") == p


def test_bkt_model_switch(conn, monkeypatch):
    monkeypatch.setenv("PORTRAIT_MODEL", "bkt")
    for sid in range(1, 9):
        pb = portrait.build(conn, sid, "ru")
        pw = portrait.build(conn, sid, "ru", model="weighted")
        for eb, ew in zip(pb["errors"], pw["errors"]):
            assert 0 <= eb["mastery"] <= 1
            assert eb["weak"] == (eb["count"] >= portrait.MIN_CASES and eb["mastery"] <= K.BKT_MASTERED)
            assert (eb["count"], eb["total"], eb["p"]) == (ew["count"], ew["total"], ew["p"])
    lost = next(e for e in portrait.build(conn, 1)["errors"] if e["tag"] == "lost_root")
    assert lost["weak"] and lost["mastery"] < 0.6


# ---------------------------------------------------------------- сценарий улучшения (B4)

def test_improvement_scenario(conn):
    key = "quadratic:lost_root"
    I.mark_applied(conn, 1, key, "2026-09-23", "разобрали: нельзя делить на x")
    before = K.mastery(conn, 1, "quadratic", "lost_root")["p"]
    for ts in ("2026-09-23 18:00:00", "2026-09-24 18:00:00", "2026-09-25 18:00:00"):
        _live_ok(conn, 1, ts)
    seq = K.sequence(conn, 1, "quadratic", "lost_root")
    assert K.values(seq) == [1, 0, 1, 1, 0, 1, 0, 0, 0]
    assert K.trend(seq) == "down"
    traj = K.mastery(conn, 1, "quadratic", "lost_root")["trajectory"]
    assert traj[-3] > before and traj[-1] > traj[-2] > traj[-3]
    eff = I.effect(conn, 1, key)
    assert eff["ready"] and (eff["e_before"], eff["n_before"], eff["e_after"], eff["n_after"]) == (4, 6, 0, 3)
    assert eff["p_after"] < eff["p_before"] and eff["delta"] < 0
    cell = portrait.class_map(conn, "ru")[0]["cells"]["quadratic"]
    assert cell["trend"] == "down" and (cell["bad"], cell["total"]) == (4, 9)

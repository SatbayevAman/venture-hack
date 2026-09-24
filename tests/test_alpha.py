"""Агент Альфа: распознавание фото → строки с уверенностью. Без сети и без ключа API."""
import json

import pytest

import evaluate
from core import db, llm, ocr, ocr_store, pipeline, seed

CFG = llm.LLMConfig("anthropic", "test-key", "test-model")
PNG = b"not really an image"  # prepare_image вернёт байты как есть


def fake_call(monkeypatch, replies):
    """Подменить llm._call: отвечает по очереди, запоминает запросы."""
    calls = []

    def _call(cfg, prompt, image=None, system="", max_tokens=2000):
        calls.append({"prompt": prompt, "image": image})
        return replies[len(calls) - 1]
    monkeypatch.setattr(llm, "_call", _call)
    return calls


GOOD = json.dumps({"problems": [{"number": 1, "lines": [{"text": "x = 7", "unsure": []},
                                                        {"text": "Ответ: 7", "unsure": []}]}],
                   "unassigned": []}, ensure_ascii=False)


# ---------------------------------------------------------------- score_lines

def test_score_illegible():
    [l] = ocr.score_lines([{"text": "x = ?7", "unsure": []}])
    assert "illegible" in l["flags"] and l["confidence"] < ocr.UNSURE and l["confidence"] <= 0.4


def test_score_unsure_list_is_illegible():
    [l] = ocr.score_lines([{"text": "x = 17", "unsure": ["17"]}])
    assert l["flags"] == ["illegible"] and l["confidence"] == 0.4


def test_score_clean_line():
    [l] = ocr.score_lines([{"text": "x₁ = (5 + 1)/2 = 3", "unsure": []}])
    assert l["confidence"] == 0.95 and l["flags"] == []


@pytest.mark.parametrize("text", ["Ответ: 2; 3", "Проверка: 3² − 5·3 + 6 = 0 ✓", "ОДЗ: x ≠ 3",
                                  "x = 5 не подходит", "x₁ = 3, x₂ = 2", "x = 0 или x = 5",
                                  "x₁,₂ = (5 ± 1)/2", "Решение"])
def test_score_labels_and_words_not_unparsable(text):
    [l] = ocr.score_lines([{"text": text, "unsure": []}])
    assert "unparsable" not in l["flags"]


def test_score_unparsable():
    [l] = ocr.score_lines([{"text": "x = ((", "unsure": []}])
    assert "unparsable" in l["flags"] and l["confidence"] <= 0.3


def test_score_plain_strings_accepted():
    [l] = ocr.score_lines(["2x = 8"])
    assert l["confidence"] == 0.95 and l["unsure"] == []


def test_seed_lines_have_no_false_unparsable():
    conn = db.connect(":memory:")
    seed.build(conn)
    bad = {r["raw_text"] for r in db.q(conn, "SELECT raw_text FROM steps") if ocr.is_unparsable(r["raw_text"])}
    assert not bad


def test_score_disagree():
    [l] = ocr.score_lines([{"text": "x = 7", "unsure": [], "alternatives": ["x = 7", "x = 1"]}])
    assert "disagree" in l["flags"] and l["confidence"] <= 0.5
    [same] = ocr.score_lines([{"text": "x = 7", "unsure": [], "alternatives": ["x = 7", "x=7"]}])
    assert same["flags"] == []


# ---------------------------------------------------------------- clean_text

@pytest.mark.parametrize("raw,want", [
    (r"3x \cdot 2", "3x · 2"),
    (r"3 \times 2", "3 · 2"),
    (r"\sqrt{49} = 7", "√(49) = 7"),
    (r"\sqrt 5", "√5"),
    (r"x^{2} = 7x", "x² = 7x"),
    ("x^2 + x^3", "x² + x³"),
    (r"2^{10}", "2^10"),
    (r"x^{n+1}", "x^(n+1)"),
    (r"x \le 5", "x ≤ 5"),
    (r"x \geq 5", "x ≥ 5"),
    (r"x \neq 3", "x ≠ 3"),
    (r"x = 5 \pm 1", "x = 5 ± 1"),
    (r"\frac{5 + 1}{2} = 3", "(5 + 1)/2 = 3"),
    (r"\dfrac{1}{2}", "1/2"),
    (r"\frac{-b \pm \sqrt{D}}{2a}", "(-b ± √(D))/(2a)"),
    (r"x_1 = 3", "x₁ = 3"),
    (r"x_{2} = 2", "x₂ = 2"),
    (r"x_{1,2} = 3", "x₁,₂ = 3"),
    (r"$x = 7$", "x = 7"),
    (r"\text{Ответ: } 7", "Ответ: 7"),
    (r"\left(x + 1\right)^{2}", "(x + 1)²"),
    (r"x = 7 \quad x = 0", "x = 7 x = 0"),
    ("  x   =   7 ", "x = 7"),
])
def test_clean_text(raw, want):
    assert ocr.clean_text(raw) == want


def test_clean_text_then_checker_parses():
    assert not ocr.is_unparsable(ocr.clean_text(r"x_{1,2} = \frac{5 \pm \sqrt{1}}{2}"))


# ---------------------------------------------------------------- recognize_detailed

def test_recognize_detailed_valid_json(monkeypatch):
    calls = fake_call(monkeypatch, [GOOD])
    res = llm.recognize_detailed(CFG, PNG, [(1, "x² = 7x")])
    assert res == {1: [{"text": "x = 7", "unsure": []}, {"text": "Ответ: 7", "unsure": []}]}
    assert len(calls) == 1 and calls[0]["image"] is not None


def test_recognize_detailed_fenced_json(monkeypatch):
    fake_call(monkeypatch, ["Вот расшифровка:\n```json\n" + GOOD + "\n```\nГотово."])
    res = llm.recognize_detailed(CFG, PNG, [(1, "x² = 7x")])
    assert [l["text"] for l in res[1]] == ["x = 7", "Ответ: 7"]


def test_recognize_detailed_retry_then_success(monkeypatch):
    calls = fake_call(monkeypatch, ['{"problems": [{"number": 1, "lines": [', GOOD])
    res = llm.recognize_detailed(CFG, PNG, [(1, "x² = 7x")])
    assert [l["text"] for l in res[1]] == ["x = 7", "Ответ: 7"]
    assert len(calls) == 2
    assert calls[1]["image"] is None and '{"problems": [{"number": 1, "lines": [' in calls[1]["prompt"]


def test_recognize_detailed_two_bad_answers(monkeypatch):
    fake_call(monkeypatch, ["не JSON", "опять не JSON"])
    with pytest.raises(llm.LLMError, match="JSON"):
        llm.recognize_detailed(CFG, PNG, [(1, "x² = 7x")])


def test_recognize_detailed_tolerant_schema(monkeypatch):
    reply = json.dumps({"problems": [{"number": "№2", "lines": ["x = 1", {"text": "x = ?", "unsure": "?"}, ""]}],
                        "unassigned": [{"text": "2x = 2", "unsure": []}]}, ensure_ascii=False)
    fake_call(monkeypatch, [reply])
    res = llm.recognize_detailed(CFG, PNG, [(2, "2x = 2")])
    assert res[2] == [{"text": "x = 1", "unsure": []}, {"text": "x = ?", "unsure": ["?"]}]
    assert res[llm.UNASSIGNED] == [{"text": "2x = 2", "unsure": []}]


def test_recognize_old_format(monkeypatch):
    reply = json.dumps({"problems": [{"number": 1, "lines": [{"text": "x = 7", "unsure": []},
                                                             {"text": "Ответ: 7", "unsure": []}]}],
                        "unassigned": [{"text": "мусор", "unsure": []}]}, ensure_ascii=False)
    fake_call(monkeypatch, [reply])
    assert llm.recognize(CFG, PNG, [(1, "x² = 7x")]) == {1: ["x = 7", "Ответ: 7"]}


def test_recognize_detailed_two_passes(monkeypatch):
    second = json.dumps({"problems": [{"number": 1, "lines": [{"text": "x = 1", "unsure": []},
                                                              {"text": "Ответ: 7", "unsure": []}]}]},
                        ensure_ascii=False)
    calls = fake_call(monkeypatch, [GOOD, second])
    res = llm.recognize_detailed(CFG, PNG, [(1, "x² = 7x")], passes=2)
    assert len(calls) == 2 and calls[0]["prompt"] != calls[1]["prompt"]
    first, ans = ocr.score_lines(res[1])
    assert first["alternatives"] == ["x = 7", "x = 1"] and "disagree" in first["flags"]
    assert "alternatives" not in ans and ans["confidence"] == 0.95


def test_merge_readings_missing_line():
    a = [{"text": "x = 7", "unsure": []}, {"text": "Ответ: 7", "unsure": []}]
    b = [{"text": "x=7", "unsure": ["7"]}, {"text": "x = 0", "unsure": []}, {"text": "Ответ: 7", "unsure": []}]
    m = ocr.merge_readings(a, b)
    assert [l["text"] for l in m] == ["x = 7", "x = 0", "Ответ: 7"]
    assert m[0]["unsure"] == ["7"] and "alternatives" not in m[0]
    assert m[1]["alternatives"] == ["", "x = 0"]


# ---------------------------------------------------------------- unverified

def test_unverified_numbering_and_edits():
    raw = {10: ocr.score_lines([{"text": "x² = 7x", "unsure": []},
                                {"text": "x = ?", "unsure": []},       # неуверенная, не правилась
                                {"text": "Ответ: ?", "unsure": []}]),  # неуверенная, исправлена
           11: ocr.score_lines([{"text": "2x = ((", "unsure": []}])}
    answers = {10: ["x² = 7x", "", "x = ?", "Ответ: 7"],  # пустая строка сдвигает номер, как в checker
               11: ["2x = 4"]}
    got = ocr.unverified(raw, answers)
    assert got == {(10, 3): 0.3}


def test_unverified_inserted_line_keeps_mapping():
    raw = {1: ocr.score_lines([{"text": "x = 7", "unsure": ["7"]}])}
    assert ocr.unverified(raw, {1: ["x² = 7x", "x = 7"]}) == {(1, 2): 0.4}
    assert ocr.unverified({}, {1: ["x = 7"]}) == {}


def test_edit_stats():
    raw = {1: ocr.score_lines(["x = 7", "Ответ: 7", "лишняя"])}
    assert ocr.edit_stats(raw, {1: ["x = 7", "Ответ: 0"]}) == {"edited": 2, "total": 3}


# ---------------------------------------------------------------- ocr_store

def test_ocr_store_save_and_stats(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed.build(conn)
    raw = {5: ocr.score_lines([{"text": "x = 7", "unsure": []}, {"text": "x = ?", "unsure": []},
                               {"text": "Ответ: ?", "unsure": []}, {"text": "2x = 4", "unsure": []}])}
    answers = {5: ["x = 7", "x = ?", "Ответ: 7", "2x = 5"]}
    sid = conn.execute("SELECT max(id) FROM submissions").fetchone()[0]
    assert ocr_store.save(conn, sid, raw, answers) == 4
    rows = db.q(conn, "SELECT line_no, ocr_text, final_text, edited FROM ocr_lines ORDER BY line_no")
    assert [(r["line_no"], r["final_text"], r["edited"]) for r in rows] == [
        (1, "x = 7", 0), (2, "x = ?", 0), (3, "Ответ: 7", 1), (4, "2x = 5", 1)]
    s = ocr_store.stats(conn)
    assert s["total"] == 4 and s["edited_share"] == 0.5 and s["unsure_share"] == 0.5
    assert s["edited_among_unsure"] == 0.5 and s["unsure_among_edited"] == 0.5
    # повторное сохранение той же работы заменяет строки, а не дублирует
    ocr_store.save(conn, sid, raw, answers)
    assert ocr_store.stats(conn)["total"] == 4
    assert ocr_store.stats(conn, submission_id=sid + 1)["total"] == 0


def test_ocr_store_deleted_line_and_no_photo():
    conn = db.connect(":memory:")
    ocr_store.ensure_schema(conn)
    conn.executescript(db.SCHEMA)
    conn.execute("INSERT INTO students VALUES (1, 's', 'c')")
    conn.execute("INSERT INTO assignments VALUES (1, 1, 't', '2026-01-01')")
    conn.execute("INSERT INTO submissions VALUES (1, 1, 1, NULL, '2026-01-01', 'live')")
    raw = {3: ocr.score_lines(["x = 7", "мусор"]), 0: ocr.score_lines(["без задачи"])}
    ocr_store.save(conn, 1, raw, {3: ["x = 7"]})
    rows = db.q(conn, "SELECT problem_id, final_text, edited FROM ocr_lines ORDER BY line_no")
    assert [(r["problem_id"], r["final_text"], r["edited"]) for r in rows] == [(3, "x = 7", 0), (3, None, 1)]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ocr_lines)")]
    assert not any("photo" in c or "image" in c for c in cols)  # фото не хранится


# ---------------------------------------------------------------- pipeline

def _live(conn):
    aid = seed.live_assignment_id(conn)
    probs = pipeline.problems_of(conn, aid)
    answers = {p["id"]: seed.LIVE_DEMO_TEXT[p["idx"]].split("\n") for p in probs}
    return aid, probs, answers


def _errors(conn, sub_id):
    return {r["problem_id"]: r for r in db.q(conn, "SELECT * FROM observations WHERE submission_id=? AND kind='error'",
                                             (sub_id,))}


def test_record_submission_unverified_line(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed.build(conn)
    aid, probs, answers = _live(conn)
    res = pipeline.run_checks(conn, aid, answers)
    pid, r = next((pid, r) for pid, r in res.items() if r.first_error and r.first_error["tag"] != "other")
    sub_id, _ = pipeline.record_submission(conn, 1, aid, answers, "2026-09-24 08:00:00", source="live",
                                           line_confidence={(pid, r.first_error["line"]): 0.4})
    errs = _errors(conn, sub_id)
    assert errs[pid]["confidence"] == 0.6 and json.loads(errs[pid]["detail"])["ocr_unverified"] is True
    for other, o in errs.items():
        if other != pid and o["tag"] != "other":
            assert o["confidence"] == 0.9 and "ocr_unverified" not in json.loads(o["detail"])


def test_record_submission_default_unchanged(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed.build(conn)
    aid, probs, answers = _live(conn)
    sub_id, res = pipeline.record_submission(conn, 1, aid, answers, "2026-09-24 08:00:00", source="live")
    errs = _errors(conn, sub_id)
    assert errs
    for o in errs.values():
        assert o["confidence"] == (0.5 if o["tag"] == "other" else 0.9)
        assert "ocr_unverified" not in json.loads(o["detail"])
    # уверенная строка (≥ UNSURE) ничего не меняет
    conn2 = db.connect(tmp_path / "t2.db")
    seed.build(conn2)
    pid = next(iter(errs))
    sub2, _ = pipeline.record_submission(conn2, 1, aid, answers, "2026-09-24 08:00:00", source="live",
                                         line_confidence={(pid, errs[pid]["line_no"]): 0.95})
    assert _errors(conn2, sub2)[pid]["confidence"] == errs[pid]["confidence"]


# ---------------------------------------------------------------- метрики

@pytest.mark.parametrize("a,b,d", [("", "", 0), ("abc", "abc", 0), ("abc", "abd", 1), ("abc", "", 3),
                                   ("x=7", "x=1", 1), ("x²=7x", "x=7", 2), ("kitten", "sitting", 3)])
def test_levenshtein(a, b, d):
    assert evaluate.levenshtein(a, b) == d == evaluate.levenshtein(b, a)


def test_cer():
    assert evaluate.cer(["x = 7"], ["x = 7"]) == (0, 3)
    assert evaluate.cer(["x = 7"], ["x = 1"]) == (1, 3)
    assert evaluate.cer(["x = 7", "Ответ: 7"], ["x = 7"]) == (1, 4)       # пропущенная строка — удаления
    assert evaluate.cer(["x = 7"], ["x = 7", "x = 0"]) == (3, 3)          # лишняя строка — вставки


def test_calibration():
    pairs = [(0.3, True), (0.4, False), (0.95, True), (0.95, False)]
    rows = {th: (rc, fa, n) for th, rc, fa, n in evaluate.calibration(pairs)}
    assert rows[0.8] == (0.5, 0.5, 2)
    assert rows[0.35] == (0.5, 0.0, 1)
    assert rows[0.96] == (1.0, 0.5, 4)


def test_error_unverified_rules():
    from core.checker import check_problem
    res = check_problem("equation", "x² − 6x + 5 = 0", ["D = 36 − 20 = 16", "x₁ = (6 + 4)/2 = 5", "x₂ = (6 − 4)/2 = ?"])
    assert res.first_error and res.first_error["line"] == 2
    assert ocr.error_unverified(7, res, {(7, 3): 0.3})           # непрочитанная строка меняет вывод
    assert not ocr.error_unverified(7, res, {(8, 3): 0.3})       # другая задача
    assert not ocr.error_unverified(7, res, {})
    res2 = check_problem("equation", "x² = 7x", ["x² = 7x", "x = 7"])
    assert res2.first_error["line"] == 2 and res2.first_error["prev_line"] == 1
    assert ocr.error_unverified(1, res2, {(1, 1): 0.4})          # строка «до» перехода
    assert ocr.error_unverified(1, res2, {(1, 2): 0.4})          # строка ошибки
    assert not ocr.error_unverified(1, res2, {(1, 2): 0.95})     # уверенная строка


# ---------------------------------------------------------------- evaluate.py целиком (с подменой модели)

def _eval_env(monkeypatch, tmp_path, reply):
    (tmp_path / "p.jpg").write_bytes(PNG)
    monkeypatch.setattr(evaluate, "ROOT", tmp_path)
    monkeypatch.setattr(llm, "config_from_env", lambda secrets=None: CFG)
    fake_call(monkeypatch, [reply] * 10)


def test_evaluate_ocr_metrics(monkeypatch, tmp_path, capsys):
    reply = json.dumps({"problems": [{"number": 1, "lines": [{"text": "x = 5", "unsure": []},
                                                             {"text": "Ответ: ?", "unsure": []}]},
                                     {"number": 2, "lines": [{"text": "2x = 2", "unsure": []}, {"text": "x = 1", "unsure": []}]}]},
                       ensure_ascii=False)
    _eval_env(monkeypatch, tmp_path, reply)
    labels = tmp_path / "labels.csv"
    labels.write_text("photo,problem,kind,statement,lines,error_line,error_tag,status\n"
                      "p.jpg,1,equation,x² = 5x,x = 5 | Ответ: 5,1,lost_root,\n"
                      "p.jpg,2,equation,3x + 5 = x + 7,2x = 2 | x = 1,0,,\n"
                      "p.jpg,3,equation,x = 1,x = 1,0,,draft\n", encoding="utf-8")
    out = tmp_path / "results.md"
    monkeypatch.setattr("sys.argv", ["evaluate.py", "--ocr", "--labels", str(labels), "--out", str(out)])
    evaluate.main()
    md = out.read_text(encoding="utf-8")
    assert "| Строк верно без правки (G1, цель ≥ 90 %) | 3/4 = 75% |" in md
    assert "| Первая ошибка по распознанному (G2, цель ≥ 80 %) | 2/2 = 100% |" in md
    assert "| Помечено «требует проверки» | 1 строк, из них действительно ошибочны 1 |" in md
    assert "| Фото распознано | 1 |" in md and "Калибровка" in md
    assert "пропущено черновиков: 1" in capsys.readouterr().out


def test_evaluate_old_labels_format_without_problem_column(tmp_path, monkeypatch):
    labels = tmp_path / "labels.csv"
    labels.write_text("photo,kind,statement,lines,error_line,error_tag\n,equation,x² = 5x,x = 5 | Ответ: 5,1,lost_root\n",
                      encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["evaluate.py", "--labels", str(labels)])
    evaluate.main()


def test_evaluate_draft_appends_row(monkeypatch, tmp_path):
    reply = json.dumps({"problems": [{"number": 1, "lines": [{"text": "x = 5", "unsure": []},
                                                             {"text": "Ответ: 5", "unsure": []}]}]}, ensure_ascii=False)
    _eval_env(monkeypatch, tmp_path, reply)
    labels = tmp_path / "labels.csv"
    labels.write_text("photo,kind,statement,lines,error_line,error_tag\n,equation,x = 1,x = 1,0,\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["evaluate.py", "--labels", str(labels), "--draft", str(tmp_path / "p.jpg"),
                                     "--statement", "x² = 5x"])
    evaluate.main()
    rows, fields = evaluate.read_labels(labels)
    assert fields[:6] == ["photo", "kind", "statement", "lines", "error_line", "error_tag"]
    assert "problem" in fields and "status" in fields
    assert rows[0]["statement"] == "x = 1" and rows[0]["status"] == ""
    assert rows[1] == {**rows[1], "photo": "p.jpg", "problem": "1", "lines": "x = 5 | Ответ: 5",
                       "error_line": "1", "error_tag": "lost_root", "status": "draft"}


def test_synthetic_set_is_labelled_synthetic():
    from pathlib import Path
    folder = Path(evaluate.__file__).parent / "eval" / "synthetic"
    rows, _ = evaluate.read_labels(folder / "labels.csv")
    assert len({r["photo"] for r in rows}) >= 30
    assert all(r["status"] == "synthetic" and (folder.parent / r["photo"]).exists() for r in rows)
    assert json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["synthetic"] is True

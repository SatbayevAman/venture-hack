"""Аккаунты, изоляция классов, согласие, журнал действий, удаление и выгрузка (агент Дзета)."""
import csv
import io

import pytest

from core import audit, auth, consent, db, pipeline, portrait, privacy, seed


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Синтетический класс строится один раз; тесты работают на копии базы."""
    path = tmp_path_factory.mktemp("zeta") / "seed.db"
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
    auth.ensure_schema(dst)
    yield dst
    dst.close()


def _second_class(conn, name="9 «Б»", aliases=("Ученик-1", "Ученик-2")):
    return [conn.execute("INSERT INTO students (alias, class_name) VALUES (?,?)", (a, name)).lastrowid for a in aliases]


# ---------------------------------------------------------------- пароли и вход

def test_password_hash():
    h, salt = auth.hash_password("correct horse")
    assert h != "correct horse" and "correct horse" not in h
    assert auth.verify_password("correct horse", h, salt)
    assert not auth.verify_password("wrong horse", h, salt)
    h2, salt2 = auth.hash_password("correct horse")
    assert salt != salt2 and h != h2  # одинаковые пароли — разные соли и хэши
    assert len(salt) == 32


def test_authenticate(conn):
    uid = auth.create_user(conn, "Teacher1", "Учитель", "teacher", "secret-pass-1", classes=[seed.CLASS_NAME])
    u = auth.authenticate(conn, "teacher1", "secret-pass-1")
    assert u and u["id"] == uid and u["role"] == "teacher"
    assert "password_hash" not in u and "salt" not in u
    # одинаковый ответ для неизвестного логина и неверного пароля
    assert auth.authenticate(conn, "nobody", "secret-pass-1") is None
    assert auth.authenticate(conn, "teacher1", "wrong") is None
    assert auth.authenticate(conn, "teacher1", "") is None
    auth.set_disabled(conn, uid, True)
    assert auth.authenticate(conn, "teacher1", "secret-pass-1") is None
    row = db.q1(conn, "SELECT password_hash FROM users WHERE id=?", (uid,))
    assert row["password_hash"] != "secret-pass-1"


def test_login_lock():
    key = "lock-test"
    auth.reset_failures(key)
    for i in range(auth.MAX_FAILURES - 1):
        auth.register_failure(key, now=1000 + i)
    assert auth.login_locked(key, now=1010) == 0
    auth.register_failure(key, now=1010)
    assert auth.login_locked(key, now=1011) > 0
    assert auth.login_locked(key, now=1000 + auth.FAILURE_WINDOW + 20) == 0  # окно прошло
    auth.reset_failures(key)


def test_rate_limit():
    u = {"id": 987654}
    limit, window = auth.RATE_LIMITS["llm"]
    assert all(auth.rate_limit(u, "llm", now=5000 + i) for i in range(limit))
    assert not auth.rate_limit(u, "llm", now=5000 + limit)
    assert auth.rate_limit({"id": 987655}, "llm", now=5000 + limit)  # лимит — на пользователя
    assert auth.rate_limit(u, "llm", now=5000 + window + limit)


def test_demo_users(conn, monkeypatch):
    monkeypatch.delenv("PORTRET_DEMO_PASSWORD", raising=False)
    monkeypatch.setenv("PORTRET_ADMIN_LOGIN", "root")
    monkeypatch.setenv("PORTRET_ADMIN_PASSWORD", "admin-pass-123")
    auth.ensure_demo_users(conn)
    auth.ensure_demo_users(conn)  # идемпотентно
    t = auth.get_user_by_login(conn, auth.DEMO_TEACHER)
    s = auth.get_user_by_login(conn, auth.DEMO_STUDENT)
    assert auth.teacher_classes(conn, t["id"]) == [seed.CLASS_NAME]
    assert db.q1(conn, "SELECT alias FROM students WHERE id=?", (s["student_id"],))["alias"] == "Айгерим"
    assert auth.authenticate(conn, "root", "admin-pass-123")["role"] == "admin"
    assert db.q1(conn, "SELECT COUNT(*) c FROM users")["c"] == 3


# ---------------------------------------------------------------- изоляция

def test_isolation_two_teachers(conn):
    other = _second_class(conn)
    t1 = auth.get_user(conn, auth.create_user(conn, "t1", "Учитель 1", "teacher", "pass-one-1", classes=[seed.CLASS_NAME]))
    t2 = auth.get_user(conn, auth.create_user(conn, "t2", "Учитель 2", "teacher", "pass-two-2", classes=["9 «Б»"]))
    v1, v2 = auth.visible_student_ids(conn, t1), auth.visible_student_ids(conn, t2)
    assert v1 and v2 and not (v1 & v2)
    assert v2 == set(other) and len(v1) == len(seed.STUDENTS)
    assert not auth.can_see(conn, t2, 1) and auth.can_see(conn, t1, 1)
    # карта класса учителя 2 не содержит учеников учителя 1
    assert {r["id"] for r in portrait.class_map(conn, "ru", student_ids=v2)} == v2
    nobody = auth.get_user(conn, auth.create_user(conn, "t3", "Без классов", "teacher", "pass-three"))
    assert auth.visible_student_ids(conn, nobody) == set()


def test_isolation_student_and_admin(conn):
    s = auth.get_user(conn, auth.create_user(conn, "s1", "Айгерим", "student", "student-pass", student_id=1))
    assert auth.visible_student_ids(conn, s) == {1}
    a = auth.get_user(conn, auth.create_user(conn, "adm", "Админ", "admin", "admin-pass-1"))
    assert auth.visible_student_ids(conn, a) is None
    assert auth.visible_student_ids(conn, None) == set()


def test_allowed_pages():
    pages = ["class", "portrait", "check", "log", "practice", "manage", "about"]
    assert auth.allowed_pages({"role": "student"}, pages) == ["portrait", "practice"]
    assert auth.allowed_pages({"role": "student"}, ["class", "portrait", "about"]) == ["portrait"]
    assert auth.allowed_pages({"role": "teacher"}, pages) == pages
    assert auth.allowed_pages({"role": "admin"}, pages) == pages
    assert auth.allowed_pages(None, pages) == []


def test_class_map_filter(conn):
    full = portrait.class_map(conn, "ru")
    assert full == portrait.class_map(conn, "ru", student_ids=None)
    part = portrait.class_map(conn, "ru", student_ids={1, 3})
    assert [r["id"] for r in part] == [1, 3]
    assert part == [r for r in full if r["id"] in (1, 3)]
    assert portrait.class_map(conn, "ru", student_ids=set()) == []


# ---------------------------------------------------------------- согласие

def test_consent(conn):
    assert consent.required_ok(conn, 1)  # синтетический — автоматически
    assert db.q1(conn, "SELECT basis FROM consents WHERE student_id=1")["basis"] == "synthetic"
    new_id = _second_class(conn, aliases=("Новенький",))[0]
    assert not consent.required_ok(conn, new_id)
    with pytest.raises(ValueError):
        consent.give(conn, new_id, "", given_by=None)  # без реквизитов формы нельзя
    consent.give(conn, new_id, "№ 12 от 01.09.2026", given_by=None)
    assert consent.required_ok(conn, new_id)
    consent.revoke(conn, new_id)
    assert not consent.required_ok(conn, new_id)
    consent.revoke(conn, 1)
    assert not consent.required_ok(conn, 1)  # отозванное синтетическое не восстанавливается само
    st = {r["student_id"]: r for r in consent.status(conn)}
    assert not st[1]["active"] and not st[new_id]["active"] and st[2]["active"]


# ---------------------------------------------------------------- журнал действий

def test_audit_no_personal_data(conn):
    uid = auth.create_user(conn, "t1", "Учитель Иванова", "teacher", "pass-one-1", classes=[seed.CLASS_NAME])
    u = auth.get_user(conn, uid)
    for action, tt, tid in [("login", "user", uid), ("view_portrait", "student", 1), ("record_submission", "submission", 5),
                            ("export_csv", "export", None), ("consent_revoked", "student", 1),
                            ("delete_student", "student", 2), ("logout", "user", uid)]:
        audit.log(conn, u, action, tt, tid)
    rows = audit.recent(conn)
    assert len(rows) == 7 and rows[0]["action"] == "logout" and rows[-1]["action"] == "login"
    dump = " ".join(str(v) for r in rows for v in r.values())
    for name in seed.STUDENTS + ["Иванова", "t1", "pass-one-1"]:
        assert name not in dump
    with pytest.raises(ValueError):
        audit.log(conn, u, "Айгерим открыла портрет")  # свободный текст не принимается
    with pytest.raises(ValueError):
        audit.log(conn, u, "login", "Айгерим", 1)


# ---------------------------------------------------------------- удаление

def test_delete_student_everywhere(conn):
    sid = 1
    sub_ids = [r["id"] for r in db.q(conn, "SELECT id FROM submissions WHERE student_id=?", (sid,))]
    obs_ids = [r["id"] for r in db.q(conn, "SELECT id FROM observations WHERE student_id=?", (sid,))]
    assert sub_ids and obs_ids
    consent.required_ok(conn, sid)
    user_id = auth.create_user(conn, "s1", "Айгерим", "student", "student-pass", student_id=sid)
    # таблицы «других направлений», созданные прямо в тесте
    conn.executescript("""
        CREATE TABLE practice_sessions (id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, started_at TEXT);
        CREATE TABLE practice_items (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES practice_sessions(id), text TEXT);
        CREATE TABLE practice_answers (id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES practice_items(id), answer TEXT);
        CREATE TABLE review_marks (id INTEGER PRIMARY KEY, observation_id INTEGER NOT NULL REFERENCES observations(id), verdict TEXT);
        CREATE TABLE ocr_runs (id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL, text TEXT);
    """)
    ps = conn.execute("INSERT INTO practice_sessions (student_id, started_at) VALUES (?, 'x')", (sid,)).lastrowid
    ps_other = conn.execute("INSERT INTO practice_sessions (student_id, started_at) VALUES (2, 'x')").lastrowid
    it = conn.execute("INSERT INTO practice_items (session_id, text) VALUES (?, 'q')", (ps,)).lastrowid
    conn.execute("INSERT INTO practice_items (session_id, text) VALUES (?, 'q')", (ps_other,))
    conn.execute("INSERT INTO practice_answers (item_id, answer) VALUES (?, 'a')", (it,))
    conn.execute("INSERT INTO review_marks (observation_id, verdict) VALUES (?, 'ok')", (obs_ids[0],))
    other_obs = db.q1(conn, "SELECT id FROM observations WHERE student_id=2")["id"]
    conn.execute("INSERT INTO review_marks (observation_id, verdict) VALUES (?, 'ok')", (other_obs,))
    conn.execute("INSERT INTO ocr_runs (submission_id, text) VALUES (?, 'lines')", (sub_ids[0],))
    conn.commit()
    before_other = db.q1(conn, "SELECT COUNT(*) c FROM observations WHERE student_id=2")["c"]

    counts = privacy.delete_student(conn, sid, user=None)

    assert counts["students"] == 1 and counts["submissions"] == len(sub_ids)
    in_subs = ",".join(map(str, sub_ids))
    for table, where in [("students", f"id={sid}"), ("submissions", f"student_id={sid}"),
                         ("observations", f"student_id={sid}"), ("consents", f"student_id={sid}"),
                         ("steps", f"submission_id IN ({in_subs})"), ("attempts", f"submission_id IN ({in_subs})"),
                         ("teacher_comments", f"submission_id IN ({in_subs})"),
                         ("practice_sessions", f"student_id={sid}"), ("practice_items", f"id={it}"),
                         ("practice_answers", f"item_id={it}"), ("review_marks", f"observation_id={obs_ids[0]}"),
                         ("ocr_runs", f"submission_id={sub_ids[0]}")]:
        assert db.q1(conn, f"SELECT COUNT(*) c FROM {table} WHERE {where}")["c"] == 0, table
    # чужие данные на месте
    assert db.q1(conn, "SELECT COUNT(*) c FROM observations WHERE student_id=2")["c"] == before_other
    assert db.q1(conn, "SELECT COUNT(*) c FROM practice_items")["c"] == 1
    assert db.q1(conn, "SELECT COUNT(*) c FROM review_marks")["c"] == 1
    # аккаунт ученика отвязан и отключён; в аудите — только факт и id
    u = auth.get_user(conn, user_id)
    assert u["student_id"] is None and u["disabled"]
    last = audit.recent(conn, 1)[0]
    assert (last["action"], last["target_type"], last["target_id"]) == ("delete_student", "student", sid)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # портреты остальных строятся как раньше
    assert portrait.build(conn, 2, "ru")["n_works"] == 6


def test_live_demo_still_works_after_delete_of_other(conn):
    privacy.delete_student(conn, 8)
    aid = seed.live_assignment_id(conn)
    answers = {pr["id"]: seed.LIVE_DEMO_TEXT[pr["idx"]].split("\n") for pr in pipeline.problems_of(conn, aid)}
    pipeline.record_submission(conn, 1, aid, answers, "2026-09-24 08:00:00", source="live")
    lost = next(e for e in portrait.build(conn, 1, "ru")["errors"] if e["tag"] == "lost_root")
    assert (lost["count"], lost["total"]) == (5, 7)


# ---------------------------------------------------------------- выгрузка

def test_export_class_csv(conn):
    data = privacy.export_class_csv(conn, "ru", {1, 2})
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 с BOM — для Excel
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0] == privacy.export_columns("ru")
    assert rows[0][0] == "Псевдоним" and "Главная рекомендация" in rows[0] and "Опоздания" in rows[0]
    assert any("Квадратные: ошибок / работ" == c for c in rows[0])
    assert [r[0] for r in rows[1:]] == ["Айгерим", "Данияр"]
    assert all(len(r) == len(rows[0]) for r in rows)
    aig = dict(zip(rows[0], rows[1]))
    assert aig["Класс"] == seed.CLASS_NAME and aig["Квадратные: ошибок / работ"].endswith("/ 6")
    assert "Потеря корня" in aig["Главная рекомендация"]
    kk = privacy.export_class_csv(conn, "kk", None).decode("utf-8-sig").splitlines()
    assert kk[0].startswith("Бүркеншік ат") and len(kk) == 1 + len(seed.STUDENTS)
    assert privacy.export_class_csv(conn, "ru", set()).decode("utf-8-sig").count("\n") == 1


def test_export_escapes_formulas(conn):
    conn.execute("INSERT INTO students (alias, class_name) VALUES ('=HYPERLINK(\"x\")', '9 «Б»')")
    data = privacy.export_class_csv(conn, "ru", None).decode("utf-8-sig")
    assert "'=HYPERLINK" in data


# ---------------------------------------------------------------- этап B: классы и задания

def test_students_csv_import(conn):
    from core import roster
    rows, errors, warnings = roster.parse_students_csv("﻿alias;class_name\nЛис;9 «Б»\nСокол;9 «Б»\n\nИванов Иван;9 «Б»\n".encode())
    assert not errors and [r["alias"] for r in rows] == ["Лис", "Сокол", "Иванов Иван"]
    assert len(warnings) == 1  # похоже на ФИО — предупреждение
    ids = roster.import_students(conn, rows)
    assert len(ids) == 3 and "9 «Б»" in roster.list_classes(conn)
    assert roster.existing_duplicates(conn, rows) == rows
    with pytest.raises(ValueError):
        roster.import_students(conn, rows[:1])
    # заголовки, дубликаты, формулы
    assert roster.parse_students_csv(b"name,class\nA,1")[1]
    _, errs, _ = roster.parse_students_csv("alias,class_name\nА,1\nа,1\n=cmd(),1\nБ\n")
    assert len(errs) == 3
    roster.create_class(conn, "10 «В»")
    assert "10 «В»" in roster.list_classes(conn)
    t = auth.create_user(conn, "t9", "Учитель", "teacher", "pass-nine-9")
    roster.set_class_teachers(conn, "9 «Б»", [t])
    assert auth.visible_student_ids(conn, auth.get_user(conn, t)) == set(ids)


def test_create_assignment_validates_reference(conn):
    from core import roster
    good = [{"statement": "x² − 5x + 6 = 0", "kind": "equation", "skill": "quadratic",
             "reference": ["D = 25 − 24 = 1", "x₁ = (5 + 1)/2 = 3", "x₂ = (5 − 1)/2 = 2"], "answer": "2; 3"},
            {"statement": "(a + 3)² − 6a", "kind": "expression", "skill": "simplify",
             "reference": ["a² + 6a + 9 − 6a", "a² + 9"], "answer": "a² + 9"}]
    assert all(roster.validate_problem(p) == [] for p in good)
    bad_ref = dict(good[0], reference=["D = 25 − 24 = 1", "x₁ = (5 + 1)/2 = 4", "x₂ = (5 − 1)/2 = 2"], answer="2; 4")
    bad_ans = dict(good[0], answer="2; 5")
    bad_stmt = dict(good[0], statement="x² − 5x + 6")
    bad_expr = dict(good[1], reference=["a² + 6a + 9 − 6a", "a² + 3"], answer="a² + 3")
    for p in (bad_ref, bad_ans, bad_stmt, bad_expr, dict(good[0], skill="nope"), dict(good[0], reference=[])):
        assert roster.validate_problem(p), p
    n_before = db.q1(conn, "SELECT COUNT(*) c FROM assignments")["c"]
    with pytest.raises(ValueError) as ex:
        roster.create_assignment(conn, "ДЗ", "2026-10-01 23:59:00", [good[0], bad_ans])
    assert any("№2" in e["ru"] for e in ex.value.args[0])
    assert db.q1(conn, "SELECT COUNT(*) c FROM assignments")["c"] == n_before  # не сохранено
    live = seed.live_assignment_id(conn)
    number = roster.next_number(conn)
    assert number == db.q1(conn, "SELECT MAX(number) m FROM assignments")["m"] + 1
    aid = roster.create_assignment(conn, f"ДЗ №{number}", "2026-10-01 23:59:00", good)
    assert db.q1(conn, "SELECT number FROM assignments WHERE id=?", (aid,))["number"] == number
    assert len(pipeline.problems_of(conn, aid)) == 2
    assert seed.live_assignment_id(conn) == live  # живое демо — по-прежнему ДЗ №7
    assert db.q1(conn, "SELECT number FROM assignments WHERE id=?", (live,))["number"] == len(seed.HOMEWORKS) + 1
    # работа по новому заданию проверяется как обычно
    first = pipeline.problems_of(conn, aid)[0]
    res = pipeline.run_checks(conn, aid, {first["id"]: ["(x − 2)(x − 3) = 0", "x = 2 или x = 3"]})
    assert res[first["id"]].correct and not res[first["id"]].first_error

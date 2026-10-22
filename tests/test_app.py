import io
import sqlite3

import pymupdf
import pytest


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import app as A
    A = importlib.reload(A)
    A.app.config["TESTING"] = True
    return A


def post(client, url, data=None, **kw):
    with client.session_transaction() as s:
        token = s.setdefault("csrf", "test")
    return client.post(url, data={**(data or {}), "csrf": token}, **kw)


def login(client, username, password):
    return post(client, "/login", {"username": username, "password": password})


def sample_pdf():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Payvandlash asoslari")
    return io.BytesIO(doc.tobytes())


def test_full_flow(app_module):
    A = app_module
    admin = A.app.test_client()
    login(admin, "admin", "admin123")
    assert "/parol" in admin.get("/").headers["Location"]  # birinchi kirishda parolni almashtirish
    post(admin, "/parol", {"old": "admin123", "new": "adminpass", "new2": "adminpass"})
    post(admin, "/admin/kafedralar", {"kind": "department", "name": "Mashinasozlik"})
    post(admin, "/admin/kafedralar", {"kind": "specialty", "name": "Payvandlash", "department": "1"})
    post(admin, "/admin/foydalanuvchilar",
         {"full_name": "Usta", "username": "usta", "password": "usta123", "role": "teacher"})
    rows = "login;F.I.Sh.;rol\nali;Ali Valiyev;o'quvchi\nvali;Vali Aliyev;student\n"
    r = post(admin, "/admin/import", {"file": (io.BytesIO(rows.encode()), "u.csv")},
             content_type="multipart/form-data")
    assert r.text.count("<code>") == 2

    teacher = A.app.test_client()
    login(teacher, "usta", "usta123")
    r = post(teacher, "/yuklash", {"title": "Payvandlash asoslari", "department": "1",
                                   "new_specialty": "Yangi yo'nalish", "file": (sample_pdf(), "dars.pdf")},
             content_type="multipart/form-data")
    rid = int(r.headers["Location"].rsplit("/", 1)[1])
    assert teacher.get(f"/resurs/{rid}/rasm").status_code == 200  # birinchi bet rasmi

    student = A.app.test_client()
    db = sqlite3.connect(A.DB_PATH)
    db.execute("UPDATE users SET password_hash = ? WHERE username = 'ali'", (A.generate_password_hash("ali123"),))
    db.commit()
    login(student, "ali", "ali123")
    assert "Payvandlash asoslari" in student.get("/?department=1&teacher=2").text
    post(student, f"/resurs/{rid}/baho", {"value": "4"})
    post(student, f"/resurs/{rid}/like")
    post(student, f"/resurs/{rid}/izoh", {"text": "Birinchi"})
    post(student, f"/resurs/{rid}/izoh", {"text": "Ikkinchi"})
    assert student.get("/admin/statistika").status_code == 403

    counted = db.execute("SELECT text, counted FROM comments ORDER BY id").fetchall()
    assert counted == [("Birinchi", 1), ("Ikkinchi", 0)]  # bitta akkauntdan bitta izoh hisobga o'tadi
    score = db.execute(A.SCORE_SQL + " WHERE r.id = ?", (rid,)).fetchone()[-1]
    assert score == 4 * A.SCORE_RATING_WEIGHT + A.SCORE_LIKE + A.SCORE_COMMENT
    assert db.execute("SELECT approved FROM specialties WHERE name = 'Yangi yo''nalish'").fetchone() == (0,)

    stats = admin.get("/admin/statistika").text
    assert "Ali Valiyev" in stats and "Usta" in stats
    assert "Usta" in admin.get("/oqituvchilar").text

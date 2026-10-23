import io
import re
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
    assert "Payvandlash asoslari" in student.get("/resurslar?department=1&teacher=2").text
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


def test_landing_and_registration(app_module):
    A = app_module
    c = A.app.test_client()
    page = c.get("/").text
    assert "Ro'yxatdan o'tish" in page and "Om qonuni" in page
    r = post(c, "/royxatdan-otish", {"role": "student", "full_name": "Rahimov Azizbek", "username": "aziz",
                                     "group_name": "MS-21", "password": "secret1", "password2": "secret1"})
    assert r.headers["Location"].endswith("/profil")
    assert "Rahimov Azizbek" in c.get("/profil").text
    assert c.get("/").headers["Location"].endswith("/resurslar")  # kirgan foydalanuvchi landingni ko'rmaydi
    db = sqlite3.connect(A.DB_PATH)
    assert db.execute("SELECT role FROM users WHERE username = 'aziz'").fetchone() == ("student",)
    # band login va zaif parol rad etiladi
    other = A.app.test_client()
    r = post(other, "/royxatdan-otish", {"role": "user", "full_name": "Boshqa Odam", "username": "AZIZ",
                                         "password": "secret1", "password2": "secret1"}, follow_redirects=True)
    assert "band" in r.text
    assert db.execute("SELECT COUNT(*) FROM users WHERE role = 'student'").fetchone() == (1,)


def test_telegram_link_and_password_reset(app_module, monkeypatch):
    A = app_module
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    monkeypatch.setattr(A.telegram, "bot_username", lambda: "bilim_test_bot")
    monkeypatch.setattr(A.telegram, "send_message", lambda chat_id, text: sent.append((chat_id, text)))
    c = A.app.test_client()
    post(c, "/royxatdan-otish", {"role": "user", "full_name": "Olimova Madina", "username": "madina",
                                 "password": "secret1", "password2": "secret1"})
    r = post(c, "/profil/telegram")
    token = r.headers["Location"].split("start=")[1]
    assert r.headers["Location"].startswith("https://t.me/bilim_test_bot?start=")
    assert A.link_telegram("noto'g'ri", 777) is None
    assert A.link_telegram(token, 777) == "Olimova Madina"
    assert A.link_telegram(token, 777) is None  # havola bir martalik
    post(c, "/logout")

    anon = A.app.test_client()
    r = post(anon, "/parolni-tiklash", {"username": "madina"})
    assert r.headers["Location"].endswith("/parolni-tiklash/kod")
    chat_id, text = sent[-1]
    code = re.search(r"<b>(\d{6})</b>", text).group(1)
    assert chat_id == 777
    r = post(anon, "/parolni-tiklash/kod", {"code": "000000" if code != "000000" else "111111",
                                            "new": "newpass1", "new2": "newpass1"}, follow_redirects=True)
    assert "Kod noto" in r.text
    r = post(anon, "/parolni-tiklash/kod", {"code": code, "new": "newpass1", "new2": "newpass1"})
    assert r.headers["Location"].endswith("/login")
    assert login(anon, "madina", "newpass1").headers["Location"].endswith("/resurslar")


def test_register_roles_groups_and_dashboard(app_module):
    A = app_module
    admin = A.app.test_client()
    db = sqlite3.connect(A.DB_PATH)
    db.execute("UPDATE users SET must_change_password = 0 WHERE username = 'admin'")
    db.commit()
    assert login(admin, "admin", "admin123").headers["Location"].endswith("/admin")
    post(admin, "/admin/kafedralar", {"kind": "group", "name": "AT-22"})
    gid = db.execute("SELECT id FROM groups WHERE name = 'AT-22'").fetchone()[0]

    c = A.app.test_client()
    page = c.get("/royxatdan-otish").text
    assert "AT-22" in page and "Foydalanuvchi" in page
    # guruh ro'yxati bor bo'lsa, o'quvchi guruhni tanlashi shart
    r = post(c, "/royxatdan-otish", {"role": "student", "full_name": "Karimov Sardor", "username": "sardor",
                                     "password": "secret1", "password2": "secret1"}, follow_redirects=True)
    assert "Guruhingizni tanlang" in r.text
    post(c, "/royxatdan-otish", {"role": "student", "full_name": "Karimov Sardor", "username": "sardor",
                                 "group_id": str(gid), "password": "secret1", "password2": "secret1"})
    g = A.app.test_client()
    post(g, "/royxatdan-otish", {"role": "user", "full_name": "Tashqi Mehmon", "username": "mehmon",
                                 "group_id": str(gid), "password": "secret1", "password2": "secret1"})
    rows = db.execute("SELECT username, role, group_name FROM users WHERE username IN ('sardor', 'mehmon')"
                      " ORDER BY username").fetchall()
    assert rows == [("mehmon", "user", None), ("sardor", "student", "AT-22")]
    r = post(c, "/royxatdan-otish", {"role": "admin", "full_name": "Xaker Xakerov", "username": "xaker",
                                     "password": "secret1", "password2": "secret1"})
    assert db.execute("SELECT 1 FROM users WHERE username = 'xaker'").fetchone() is None

    dash = admin.get("/admin")
    assert dash.status_code == 200 and "Tashqi Mehmon" in dash.text and "Boshqaruv paneli" in dash.text
    assert g.get("/admin").status_code == 403
    # guruh nomi o'zgarsa, o'quvchida ham yangilanadi
    post(admin, f"/admin/guruh/{gid}/saqlash", {"name": "AT-23"})
    assert db.execute("SELECT group_name FROM users WHERE username = 'sardor'").fetchone() == ("AT-23",)


def test_migrates_old_database(tmp_path, monkeypatch):
    old = sqlite3.connect(tmp_path / "texnikum.db")
    old.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,"
                " full_name TEXT NOT NULL, role TEXT NOT NULL, department_id INTEGER, group_name TEXT,"
                " is_active INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 0,"
                " created_at TEXT NOT NULL)")
    old.execute("INSERT INTO users (username, password_hash, full_name, role, group_name, created_at)"
                " VALUES ('admin', 'x', 'Admin', 'admin', 'MS-21', '2026-01-01')")
    # boshqa jadvallar users'ga bog'langan: qayta yaratishda ular o'chib ketmasligi kerak
    old.execute("PRAGMA foreign_keys = ON")
    old.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY,"
                " user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE)")
    old.execute("INSERT INTO notes (user_id) VALUES (1)")
    old.commit()
    old.close()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import app as A
    importlib.reload(A)
    cols = {r[1] for r in sqlite3.connect(A.DB_PATH).execute("PRAGMA table_info(users)")}
    assert {"telegram_chat_id", "telegram_link_token"} <= cols
    db = sqlite3.connect(A.DB_PATH)
    assert db.execute("SELECT COUNT(*) FROM users").fetchone() == (1,)
    assert db.execute("SELECT COUNT(*) FROM notes").fetchone() == (1,)
    assert db.execute("SELECT name FROM groups").fetchall() == [("MS-21",)]
    db.execute("INSERT INTO users (username, password_hash, full_name, role, created_at)"
               " VALUES ('mehmon', 'x', 'Mehmon', 'user', '2026-01-01')")  # yangi rol qabul qilinadi

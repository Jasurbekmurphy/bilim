"""Texnikum bilimlar manbai — pedagoglar resurslari platformasi."""
import csv
import io
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import telegram

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
DB_PATH = os.path.join(DATA_DIR, "texnikum.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
PREVIEW_DIR = os.path.join(DATA_DIR, "previews")

ALLOWED_EXT = {"pdf", "ppt", "pptx", "doc", "docx", "xls", "xlsx", "odp", "odt",
               "ods", "rtf", "txt", "jpg", "jpeg", "png"}
OFFICE_EXT = {"ppt", "pptx", "doc", "docx", "xls", "xlsx", "odp", "odt", "ods", "rtf", "txt"}
IMAGE_EXT = {"jpg", "jpeg", "png"}

# Reyting formulasi: o'rtacha baho * 10 + har bir like + har bir hisobga o'tgan izoh
SCORE_RATING_WEIGHT = 10
SCORE_LIKE = 1.0
SCORE_COMMENT = 0.5
# Shu vaqtdan ko'p harakatsiz qolsa, sessiya tugagan deb hisoblanadi
IDLE_TIMEOUT = timedelta(minutes=30)
ROLE_NAMES = {"admin": "Administrator", "teacher": "O'qituvchi", "student": "O'quvchi",
              "user": "Foydalanuvchi"}
# O'zi ro'yxatdan o'tganda tanlash mumkin bo'lgan rollar
SELF_ROLES = ("user", "student")
# O'quvchilar o'zi ro'yxatdan o'tadi; o'qituvchilarga login/parolni admin beradi
ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "1") != "0"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,30}$")
RESET_CODE_TTL = timedelta(minutes=10)
RESET_MAX_ATTEMPTS = 5
TELEGRAM_LINK_TTL = timedelta(minutes=30)



def load_secret_key():
    """Kalit faylda saqlanadi, aks holda har qayta ishga tushganda hamma tizimdan chiqib ketadi."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(path) as fh:
        return fh.read().strip()


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024
app.jinja_env.globals["ROLE_NAMES"] = ROLE_NAMES
app.jinja_env.globals["allow_registration"] = ALLOW_REGISTRATION
app.jinja_env.globals["now_year"] = lambda: datetime.now().year

SCHEMA = """
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS specialties (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    approved INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    UNIQUE (name, department_id)
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'teacher', 'student', 'user')),
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    group_name TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    stored_name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    ext TEXT NOT NULL,
    pdf_name TEXT,
    preview_name TEXT,
    teacher_id INTEGER NOT NULL REFERENCES users(id),
    department_id INTEGER NOT NULL REFERENCES departments(id),
    specialty_id INTEGER REFERENCES specialties(id) ON DELETE SET NULL,
    views INTEGER NOT NULL DEFAULT 0,
    downloads INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ratings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    value INTEGER NOT NULL CHECK (value BETWEEN 1 AND 5),
    PRIMARY KEY (user_id, resource_id)
);
CREATE TABLE IF NOT EXISTS likes (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, resource_id)
);
CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, resource_id)
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    counted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    login_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    logout_at TEXT,
    ip TEXT
);
CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_res_dep ON resources(department_id, specialty_id);
CREATE INDEX IF NOT EXISTS idx_res_teacher ON resources(teacher_id);
CREATE INDEX IF NOT EXISTS idx_sess_user ON login_sessions(user_id);
"""

# Har bir resurs uchun reyting balli (yashirilmaganlar)
SCORE_SQL = f"""
SELECT r.id AS resource_id,
       COALESCE((SELECT AVG(value) FROM ratings WHERE resource_id = r.id), 0) AS avg_rating,
       (SELECT COUNT(*) FROM ratings WHERE resource_id = r.id) AS rating_count,
       (SELECT COUNT(*) FROM likes WHERE resource_id = r.id) AS like_count,
       (SELECT COUNT(*) FROM comments WHERE resource_id = r.id) AS comment_count,
       (SELECT COUNT(*) FROM comments WHERE resource_id = r.id AND counted = 1) AS counted_comments,
       COALESCE((SELECT AVG(value) FROM ratings WHERE resource_id = r.id), 0) * {SCORE_RATING_WEIGHT}
         + (SELECT COUNT(*) FROM likes WHERE resource_id = r.id) * {SCORE_LIKE}
         + (SELECT COUNT(*) FROM comments WHERE resource_id = r.id AND counted = 1) * {SCORE_COMMENT}
         AS score
FROM resources r
"""


def now():
    return datetime.now().replace(microsecond=0)


def now_str():
    return now().isoformat(sep=" ")


def parse_dt(value):
    return datetime.fromisoformat(value) if value else None


# ---------------------------------------------------------------- DB

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def migrate(db):
    """Eski bazaga yangi ustunlarni qo'shadi; mavjud ma'lumotlar saqlanadi."""
    users_sql = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()[0]
    if "'user'" not in users_sql:
        # SQLite CHECK cheklovini o'zgartirib bo'lmaydi: jadval yangi cheklov bilan qayta yaratiladi.
        # Ulanishda foreign_keys o'chiq, shuning uchun DROP bog'langan yozuvlarni o'chirmaydi.
        new_sql = users_sql.replace("'student')", "'student', 'user')").replace(
            "CREATE TABLE users", "CREATE TABLE users_new", 1)
        cols = ", ".join(row[1] for row in db.execute("PRAGMA table_info(users)"))
        db.execute("BEGIN")
        db.execute(new_sql)
        db.execute(f"INSERT INTO users_new ({cols}) SELECT {cols} FROM users")
        db.execute("DROP TABLE users")
        db.execute("ALTER TABLE users_new RENAME TO users")
        db.execute("COMMIT")
    cols = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    for name, decl in (("telegram_chat_id", "INTEGER"), ("telegram_link_token", "TEXT"),
                       ("telegram_link_expires", "TEXT")):
        if name not in cols:
            try:
                db.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError:
                pass  # boshqa worker allaqachon qo'shgan


def init_db():
    for d in (UPLOAD_DIR, PREVIEW_DIR):
        os.makedirs(d, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    had_groups = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'groups'").fetchone()
    db.executescript(SCHEMA)
    migrate(db)
    if not had_groups:
        # Avval qo'lda yozilgan guruh nomlari ro'yxatga aylanadi
        db.execute("INSERT OR IGNORE INTO groups (name) SELECT DISTINCT trim(group_name) FROM users"
                   " WHERE trim(COALESCE(group_name, '')) != ''")
    if not db.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
        password = os.environ.get("ADMIN_PASSWORD", "admin123")
        # OR IGNORE: bir nechta gunicorn worker bir vaqtda ishga tushganda xato bermasligi uchun
        cur = db.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, full_name, role, must_change_password, created_at)"
            " VALUES ('admin', ?, 'Administrator', 'admin', 1, ?)",
            (generate_password_hash(password), now_str()))
        if cur.rowcount:
            print(f"Admin yaratildi: login 'admin', parol '{password}' (birinchi kirishda o'zgartiriladi)")
    db.commit()
    db.close()


# ---------------------------------------------------------------- auth

def current_user():
    if "user" not in g:
        g.user = None
        uid = session.get("user_id")
        if uid:
            g.user = get_db().execute(
                "SELECT * FROM users WHERE id = ? AND is_active = 1", (uid,)).fetchone()
    return g.user


app.jinja_env.globals["current_user"] = current_user


def login_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("login", next=request.path))
            if roles and user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def before_request():
    if request.method == "POST" and request.endpoint != "static":
        if request.form.get("csrf") != session.get("csrf"):
            abort(400, "CSRF token noto'g'ri. Sahifani yangilab, qaytadan urinib ko'ring.")
    user = current_user()
    if session.get("user_id") and user is None:
        session.clear()  # o'chirilgan yoki bloklangan foydalanuvchi
        return
    if user is None:
        return
    touch_session()
    if user["must_change_password"] and request.endpoint not in ("change_password", "logout", "static"):
        flash("Xavfsizlik uchun avval parolingizni o'zgartiring.", "warning")
        return redirect(url_for("change_password"))


def touch_session():
    """Faol sessiyaning oxirgi faollik vaqtini yangilaydi (davomiylikni hisoblash uchun)."""
    sid = session.get("login_session_id")
    db = get_db()
    row = db.execute("SELECT * FROM login_sessions WHERE id = ?", (sid,)).fetchone() if sid else None
    t = now()
    if row is None or row["logout_at"] or t - parse_dt(row["last_seen"]) > IDLE_TIMEOUT:
        # Uzoq harakatsizlikdan keyin qaytgan foydalanuvchi uchun yangi kirish yoziladi
        cur = db.execute(
            "INSERT INTO login_sessions (user_id, login_at, last_seen, ip) VALUES (?, ?, ?, ?)",
            (current_user()["id"], t.isoformat(sep=" "), t.isoformat(sep=" "), client_ip()))
        session["login_session_id"] = cur.lastrowid
    elif t - parse_dt(row["last_seen"]) > timedelta(seconds=30):
        db.execute("UPDATE login_sessions SET last_seen = ? WHERE id = ?", (t.isoformat(sep=" "), sid))
    db.commit()


def client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def start_session(user_id):
    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    t = now_str()
    db = get_db()
    cur = db.execute("INSERT INTO login_sessions (user_id, login_at, last_seen, ip) VALUES (?, ?, ?, ?)",
                     (user_id, t, t, client_ip()))
    db.commit()
    session["login_session_id"] = cur.lastrowid


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?",
                          (request.form.get("username", "").strip(),)).fetchone()
        if user and user["is_active"] and check_password_hash(user["password_hash"], request.form.get("password", "")):
            start_session(user["id"])
            nxt = request.args.get("next", "")
            home = url_for("admin_dashboard") if user["role"] == "admin" else url_for("index")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else home)
        flash("Login yoki parol noto'g'ri, yoki akkaunt bloklangan.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    sid = session.get("login_session_id")
    if sid:
        db = get_db()
        t = now_str()
        db.execute("UPDATE login_sessions SET logout_at = ?, last_seen = ? WHERE id = ? AND logout_at IS NULL",
                   (t, t, sid))
        db.commit()
    session.clear()
    return redirect(url_for("landing"))


@app.route("/parol", methods=["GET", "POST"])
@login_required()
def change_password():
    user = current_user()
    if request.method == "POST":
        old, new, new2 = (request.form.get(k, "") for k in ("old", "new", "new2"))
        if not check_password_hash(user["password_hash"], old):
            flash("Joriy parol noto'g'ri.", "error")
        elif len(new) < 6:
            flash("Yangi parol kamida 6 belgidan iborat bo'lsin.", "error")
        elif new != new2:
            flash("Yangi parollar mos kelmadi.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                       (generate_password_hash(new), user["id"]))
            db.commit()
            flash("Parol o'zgartirildi.", "success")
            return redirect(url_for("index"))
    return render_template("change_password.html")


@app.route("/royxatdan-otish", methods=["GET", "POST"])
def register():
    if not ALLOW_REGISTRATION:
        abort(404)
    if current_user():
        return redirect(url_for("index"))
    form = request.form
    if request.method == "POST":
        db = get_db()
        username = form.get("username", "").strip()
        full_name = " ".join(form.get("full_name", "").split())
        password = form.get("password", "")
        role = form.get("role")
        dep = (form.get("department") or None) if role == "student" else None
        group = None
        if role == "student":
            gid = form.get("group_id")
            if gid:
                row = db.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
                group = row["name"] if row else None
            elif not groups():
                group = form.get("group_name", "").strip()[:30] or None
        if role not in SELF_ROLES:
            flash("Kim sifatida ro'yxatdan o'tishingizni tanlang.", "error")
        elif len(full_name) < 5:
            flash("Familiya va ismingizni to'liq yozing.", "error")
        elif role == "student" and not group:
            flash("Guruhingizni tanlang.", "error")
        elif not USERNAME_RE.match(username):
            flash("Login 3–30 belgi bo'lsin: lotin harflari, raqamlar, nuqta yoki pastki chiziq.", "error")
        elif db.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone():
            flash(f"'{username}' logini band, boshqasini tanlang.", "error")
        elif len(password) < 6:
            flash("Parol kamida 6 belgidan iborat bo'lsin.", "error")
        elif password != form.get("password2", ""):
            flash("Parollar mos kelmadi.", "error")
        elif dep and not db.execute("SELECT 1 FROM departments WHERE id = ?", (dep,)).fetchone():
            flash("Kafedra topilmadi.", "error")
        else:
            cur = db.execute(
                "INSERT INTO users (username, password_hash, full_name, role, department_id, group_name, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, generate_password_hash(password), full_name, role, dep, group, now_str()))
            db.commit()
            start_session(cur.lastrowid)
            flash("Xush kelibsiz! Parolni unutib qo'ysangiz tiklay olishingiz uchun Telegramni ulab qo'ying.",
                  "success")
            return redirect(url_for("profile"))
    return render_template("register.html", departments=departments(), groups=groups(), form=form)


@app.route("/profil")
@login_required()
def profile():
    user = current_user()
    dep = get_db().execute("SELECT name FROM departments WHERE id = ?", (user["department_id"],)).fetchone()
    return render_template("profile.html", user=user, department=dep["name"] if dep else None,
                           telegram_enabled=telegram.enabled())


@app.route("/profil/telegram", methods=["POST"])
@login_required()
def telegram_link():
    bot = telegram.bot_username()
    if not bot:
        flash("Telegram bot hozircha sozlanmagan. Administratorga murojaat qiling.", "error")
        return redirect(url_for("profile"))
    token = secrets.token_urlsafe(16)
    db = get_db()
    db.execute("UPDATE users SET telegram_link_token = ?, telegram_link_expires = ? WHERE id = ?",
               (token, (now() + TELEGRAM_LINK_TTL).isoformat(sep=" "), current_user()["id"]))
    db.commit()
    return redirect(f"https://t.me/{bot}?start={token}")


@app.route("/profil/telegram/uzish", methods=["POST"])
@login_required()
def telegram_unlink():
    db = get_db()
    db.execute("UPDATE users SET telegram_chat_id = NULL WHERE id = ?", (current_user()["id"],))
    db.commit()
    flash("Telegram uzildi.", "success")
    return redirect(url_for("profile"))


def link_telegram(token, chat_id):
    """Botga /start <token> kelganda chaqiriladi. Ulangan foydalanuvchi ismini qaytaradi."""
    db = sqlite3.connect(DB_PATH)
    try:
        row = db.execute("SELECT id, full_name, telegram_link_expires FROM users"
                         " WHERE telegram_link_token = ? AND is_active = 1", (token,)).fetchone()
        if not row or parse_dt(row[2]) < now():
            return None
        # Bitta Telegram bitta akkauntga ulanadi
        db.execute("UPDATE users SET telegram_chat_id = NULL WHERE telegram_chat_id = ?", (chat_id,))
        db.execute("UPDATE users SET telegram_chat_id = ?, telegram_link_token = NULL,"
                   " telegram_link_expires = NULL WHERE id = ?", (chat_id, row[0]))
        db.commit()
        return row[1]
    finally:
        db.close()


@app.route("/parolni-tiklash", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE AND is_active = 1",
                          (request.form.get("username", "").strip(),)).fetchone()
        if not user:
            flash("Bunday login topilmadi.", "error")
        elif not (user["telegram_chat_id"] and telegram.enabled()):
            flash("Bu akkaunt Telegramga ulanmagan. Parolni tiklash uchun administratorga murojaat qiling.",
                  "warning")
        else:
            last = db.execute("SELECT created_at FROM password_resets WHERE user_id = ?"
                              " ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
            if last and now() - parse_dt(last["created_at"]) < timedelta(minutes=1):
                flash("Kod yaqinda yuborildi. Telegramni tekshiring yoki bir daqiqadan keyin qayta urining.",
                      "warning")
                session["reset_user_id"] = user["id"]
                return redirect(url_for("reset_password"))
            code = f"{secrets.randbelow(10 ** 6):06d}"
            try:
                telegram.send_message(user["telegram_chat_id"],
                                      f"🔐 Parolni tiklash kodi: <b>{code}</b>\n\n"
                                      f"Kod {int(RESET_CODE_TTL.total_seconds() // 60)} daqiqa amal qiladi. "
                                      "Agar buni siz so'ramagan bo'lsangiz, xabarga e'tibor bermang.")
            except Exception as exc:
                app.logger.warning("Telegram xabari yuborilmadi: %s", exc)
                flash("Telegramga xabar yuborib bo'lmadi. Keyinroq urining yoki administratorga murojaat qiling.",
                      "error")
                return render_template("forgot_password.html")
            db.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
            db.execute("INSERT INTO password_resets (user_id, code_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
                       (user["id"], generate_password_hash(code), now_str(),
                        (now() + RESET_CODE_TTL).isoformat(sep=" ")))
            db.commit()
            session["reset_user_id"] = user["id"]
            flash("Tasdiqlash kodi Telegramingizga yuborildi.", "success")
            return redirect(url_for("reset_password"))
    return render_template("forgot_password.html")


@app.route("/parolni-tiklash/kod", methods=["GET", "POST"])
def reset_password():
    uid = session.get("reset_user_id")
    if not uid:
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        db = get_db()
        row = db.execute("SELECT * FROM password_resets WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                         (uid,)).fetchone()
        new = request.form.get("new", "")
        if not row or parse_dt(row["expires_at"]) < now() or row["attempts"] >= RESET_MAX_ATTEMPTS:
            flash("Kod eskirgan yoki urinishlar tugagan. Yangi kod so'rang.", "error")
            return redirect(url_for("forgot_password"))
        if not check_password_hash(row["code_hash"], request.form.get("code", "").strip()):
            db.execute("UPDATE password_resets SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            db.commit()
            flash("Kod noto'g'ri.", "error")
        elif len(new) < 6:
            flash("Yangi parol kamida 6 belgidan iborat bo'lsin.", "error")
        elif new != request.form.get("new2", ""):
            flash("Yangi parollar mos kelmadi.", "error")
        else:
            db.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                       (generate_password_hash(new), uid))
            db.execute("DELETE FROM password_resets WHERE user_id = ?", (uid,))
            db.commit()
            session.pop("reset_user_id", None)
            flash("Parol yangilandi. Yangi parol bilan kiring.", "success")
            return redirect(url_for("login"))
    return render_template("reset_password.html")


# ---------------------------------------------------------------- resources

def departments():
    return get_db().execute("SELECT * FROM departments ORDER BY name").fetchall()


def groups():
    return get_db().execute("SELECT * FROM groups ORDER BY name").fetchall()


def specialties(include_unapproved_by=None):
    sql = ("SELECT s.*, d.name AS department_name FROM specialties s "
           "JOIN departments d ON d.id = s.department_id WHERE s.approved = 1")
    params = []
    if include_unapproved_by:
        sql += " OR s.created_by = ?"
        params.append(include_unapproved_by)
    return get_db().execute(sql + " ORDER BY s.name", params).fetchall()


def teachers():
    return get_db().execute(
        "SELECT id, full_name FROM users WHERE role = 'teacher' AND is_active = 1 ORDER BY full_name").fetchall()


@app.route("/")
def landing():
    if current_user():
        return redirect(url_for("index"))
    db = get_db()
    stats = db.execute(
        """SELECT (SELECT COUNT(*) FROM resources WHERE hidden = 0) AS resources,
                  (SELECT COUNT(*) FROM users WHERE role = 'teacher' AND is_active = 1) AS teachers,
                  (SELECT COUNT(*) FROM users WHERE role = 'student' AND is_active = 1) AS students,
                  (SELECT COUNT(*) FROM departments) AS departments,
                  (SELECT COALESCE(SUM(downloads), 0) FROM resources) AS downloads""").fetchone()
    deps = db.execute(
        "SELECT d.name, (SELECT COUNT(*) FROM resources r WHERE r.department_id = d.id AND r.hidden = 0) AS n,"
        " (SELECT COUNT(*) FROM specialties s WHERE s.department_id = d.id AND s.approved = 1) AS specs"
        " FROM departments d ORDER BY n DESC, d.name LIMIT 8").fetchall()
    return render_template("landing.html", stats=stats, deps=deps)


@app.route("/resurslar")
@login_required()
def index():
    args = request.args
    where, params = ["r.hidden = 0"], []
    if args.get("department"):
        where.append("r.department_id = ?")
        params.append(args["department"])
    if args.get("specialty"):
        where.append("r.specialty_id = ?")
        params.append(args["specialty"])
    if args.get("teacher"):
        where.append("r.teacher_id = ?")
        params.append(args["teacher"])
    if args.get("q"):
        where.append("(r.title LIKE ? OR r.description LIKE ?)")
        params += [f"%{args['q']}%"] * 2
    if args.get("saved"):
        where.append("r.id IN (SELECT resource_id FROM favorites WHERE user_id = ?)")
        params.append(current_user()["id"])
    order = {"top": "s.score DESC", "popular": "r.views + r.downloads DESC",
             "new": "r.created_at DESC"}.get(args.get("sort"), "r.created_at DESC")
    rows = get_db().execute(f"""
        SELECT r.*, u.full_name AS teacher_name, d.name AS department_name, sp.name AS specialty_name,
               s.avg_rating, s.rating_count, s.like_count, s.comment_count, s.score
        FROM resources r
        JOIN users u ON u.id = r.teacher_id
        JOIN departments d ON d.id = r.department_id
        LEFT JOIN specialties sp ON sp.id = r.specialty_id
        JOIN ({SCORE_SQL}) s ON s.resource_id = r.id
        WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 200""", params).fetchall()
    return render_template("index.html", resources=rows, departments=departments(),
                           specialties=specialties(), teachers=teachers())


def make_preview(path, ext, stem):
    """Faylning birinchi betini PNG rasmga aylantiradi. (pdf_name, preview_name) qaytaradi."""
    import pymupdf

    pdf_name = None
    pdf_path = path if ext == "pdf" else None
    if ext in OFFICE_EXT and shutil.which("soffice"):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                subprocess.run(
                    ["soffice", f"-env:UserInstallation=file://{tmp}/profile", "--headless",
                     "--convert-to", "pdf", "--outdir", tmp, path],
                    check=True, timeout=180, capture_output=True)
                produced = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".pdf")
                if os.path.exists(produced):
                    pdf_name = stem + ".pdf"
                    shutil.move(produced, os.path.join(PREVIEW_DIR, pdf_name))
                    pdf_path = os.path.join(PREVIEW_DIR, pdf_name)
            except (subprocess.SubprocessError, OSError) as exc:
                app.logger.warning("LibreOffice konvertatsiyasi bajarilmadi: %s", exc)
    source = path if ext in IMAGE_EXT else pdf_path
    preview_name = None
    if source:
        try:
            page = pymupdf.open(source)[0]
            zoom = min(2.0, 800 / max(page.rect.width, 1))
            preview_name = stem + ".png"
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(os.path.join(PREVIEW_DIR, preview_name))
        except Exception as exc:  # buzilgan fayl yuklashni to'xtatmasin
            app.logger.warning("Preview yaratilmadi: %s", exc)
            preview_name = None
    return pdf_name, preview_name


def resolve_specialty(db, form, department_id, user_id):
    """Ro'yxatdan tanlangan yoki o'qituvchi o'zi yozgan mutaxassislik id sini qaytaradi."""
    new_name = form.get("new_specialty", "").strip()
    if new_name:
        row = db.execute("SELECT id FROM specialties WHERE name = ? COLLATE NOCASE AND department_id = ?",
                         (new_name, department_id)).fetchone()
        if row:
            return row["id"]
        cur = db.execute(
            "INSERT INTO specialties (name, department_id, approved, created_by) VALUES (?, ?, 0, ?)",
            (new_name, department_id, user_id))
        return cur.lastrowid
    sid = form.get("specialty")
    if sid and db.execute("SELECT 1 FROM specialties WHERE id = ? AND department_id = ?",
                          (sid, department_id)).fetchone():
        return int(sid)
    return None


@app.route("/yuklash", methods=["GET", "POST"])
@login_required("teacher", "admin")
def upload():
    user = current_user()
    if request.method == "POST":
        db = get_db()
        f = request.files.get("file")
        title = request.form.get("title", "").strip()
        dep = request.form.get("department")
        ext = f.filename.rsplit(".", 1)[-1].lower() if f and "." in f.filename else ""
        if not f or not f.filename:
            flash("Fayl tanlanmagan.", "error")
        elif ext not in ALLOWED_EXT:
            flash("Bu turdagi fayl qabul qilinmaydi: " + ", ".join(sorted(ALLOWED_EXT)), "error")
        elif not title:
            flash("Sarlavha kiriting.", "error")
        elif not dep or not db.execute("SELECT 1 FROM departments WHERE id = ?", (dep,)).fetchone():
            flash("Kafedrani tanlang.", "error")
        else:
            stem = uuid.uuid4().hex
            stored = f"{stem}.{ext}"
            path = os.path.join(UPLOAD_DIR, stored)
            f.save(path)
            pdf_name, preview_name = make_preview(path, ext, stem)
            spec_id = resolve_specialty(db, request.form, int(dep), user["id"])
            cur = db.execute(
                "INSERT INTO resources (title, description, stored_name, original_name, ext, pdf_name,"
                " preview_name, teacher_id, department_id, specialty_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (title, request.form.get("description", "").strip(), stored, f.filename, ext,
                 pdf_name, preview_name, user["id"], int(dep), spec_id, now_str()))
            db.commit()
            flash("Resurs joylandi.", "success")
            return redirect(url_for("resource", rid=cur.lastrowid))
    return render_template("upload.html", departments=departments(),
                           specialties=specialties(include_unapproved_by=user["id"]), resource=None)


def get_resource(rid):
    r = get_db().execute(f"""
        SELECT r.*, u.full_name AS teacher_name, d.name AS department_name, sp.name AS specialty_name,
               s.avg_rating, s.rating_count, s.like_count, s.comment_count, s.score
        FROM resources r
        JOIN users u ON u.id = r.teacher_id
        JOIN departments d ON d.id = r.department_id
        LEFT JOIN specialties sp ON sp.id = r.specialty_id
        JOIN ({SCORE_SQL}) s ON s.resource_id = r.id
        WHERE r.id = ?""", (rid,)).fetchone()
    if r is None:
        abort(404)
    user = current_user()
    if r["hidden"] and user["role"] != "admin" and user["id"] != r["teacher_id"]:
        abort(404)
    return r


def can_edit(r):
    user = current_user()
    return user["role"] == "admin" or user["id"] == r["teacher_id"]


app.jinja_env.globals["can_edit"] = can_edit


@app.route("/resurs/<int:rid>")
@login_required()
def resource(rid):
    db = get_db()
    r = get_resource(rid)
    uid = current_user()["id"]
    if uid != r["teacher_id"]:
        db.execute("UPDATE resources SET views = views + 1 WHERE id = ?", (rid,))
        db.commit()
    comments = db.execute(
        "SELECT c.*, u.full_name, u.role FROM comments c JOIN users u ON u.id = c.user_id"
        " WHERE c.resource_id = ? ORDER BY c.created_at DESC", (rid,)).fetchall()
    mine = {
        "rating": (db.execute("SELECT value FROM ratings WHERE user_id = ? AND resource_id = ?",
                              (uid, rid)).fetchone() or {"value": 0})["value"],
        "liked": bool(db.execute("SELECT 1 FROM likes WHERE user_id = ? AND resource_id = ?",
                                 (uid, rid)).fetchone()),
        "saved": bool(db.execute("SELECT 1 FROM favorites WHERE user_id = ? AND resource_id = ?",
                                 (uid, rid)).fetchone()),
    }
    return render_template("resource.html", r=r, comments=comments, mine=mine)


@app.route("/resurs/<int:rid>/<action>", methods=["POST"])
@login_required()
def resource_action(rid, action):
    db = get_db()
    r = get_resource(rid)
    uid = current_user()["id"]
    if action == "baho":
        if uid == r["teacher_id"]:
            flash("O'z resursingizga baho qo'ya olmaysiz.", "warning")
        else:
            value = int(request.form.get("value", 0))
            if 1 <= value <= 5:
                db.execute("INSERT INTO ratings (user_id, resource_id, value) VALUES (?, ?, ?)"
                           " ON CONFLICT (user_id, resource_id) DO UPDATE SET value = excluded.value",
                           (uid, rid, value))
    elif action in ("like", "saqlash"):
        table = "likes" if action == "like" else "favorites"
        if action == "like" and uid == r["teacher_id"]:
            flash("O'z resursingizga like bosa olmaysiz.", "warning")
        elif db.execute(f"SELECT 1 FROM {table} WHERE user_id = ? AND resource_id = ?", (uid, rid)).fetchone():
            db.execute(f"DELETE FROM {table} WHERE user_id = ? AND resource_id = ?", (uid, rid))
        else:
            db.execute(f"INSERT INTO {table} (user_id, resource_id) VALUES (?, ?)", (uid, rid))
    elif action == "izoh":
        text = request.form.get("text", "").strip()[:2000]
        if text:
            # Reytingga faqat bitta akkauntning birinchi izohi o'tadi; muallifning o'z izohi o'tmaydi
            already = db.execute("SELECT 1 FROM comments WHERE user_id = ? AND resource_id = ? AND counted = 1",
                                 (uid, rid)).fetchone()
            counted = 0 if already or uid == r["teacher_id"] else 1
            db.execute("INSERT INTO comments (resource_id, user_id, text, counted, created_at) VALUES (?, ?, ?, ?, ?)",
                       (rid, uid, text, counted, now_str()))
    else:
        abort(404)
    db.commit()
    return redirect(url_for("resource", rid=rid) + ("#izohlar" if action == "izoh" else ""))


@app.route("/izoh/<int:cid>/ochirish", methods=["POST"])
@login_required()
def delete_comment(cid):
    db = get_db()
    c = db.execute("SELECT * FROM comments WHERE id = ?", (cid,)).fetchone()
    if c is None:
        abort(404)
    if current_user()["role"] != "admin" and current_user()["id"] != c["user_id"]:
        abort(403)
    db.execute("DELETE FROM comments WHERE id = ?", (cid,))
    if c["counted"]:
        # Hisobga o'tgan izoh o'chirilsa, shu foydalanuvchining keyingi izohi hisobga o'tadi
        nxt = db.execute("SELECT id FROM comments WHERE user_id = ? AND resource_id = ? ORDER BY created_at LIMIT 1",
                         (c["user_id"], c["resource_id"])).fetchone()
        res = db.execute("SELECT teacher_id FROM resources WHERE id = ?", (c["resource_id"],)).fetchone()
        if nxt and res["teacher_id"] != c["user_id"]:
            db.execute("UPDATE comments SET counted = 1 WHERE id = ?", (nxt["id"],))
    db.commit()
    return redirect(url_for("resource", rid=c["resource_id"]) + "#izohlar")


@app.route("/resurs/<int:rid>/tahrirlash", methods=["GET", "POST"])
@login_required("teacher", "admin")
def edit_resource(rid):
    r = get_resource(rid)
    if not can_edit(r):
        abort(403)
    db = get_db()
    if request.method == "POST":
        dep = request.form.get("department")
        if not request.form.get("title", "").strip() or not db.execute(
                "SELECT 1 FROM departments WHERE id = ?", (dep,)).fetchone():
            flash("Sarlavha va kafedra majburiy.", "error")
        else:
            spec_id = resolve_specialty(db, request.form, int(dep), current_user()["id"])
            db.execute("UPDATE resources SET title = ?, description = ?, department_id = ?, specialty_id = ?"
                       " WHERE id = ?", (request.form["title"].strip(), request.form.get("description", "").strip(),
                                         int(dep), spec_id, rid))
            db.commit()
            flash("Saqlandi.", "success")
            return redirect(url_for("resource", rid=rid))
    return render_template("upload.html", departments=departments(),
                           specialties=specialties(include_unapproved_by=current_user()["id"]), resource=r)


@app.route("/resurs/<int:rid>/ochirish", methods=["POST"])
@login_required("teacher", "admin")
def delete_resource(rid):
    r = get_resource(rid)
    if not can_edit(r):
        abort(403)
    db = get_db()
    db.execute("DELETE FROM resources WHERE id = ?", (rid,))
    db.commit()
    for name, folder in ((r["stored_name"], UPLOAD_DIR), (r["pdf_name"], PREVIEW_DIR), (r["preview_name"], PREVIEW_DIR)):
        if name and os.path.exists(os.path.join(folder, name)):
            os.remove(os.path.join(folder, name))
    flash("Resurs o'chirildi.", "success")
    return redirect(url_for("index"))


@app.route("/resurs/<int:rid>/yashirish", methods=["POST"])
@login_required("admin")
def toggle_hidden(rid):
    db = get_db()
    db.execute("UPDATE resources SET hidden = 1 - hidden WHERE id = ?", (rid,))
    db.commit()
    return redirect(request.referrer or url_for("resource", rid=rid))


@app.route("/resurs/<int:rid>/yuklab-olish")
@login_required()
def download(rid):
    r = get_resource(rid)
    db = get_db()
    db.execute("UPDATE resources SET downloads = downloads + 1 WHERE id = ?", (rid,))
    db.commit()
    return send_from_directory(UPLOAD_DIR, r["stored_name"], as_attachment=True, download_name=r["original_name"])


@app.route("/resurs/<int:rid>/korish")
@login_required()
def view_file(rid):
    r = get_resource(rid)
    if r["ext"] == "pdf":
        return send_from_directory(UPLOAD_DIR, r["stored_name"], mimetype="application/pdf")
    if r["pdf_name"]:
        return send_from_directory(PREVIEW_DIR, r["pdf_name"], mimetype="application/pdf")
    if r["ext"] in IMAGE_EXT:
        return send_from_directory(UPLOAD_DIR, r["stored_name"])
    abort(404)


@app.route("/resurs/<int:rid>/rasm")
@login_required()
def preview(rid):
    r = get_resource(rid)
    if not r["preview_name"]:
        abort(404)
    return send_from_directory(PREVIEW_DIR, r["preview_name"], max_age=86400)


@app.route("/api/mutaxassisliklar")
@login_required()
def api_specialties():
    return [dict(id=s["id"], name=s["name"], department_id=s["department_id"])
            for s in specialties(include_unapproved_by=current_user()["id"])]


@app.route("/oqituvchilar")
@login_required()
def teacher_rating():
    rows = get_db().execute(f"""
        SELECT u.id, u.full_name, d.name AS department_name,
               COUNT(r.id) AS resource_count,
               COALESCE(SUM(s.score), 0) AS total_score,
               COALESCE(SUM(s.like_count), 0) AS likes,
               COALESCE(SUM(r.downloads), 0) AS downloads,
               (SELECT AVG(value) FROM ratings rt JOIN resources rr ON rr.id = rt.resource_id
                 WHERE rr.teacher_id = u.id AND rr.hidden = 0) AS avg_rating
        FROM users u
        LEFT JOIN departments d ON d.id = u.department_id
        LEFT JOIN resources r ON r.teacher_id = u.id AND r.hidden = 0
        LEFT JOIN ({SCORE_SQL}) s ON s.resource_id = r.id
        WHERE u.role = 'teacher' AND u.is_active = 1
        GROUP BY u.id ORDER BY total_score DESC, resource_count DESC""").fetchall()
    return render_template("teachers.html", rows=rows,
                           formula=(SCORE_RATING_WEIGHT, SCORE_LIKE, SCORE_COMMENT))


@app.route("/mening-resurslarim")
@login_required("teacher", "admin")
def my_resources():
    return redirect(url_for("index", teacher=current_user()["id"]))


# ---------------------------------------------------------------- admin

@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    db = get_db()
    today = now().date()
    start = today - timedelta(days=13)
    counts = db.execute(
        """SELECT (SELECT COUNT(*) FROM users WHERE role = 'teacher' AND is_active = 1) AS teachers,
                  (SELECT COUNT(*) FROM users WHERE role = 'student' AND is_active = 1) AS students,
                  (SELECT COUNT(*) FROM users WHERE role = 'user' AND is_active = 1) AS guests,
                  (SELECT COUNT(*) FROM resources) AS resources,
                  (SELECT COUNT(*) FROM resources WHERE hidden = 1) AS hidden,
                  (SELECT COALESCE(SUM(downloads), 0) FROM resources) AS downloads,
                  (SELECT COALESCE(SUM(views), 0) FROM resources) AS views,
                  (SELECT COUNT(*) FROM comments) AS comments,
                  (SELECT COUNT(*) FROM likes) AS likes,
                  (SELECT COUNT(*) FROM specialties WHERE approved = 0) AS pending_specs,
                  (SELECT COUNT(DISTINCT user_id) FROM login_sessions WHERE login_at >= ?) AS today_users,
                  (SELECT COUNT(DISTINCT user_id) FROM login_sessions
                    WHERE logout_at IS NULL AND last_seen >= ?) AS online,
                  (SELECT COUNT(*) FROM users WHERE created_at >= ?) AS new_week""",
        (today.isoformat(), (now() - timedelta(minutes=5)).isoformat(sep=" "),
         (today - timedelta(days=6)).isoformat())).fetchone()
    # Oxirgi 14 kun: kirishlar va yangi ro'yxatdan o'tganlar (bo'sh kunlar ham ko'rinsin)
    logins = dict(db.execute("SELECT substr(login_at, 1, 10), COUNT(DISTINCT user_id) FROM login_sessions"
                             " WHERE login_at >= ? GROUP BY 1", (start.isoformat(),)).fetchall())
    signups = dict(db.execute("SELECT substr(created_at, 1, 10), COUNT(*) FROM users"
                              " WHERE created_at >= ? GROUP BY 1", (start.isoformat(),)).fetchall())
    days = [(start + timedelta(days=i)).isoformat() for i in range(14)]
    daily = [{"day": d, "logins": logins.get(d, 0), "signups": signups.get(d, 0)} for d in days]
    recent_users = db.execute("SELECT id, full_name, role, group_name, created_at FROM users"
                              " ORDER BY id DESC LIMIT 6").fetchall()
    recent_resources = db.execute(
        "SELECT r.id, r.title, r.ext, r.created_at, u.full_name AS teacher_name FROM resources r"
        " JOIN users u ON u.id = r.teacher_id ORDER BY r.id DESC LIMIT 6").fetchall()
    top = db.execute(
        f"SELECT r.id, r.title, s.score, s.like_count, r.downloads FROM ({SCORE_SQL}) s"
        " JOIN resources r ON r.id = s.resource_id WHERE r.hidden = 0 ORDER BY s.score DESC LIMIT 5").fetchall()
    return render_template("admin_dashboard.html", c=counts, daily=daily, recent_users=recent_users,
                           recent_resources=recent_resources, top=top)


@app.route("/admin/foydalanuvchilar", methods=["GET", "POST"])
@login_required("admin")
def admin_users():
    db = get_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role")
        password = request.form.get("password", "").strip() or gen_password()
        if not username or not full_name or role not in ROLE_NAMES:
            flash("Login, F.I.Sh. va rol majburiy.", "error")
        elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            flash(f"'{username}' logini band.", "error")
        else:
            db.execute("INSERT INTO users (username, password_hash, full_name, role, department_id, group_name,"
                       " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (username, generate_password_hash(password), full_name, role,
                        request.form.get("department") or None, request.form.get("group_name", "").strip() or None,
                        now_str()))
            db.commit()
            flash(f"Foydalanuvchi yaratildi. Login: {username}  Parol: {password}", "success")
        return redirect(url_for("admin_users"))
    role = request.args.get("role")
    users = db.execute(
        "SELECT u.*, d.name AS department_name FROM users u LEFT JOIN departments d ON d.id = u.department_id"
        + (" WHERE u.role = ?" if role else "") + " ORDER BY u.role, u.full_name",
        (role,) if role else ()).fetchall()
    return render_template("admin_users.html", users=users, departments=departments())


def gen_password():
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


@app.route("/admin/foydalanuvchi/<int:uid>/<action>", methods=["POST"])
@login_required("admin")
def admin_user_action(uid, action):
    db = get_db()
    if uid == current_user()["id"] and action in ("bloklash", "ochirish"):
        flash("O'zingizni bloklay yoki o'chira olmaysiz.", "error")
    elif action == "parol":
        password = gen_password()
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), uid))
        flash(f"Yangi parol: {password}", "success")
    elif action == "bloklash":
        db.execute("UPDATE users SET is_active = 1 - is_active WHERE id = ?", (uid,))
    elif action == "ochirish":
        if db.execute("SELECT 1 FROM resources WHERE teacher_id = ?", (uid,)).fetchone():
            flash("Bu foydalanuvchining resurslari bor. Uni o'chirish o'rniga bloklang.", "error")
        else:
            db.execute("DELETE FROM users WHERE id = ?", (uid,))
    else:
        abort(404)
    db.commit()
    return redirect(request.referrer or url_for("admin_users"))


@app.route("/admin/import", methods=["POST"])
@login_required("admin")
def admin_import():
    """CSV yoki Excel: login, F.I.Sh., rol (teacher/student), kafedra (ixtiyoriy), guruh (ixtiyoriy), parol (ixtiyoriy)."""
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Fayl tanlanmagan.", "error")
        return redirect(url_for("admin_users"))
    if f.filename.lower().endswith(".xlsx"):
        import openpyxl
        ws = openpyxl.load_workbook(f, read_only=True).active
        rows = [[("" if c is None else str(c)).strip() for c in row] for row in ws.iter_rows(values_only=True)]
    else:
        text = f.read().decode("utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t") if text.strip() else csv.excel
        rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    db = get_db()
    deps = {d["name"].lower(): d["id"] for d in departments()}
    role_alias = {"teacher": "teacher", "o'qituvchi": "teacher", "oqituvchi": "teacher",
                  "student": "student", "o'quvchi": "student", "oquvchi": "student"}
    created, skipped = [], []
    for row in rows:
        row = (row + [""] * 6)[:6]
        username, full_name, role, dep, group, password = row
        role = role_alias.get(role.lower())
        if not username or not full_name or not role:
            if username and username.lower() not in ("login", "username"):
                skipped.append(username)
            continue
        if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            skipped.append(username)
            continue
        password = password or gen_password()
        db.execute("INSERT INTO users (username, password_hash, full_name, role, department_id, group_name, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (username, generate_password_hash(password), full_name, role, deps.get(dep.lower()),
                    group or None, now_str()))
        created.append((username, full_name, password))
    db.commit()
    if skipped:
        flash("O'tkazib yuborildi (band yoki noto'g'ri): " + ", ".join(skipped), "warning")
    return render_template("import_result.html", created=created)


@app.route("/admin/kafedralar", methods=["GET", "POST"])
@login_required("admin")
def admin_departments():
    db = get_db()
    if request.method == "POST":
        kind = request.form.get("kind")
        name = request.form.get("name", "").strip()
        try:
            if kind == "department" and name:
                db.execute("INSERT INTO departments (name) VALUES (?)", (name,))
            elif kind == "specialty" and name and request.form.get("department"):
                db.execute("INSERT INTO specialties (name, department_id, approved, created_by) VALUES (?, ?, 1, ?)",
                           (name, request.form["department"], current_user()["id"]))
            elif kind == "group" and name:
                db.execute("INSERT INTO groups (name, department_id) VALUES (?, ?)",
                           (name[:30], request.form.get("department") or None))
            db.commit()
        except sqlite3.IntegrityError:
            flash("Bunday nom allaqachon mavjud.", "error")
        return redirect(url_for("admin_departments"))
    specs = db.execute(
        "SELECT s.*, d.name AS department_name, u.full_name AS author,"
        " (SELECT COUNT(*) FROM resources WHERE specialty_id = s.id) AS resource_count"
        " FROM specialties s JOIN departments d ON d.id = s.department_id LEFT JOIN users u ON u.id = s.created_by"
        " ORDER BY s.approved, d.name, s.name").fetchall()
    deps = db.execute("SELECT d.*, (SELECT COUNT(*) FROM resources WHERE department_id = d.id) AS resource_count"
                      " FROM departments d ORDER BY name").fetchall()
    grs = db.execute("SELECT gr.*, d.name AS department_name,"
                     " (SELECT COUNT(*) FROM users WHERE group_name = gr.name AND role = 'student') AS student_count"
                     " FROM groups gr LEFT JOIN departments d ON d.id = gr.department_id ORDER BY gr.name").fetchall()
    return render_template("admin_departments.html", departments=deps, specialties=specs, groups=grs)


@app.route("/admin/guruh/<int:gid>/<action>", methods=["POST"])
@login_required("admin")
def admin_group_action(gid, action):
    db = get_db()
    if action == "saqlash":
        name = request.form.get("name", "").strip()[:30]
        old = db.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
        if name and old:
            try:
                db.execute("UPDATE groups SET name = ?, department_id = ? WHERE id = ?",
                           (name, request.form.get("department") or None, gid))
                # Guruh nomi o'quvchilar yozuvida ham yangilanadi
                db.execute("UPDATE users SET group_name = ? WHERE group_name = ?", (name, old["name"]))
            except sqlite3.IntegrityError:
                flash("Bunday guruh allaqachon mavjud.", "error")
    elif action == "ochirish":
        db.execute("DELETE FROM groups WHERE id = ?", (gid,))
    else:
        abort(404)
    db.commit()
    return redirect(url_for("admin_departments") + "#guruhlar")


@app.route("/admin/kafedra/<int:did>/<action>", methods=["POST"])
@login_required("admin")
def admin_department_action(did, action):
    db = get_db()
    if action == "nom":
        name = request.form.get("name", "").strip()
        if name:
            try:
                db.execute("UPDATE departments SET name = ? WHERE id = ?", (name, did))
            except sqlite3.IntegrityError:
                flash("Bunday nom allaqachon mavjud.", "error")
    elif action == "ochirish":
        if db.execute("SELECT 1 FROM resources WHERE department_id = ?", (did,)).fetchone():
            flash("Kafedrada resurslar bor, avval ularni boshqa kafedraga o'tkazing.", "error")
        else:
            db.execute("DELETE FROM departments WHERE id = ?", (did,))
    else:
        abort(404)
    db.commit()
    return redirect(url_for("admin_departments"))


@app.route("/admin/mutaxassislik/<int:sid>/<action>", methods=["POST"])
@login_required("admin")
def admin_specialty_action(sid, action):
    db = get_db()
    if action == "tasdiqlash":
        db.execute("UPDATE specialties SET approved = 1 WHERE id = ?", (sid,))
    elif action == "nom":
        name = request.form.get("name", "").strip()
        if name:
            try:
                db.execute("UPDATE specialties SET name = ? WHERE id = ?", (name, sid))
            except sqlite3.IntegrityError:
                flash("Bu kafedrada bunday mutaxassislik bor. Birlashtirish uchun pastdagi ro'yxatdan foydalaning.",
                      "error")
    elif action == "birlashtirish":
        target = request.form.get("target")
        if target and int(target) != sid:
            db.execute("UPDATE resources SET specialty_id = ? WHERE specialty_id = ?", (target, sid))
            db.execute("DELETE FROM specialties WHERE id = ?", (sid,))
    elif action == "ochirish":
        db.execute("DELETE FROM specialties WHERE id = ?", (sid,))
    else:
        abort(404)
    db.commit()
    return redirect(url_for("admin_departments"))


def fmt_duration(seconds):
    seconds = int(seconds or 0)
    h, m = divmod(seconds // 60, 60)
    return f"{h} soat {m} daq" if h else f"{m} daq"


app.jinja_env.filters["duration"] = fmt_duration


@app.route("/admin/statistika")
@login_required("admin")
def admin_stats():
    db = get_db()
    days = int(request.args.get("days", 30))
    since = (now() - timedelta(days=days)).isoformat(sep=" ") if days else "0000"
    role = request.args.get("role")
    users = db.execute(
        """SELECT u.id, u.full_name, u.username, u.role, u.group_name,
                  COUNT(ls.id) AS login_count,
                  MAX(ls.login_at) AS last_login,
                  COALESCE(SUM(strftime('%s', ls.last_seen) - strftime('%s', ls.login_at)), 0) AS total_seconds
           FROM users u LEFT JOIN login_sessions ls ON ls.user_id = u.id AND ls.login_at >= ?
           WHERE u.role != 'admin'""" + (" AND u.role = ?" if role else "") +
        " GROUP BY u.id ORDER BY last_login IS NULL, last_login DESC",
        (since, role) if role else (since,)).fetchall()
    totals = db.execute(
        """SELECT (SELECT COUNT(*) FROM resources) AS resources,
                  (SELECT COUNT(*) FROM users WHERE role = 'teacher') AS teachers,
                  (SELECT COUNT(*) FROM users WHERE role = 'student') AS students,
                  (SELECT COUNT(*) FROM login_sessions WHERE login_at >= ?) AS logins,
                  (SELECT COUNT(DISTINCT user_id) FROM login_sessions WHERE login_at >= ?) AS active_users,
                  (SELECT COALESCE(SUM(downloads), 0) FROM resources) AS downloads""",
        (since, since)).fetchone()
    daily = db.execute(
        "SELECT substr(login_at, 1, 10) AS day, COUNT(*) AS n, COUNT(DISTINCT user_id) AS u"
        " FROM login_sessions WHERE login_at >= ? GROUP BY day ORDER BY day", (since,)).fetchall()
    online_since = (now() - timedelta(minutes=5)).isoformat(sep=" ")
    online = db.execute("SELECT DISTINCT u.full_name FROM login_sessions ls JOIN users u ON u.id = ls.user_id"
                        " WHERE ls.logout_at IS NULL AND ls.last_seen >= ?", (online_since,)).fetchall()
    return render_template("admin_stats.html", users=users, totals=totals, daily=daily, online=online, days=days)


@app.route("/admin/statistika/<int:uid>")
@login_required("admin")
def admin_user_sessions(uid):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user is None:
        abort(404)
    sessions = db.execute(
        "SELECT *, strftime('%s', last_seen) - strftime('%s', login_at) AS seconds FROM login_sessions"
        " WHERE user_id = ? ORDER BY login_at DESC LIMIT 500", (uid,)).fetchall()
    return render_template("admin_user_sessions.html", user=user, sessions=sessions)


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, message="Bu sahifaga ruxsatingiz yo'q."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, message="Sahifa topilmadi."), 404


@app.errorhandler(413)
def too_large(_e):
    mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return render_template("error.html", code=413, message=f"Fayl juda katta (maksimum {mb} MB)."), 413


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))

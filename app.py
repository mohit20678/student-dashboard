import json
import os
import re
import secrets
import threading
import webbrowser
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data.json")
KEY = os.path.join(BASE, ".secret_key")
lock = threading.RLock()

# ---- college rules (change here if your college uses different values) ----
LIMIT = 75     # minimum attendance %
BORDER = 80    # from LIMIT up to this = yellow (borderline), above = green
MAX = {"mid1": 30, "mid2": 30, "internal": 10, "test": 10, "sem": 70}

app = Flask(__name__, static_folder="static")
if not os.path.exists(KEY):
    with open(KEY, "w") as f:
        f.write(secrets.token_hex(32))
with open(KEY) as f:
    app.secret_key = f.read().strip()


# ------------------------------------------------------------------ database
def load():
    with open(DB, encoding="utf-8") as f:
        return json.load(f)


def save(d):
    with open(DB, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def init_db():
    if not os.path.exists(DB):
        save({"users": [], "students": [], "subjects": [], "attendance": [], "marks": {}})
    d = load()
    if not any(u["role"] == "admin" for u in d["users"]):
        d["users"].append({"username": "admin", "role": "admin", "name": "Administrator",
                           "password": generate_password_hash("admin123")})
        save(d)


init_db()


def err(msg, code=400):
    return jsonify({"error": msg}), code


def find_user(d, username):
    username = str(username).strip().lower()
    return next((u for u in d["users"] if u["username"].lower() == username), None)


def need(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            if session.get("role") not in roles:
                return err("Not allowed", 401)
            return fn(*a, **k)
        return wrapper
    return deco


# ------------------------------------------------------------ attendance math
def metrics(total, present):
    """percent, colour status, classes needed to reach LIMIT, classes that can be missed."""
    if total == 0:
        return {"percent": 0, "status": "none", "need": 0, "can_miss": 0}
    below = present * 100 < LIMIT * total
    if below:
        status = "red"
    elif present * 100 < BORDER * total:
        status = "yellow"
    else:
        status = "green"
    # (present+x)/(total+x) >= 0.75  ->  x >= 3*total - 4*present
    need_cls = 3 * total - 4 * present if below else 0
    # present/(total+y) >= 0.75      ->  y <= (4*present - 3*total)/3
    can_miss = 0 if below else (4 * present - 3 * total) // 3
    return {"percent": round(present * 100 / total, 1), "status": status,
            "need": need_cls, "can_miss": can_miss}


def subject_counts(d, code, roll):
    t = p = 0
    for r in d["attendance"]:
        if r["subject"] == code and roll in r["status"]:
            t += 1
            p += 1 if r["status"][roll] else 0
    return t, p


def student_stats(d, st):
    subs, T, P = [], 0, 0
    for sub in d["subjects"]:
        if sub["klass"] != st["klass"]:
            continue
        t, p = subject_counts(d, sub["code"], st["roll"])
        subs.append({"code": sub["code"], "name": sub["name"], "total": t, "present": p,
                     "marks": d["marks"].get(sub["code"] + "|" + st["roll"], {}),
                     **metrics(t, p)})
        T += t
        P += p
    return subs, {"total": T, "present": P, **metrics(T, P)}


# --------------------------------------------------------------------- pages
@app.route("/")
def home():
    return send_from_directory("static", "login.html")


@app.route("/<page>")
def page(page):
    if page in ("student", "teacher", "admin"):
        return send_from_directory("static", page + ".html")
    return err("Not found", 404)


# ---------------------------------------------------------------------- auth
@app.route("/api/login", methods=["POST"])
def login():
    b = request.get_json() or {}
    u = find_user(load(), b.get("username", ""))
    if not u or not check_password_hash(u["password"], str(b.get("password", ""))):
        return err("Wrong username or password", 401)
    session.clear()
    session.update(username=u["username"], role=u["role"], name=u["name"])
    return jsonify({"role": u["role"], "name": u["name"]})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    if "role" not in session:
        return err("login required", 401)
    return jsonify({"username": session["username"], "role": session["role"], "name": session["name"]})


@app.route("/api/password", methods=["POST"])
@need("student", "teacher", "admin")
def change_password():
    b = request.get_json() or {}
    with lock:
        d = load()
        u = find_user(d, session["username"])
        if not check_password_hash(u["password"], str(b.get("old", ""))):
            return err("Old password is wrong")
        if len(str(b.get("new", ""))) < 4:
            return err("New password must be at least 4 characters")
        u["password"] = generate_password_hash(str(b["new"]))
        save(d)
    return jsonify({"ok": True})


# ------------------------------------------------------------------- student
@app.route("/api/student/me")
@need("student")
def student_me():
    d = load()
    st = next((s for s in d["students"] if s["roll"].lower() == session["username"].lower()), None)
    if not st:
        return err("Student record not found", 404)
    subs, overall = student_stats(d, st)
    return jsonify({"student": st, "subjects": subs, "overall": overall, "max": MAX, "limit": LIMIT})


# ------------------------------------------------------------ teacher / admin
def get_subject(d, code):
    s = next((x for x in d["subjects"] if x["code"] == code), None)
    if s and session["role"] == "teacher" and s["teacher"].lower() != session["username"].lower():
        return None
    return s


@app.route("/api/subjects")
@need("teacher", "admin")
def my_subjects():
    d = load()
    subs = d["subjects"] if session["role"] == "admin" else \
        [s for s in d["subjects"] if s["teacher"].lower() == session["username"].lower()]
    return jsonify(subs)


@app.route("/api/subjects/<code>/students")
@need("teacher", "admin")
def subject_students(code):
    d = load()
    sub = get_subject(d, code)
    if not sub:
        return err("Subject not found", 404)
    day = request.args.get("date", "")
    existing = next((r for r in d["attendance"] if r["subject"] == code and r["date"] == day), None)
    rows = []
    for st in d["students"]:
        if st["klass"] != sub["klass"]:
            continue
        t, p = subject_counts(d, code, st["roll"])
        rows.append({**st, "total": t, "present": p, **metrics(t, p),
                     "marks": d["marks"].get(code + "|" + st["roll"], {}),
                     "today": existing["status"].get(st["roll"]) if existing else None})
    return jsonify({"subject": sub, "students": rows, "max": MAX, "marked": bool(existing)})


@app.route("/api/subjects/<code>/attendance", methods=["POST"])
@need("teacher", "admin")
def save_attendance(code):
    b = request.get_json() or {}
    day = str(b.get("date", ""))
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return err("Pick a valid date")
    with lock:
        d = load()
        sub = get_subject(d, code)
        if not sub:
            return err("Subject not found", 404)
        rolls = {s["roll"] for s in d["students"] if s["klass"] == sub["klass"]}
        status = {r: bool(v) for r, v in (b.get("status") or {}).items() if r in rolls}
        if not status:
            return err("No students to mark")
        d["attendance"] = [r for r in d["attendance"] if not (r["subject"] == code and r["date"] == day)]
        d["attendance"].append({"date": day, "subject": code, "status": status})
        save(d)
    return jsonify({"ok": True})


@app.route("/api/subjects/<code>/marks", methods=["POST"])
@need("teacher", "admin")
def save_marks(code):
    b = request.get_json() or {}
    roll = str(b.get("roll", ""))
    with lock:
        d = load()
        sub = get_subject(d, code)
        st = next((s for s in d["students"] if s["roll"] == roll and s["klass"] == (sub or {}).get("klass")), None)
        if not sub or not st:
            return err("Student or subject not found", 404)
        cur = d["marks"].setdefault(code + "|" + roll, {})
        for field, val in (b.get("marks") or {}).items():
            if field not in MAX:
                continue
            if val == "" or val is None:
                cur.pop(field, None)
                continue
            try:
                num = float(val)
            except ValueError:
                return err(field + " must be a number")
            if num < 0 or num > MAX[field]:
                return err(f"{field} must be between 0 and {MAX[field]}")
            cur[field] = num
        save(d)
    return jsonify({"ok": True})


# --------------------------------------------------------------------- admin
@app.route("/api/admin/data")
@need("admin")
def admin_data():
    d = load()
    teachers = [{"username": u["username"], "name": u["name"]} for u in d["users"] if u["role"] == "teacher"]
    return jsonify({"students": d["students"], "teachers": teachers, "subjects": d["subjects"], "max": MAX})


@app.route("/api/admin/overview")
@need("admin")
def admin_overview():
    d = load()
    out = []
    for st in d["students"]:
        subs, overall = student_stats(d, st)
        out.append({**st, "subjects": subs, "overall": overall})
    return jsonify(out)


def add_student(d, roll, name, klass):
    roll, name, klass = roll.strip().upper(), name.strip(), klass.strip().upper()
    if not roll or not name or not klass:
        return "Roll no, name and class are required"
    if find_user(d, roll):
        return "Roll no " + roll + " already exists"
    d["students"].append({"roll": roll, "name": name, "klass": klass})
    d["users"].append({"username": roll, "role": "student", "name": name,
                       "password": generate_password_hash(roll)})
    return None


@app.route("/api/admin/students", methods=["POST"])
@need("admin")
def admin_add_student():
    b = request.get_json() or {}
    with lock:
        d = load()
        e = add_student(d, str(b.get("roll", "")), str(b.get("name", "")), str(b.get("klass", "")))
        if e:
            return err(e)
        save(d)
    return jsonify({"ok": True})


@app.route("/api/admin/students/bulk", methods=["POST"])
@need("admin")
def admin_bulk_students():
    text = str((request.get_json() or {}).get("text", ""))
    added, skipped = 0, []
    with lock:
        d = load()
        for line in text.splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) < 3 or not any(parts):
                continue
            e = add_student(d, parts[0], parts[1], parts[2])
            if e:
                skipped.append(e)
            else:
                added += 1
        save(d)
    return jsonify({"added": added, "skipped": skipped})


@app.route("/api/admin/students/<roll>", methods=["DELETE"])
@need("admin")
def admin_del_student(roll):
    with lock:
        d = load()
        d["students"] = [s for s in d["students"] if s["roll"] != roll]
        d["users"] = [u for u in d["users"] if not (u["role"] == "student" and u["username"] == roll)]
        d["marks"] = {k: v for k, v in d["marks"].items() if not k.endswith("|" + roll)}
        for r in d["attendance"]:
            r["status"].pop(roll, None)
        save(d)
    return jsonify({"ok": True})


@app.route("/api/admin/teachers", methods=["POST"])
@need("admin")
def admin_add_teacher():
    b = request.get_json() or {}
    user, name = str(b.get("username", "")).strip(), str(b.get("name", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", user) or not name:
        return err("Username (3-30 letters/numbers) and name are required")
    with lock:
        d = load()
        if find_user(d, user):
            return err("Username already exists")
        d["users"].append({"username": user, "role": "teacher", "name": name,
                           "password": generate_password_hash(str(b.get("password") or user))})
        save(d)
    return jsonify({"ok": True})


@app.route("/api/admin/teachers/<username>", methods=["DELETE"])
@need("admin")
def admin_del_teacher(username):
    with lock:
        d = load()
        if any(s["teacher"] == username for s in d["subjects"]):
            return err("This teacher still has subjects. Delete or reassign them first")
        d["users"] = [u for u in d["users"] if not (u["role"] == "teacher" and u["username"] == username)]
        save(d)
    return jsonify({"ok": True})


@app.route("/api/admin/subjects", methods=["POST"])
@need("admin")
def admin_add_subject():
    b = request.get_json() or {}
    code, name = str(b.get("code", "")).strip().upper(), str(b.get("name", "")).strip()
    klass, teacher = str(b.get("klass", "")).strip().upper(), str(b.get("teacher", "")).strip()
    if not (code and name and klass and teacher):
        return err("Code, name, class and teacher are required")
    with lock:
        d = load()
        if any(s["code"] == code for s in d["subjects"]):
            return err("Subject code already exists")
        if not any(u["role"] == "teacher" and u["username"] == teacher for u in d["users"]):
            return err("Teacher not found")
        d["subjects"].append({"code": code, "name": name, "klass": klass, "teacher": teacher})
        save(d)
    return jsonify({"ok": True})


@app.route("/api/admin/subjects/<code>", methods=["DELETE"])
@need("admin")
def admin_del_subject(code):
    with lock:
        d = load()
        d["subjects"] = [s for s in d["subjects"] if s["code"] != code]
        d["attendance"] = [r for r in d["attendance"] if r["subject"] != code]
        d["marks"] = {k: v for k, v in d["marks"].items() if not k.startswith(code + "|")}
        save(d)
    return jsonify({"ok": True})


@app.route("/api/admin/reset", methods=["POST"])
@need("admin")
def admin_reset():
    user = str((request.get_json() or {}).get("username", ""))
    with lock:
        d = load()
        u = find_user(d, user)
        if not u or u["role"] == "admin":
            return err("User not found")
        u["password"] = generate_password_hash(u["username"])
        save(d)
    return jsonify({"ok": True})


if __name__ == "__main__":
    # open the browser only once (not again after auto-reload)
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(debug=True, port=5000)

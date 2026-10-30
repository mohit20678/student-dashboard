import json
import os
import re
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timedelta
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

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("HTTPS_ONLY") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

FAILS = {}  # username -> [wrong attempts, locked until (epoch seconds)]
MAX_TRIES, LOCK_SECS = 5, 300


# ------------------------------------------------------------------ database
def load():
    with open(DB, encoding="utf-8") as f:
        return json.load(f)


def save(d):
    with open(DB, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def _first_pw():
    pw = secrets.token_urlsafe(9)
    print("\n*** FIRST RUN: admin username 'admin', temporary password:", pw, "***\n")
    return pw


def init_db():
    if not os.path.exists(DB):
        save({"users": [], "students": [], "subjects": [], "attendance": [], "marks": {}})
    d = load()
    if not any(u["role"] == "admin" for u in d["users"]):
        d["users"].append({"username": "admin", "role": "admin", "name": "Administrator", "must_change": True,
                           "password": generate_password_hash(_first_pw())})
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
@app.before_request
def force_password_change():
    allowed = ("/api/me", "/api/logout", "/api/password", "/api/login")
    if session.get("must_change") and request.path.startswith("/api/") and request.path not in allowed:
        return err("You must change your password first", 403)


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


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
    name = str(b.get("username", "")).strip().lower()
    rec = FAILS.get(name, [0, 0])
    wait = int(rec[1] - time.time())
    if wait > 0:
        return err(f"Too many wrong attempts. Try again in {wait // 60 + 1} minute(s).", 429)
    u = find_user(load(), name)
    if not u or not check_password_hash(u["password"], str(b.get("password", ""))):
        if len(FAILS) > 5000:
            FAILS.clear()
        rec[0] += 1
        FAILS[name] = [0, time.time() + LOCK_SECS] if rec[0] >= MAX_TRIES else rec
        return err("Wrong username or password", 401)
    FAILS.pop(name, None)
    session.clear()
    session.permanent = True
    session.update(username=u["username"], role=u["role"], name=u["name"],
                   must_change=bool(u.get("must_change")))
    return jsonify({"role": u["role"], "name": u["name"], "must_change": bool(u.get("must_change"))})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    if "role" not in session:
        return err("login required", 401)
    return jsonify({"username": session["username"], "role": session["role"], "name": session["name"],
                    "must_change": bool(session.get("must_change"))})


@app.route("/api/password", methods=["POST"])
@need("student", "teacher", "admin")
def change_password():
    b = request.get_json() or {}
    with lock:
        d = load()
        u = find_user(d, session["username"])
        if not check_password_hash(u["password"], str(b.get("old", ""))):
            return err("Old password is wrong")
        new = str(b.get("new", ""))
        if len(new) < 8 or new.lower() == u["username"].lower() or check_password_hash(u["password"], new):
            return err("New password: at least 8 characters, different from the old one and from your username")
        u["password"] = generate_password_hash(new)
        u.pop("must_change", None)
        session["must_change"] = False
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
def analyse(items):
    """items: list of (roll, name, value). Returns max, min, average, ranks and tied marks."""
    if not items:
        return {"count": 0, "ranks": [], "same": []}
    items = sorted(items, key=lambda x: (-x[2], x[1].lower()))
    ranks, prev, rank = [], None, 0
    for i, (roll, name, v) in enumerate(items, 1):
        if v != prev:
            rank, prev = i, v
        ranks.append({"rank": rank, "roll": roll, "name": name, "mark": v})
    groups = {}
    for r in ranks:
        groups.setdefault(r["mark"], []).append(r["name"])
    vals = [r["mark"] for r in ranks]
    return {"count": len(vals), "max": max(vals), "min": min(vals),
            "avg": round(sum(vals) / len(vals), 2), "ranks": ranks,
            "same": [{"mark": m, "students": n} for m, n in groups.items() if len(n) > 1]}


def mark_value(marks, field):
    """One student's value for a mark type, or None if nothing is entered."""
    if field == "total":
        return round(sum(marks.values()), 2) if marks else None
    return marks.get(field)


def pick_field():
    f = request.args.get("field", "total")
    return f if (f == "total" or f in MAX) else None


@app.route("/api/subjects/<code>/analysis")
@need("teacher", "admin")
def subject_analysis(code):
    d = load()
    sub = get_subject(d, code)
    field = pick_field()
    if not sub or not field:
        return err("Subject or mark type not found", 404)
    items, missing = [], 0
    for st in d["students"]:
        if st["klass"] != sub["klass"]:
            continue
        v = mark_value(d["marks"].get(code + "|" + st["roll"], {}), field)
        if v is None:
            missing += 1
        else:
            items.append((st["roll"], st["name"], v))
    return jsonify({**analyse(items), "missing": missing, "field": field, "limit": MAX.get(field)})


@app.route("/api/admin/merit")
@need("admin")
def admin_merit():
    d = load()
    klass = request.args.get("klass", "").strip().upper()
    field = pick_field()
    if not klass or not field:
        return err("Choose a class")
    codes = [s["code"] for s in d["subjects"] if s["klass"] == klass]
    items, missing = [], 0
    for st in d["students"]:
        if st["klass"] != klass:
            continue
        vals = [mark_value(d["marks"].get(c + "|" + st["roll"], {}), field) for c in codes]
        vals = [v for v in vals if v is not None]
        if not vals:
            missing += 1
        else:
            items.append((st["roll"], st["name"], round(sum(vals), 2)))
    return jsonify({**analyse(items), "missing": missing, "field": field, "limit": None})


RESET_GAP = {}  # username -> time of the last forgot-password request


def _rate_locked(key):
    return int(FAILS.get(key, [0, 0])[1] - time.time())


def _rate_fail(key):
    rec = FAILS.get(key, [0, 0])
    if len(FAILS) > 5000:
        FAILS.clear()
    rec[0] += 1
    FAILS[key] = [0, time.time() + LOCK_SECS] if rec[0] >= MAX_TRIES else rec


def _new_recovery(u):
    code = "-".join(secrets.token_hex(2).upper() for _ in range(4))
    u["recovery_hash"] = generate_password_hash(code)
    return code


@app.route("/api/forgot", methods=["POST"])
def forgot():
    name = str((request.get_json() or {}).get("username", "")).strip()
    key = name.lower()
    generic = jsonify({"ok": True, "message": "If this account exists, your request was sent to the admin. Ask the admin for your temporary password."})
    if not key or len(key) > 40 or time.time() - RESET_GAP.get(key, 0) < 60:
        return generic
    with lock:
        d = load()
        u = find_user(d, name)
        reqs = d.setdefault("reset_requests", [])
        if u and u["role"] in ("student", "teacher") and len(reqs) < 200 \
                and not any(r["username"].lower() == key for r in reqs):
            reqs.append({"username": u["username"], "name": u["name"], "role": u["role"],
                         "time": datetime.now().strftime("%Y-%m-%d %H:%M")})
            save(d)
    if len(RESET_GAP) > 5000:
        RESET_GAP.clear()
    RESET_GAP[key] = time.time()
    return generic


@app.route("/api/admin/requests")
@need("admin")
def admin_requests():
    return jsonify(load().get("reset_requests", []))


@app.route("/api/admin/requests/approve", methods=["POST"])
@need("admin")
def admin_approve():
    name = str((request.get_json() or {}).get("username", ""))
    with lock:
        d = load()
        u = find_user(d, name)
        if not u or u["role"] == "admin":
            return err("User not found")
        temp = secrets.token_urlsafe(8)
        u["password"] = generate_password_hash(temp)
        u["must_change"] = True
        d["reset_requests"] = [r for r in d.get("reset_requests", []) if r["username"].lower() != u["username"].lower()]
        save(d)
    FAILS.pop(u["username"].lower(), None)
    return jsonify({"username": u["username"], "temp_password": temp})


@app.route("/api/admin/requests/dismiss", methods=["POST"])
@need("admin")
def admin_dismiss():
    name = str((request.get_json() or {}).get("username", "")).lower()
    with lock:
        d = load()
        d["reset_requests"] = [r for r in d.get("reset_requests", []) if r["username"].lower() != name]
        save(d)
    return jsonify({"ok": True})


@app.route("/api/admin/recovery-code", methods=["POST"])
@need("admin")
def admin_recovery_code():
    b = request.get_json() or {}
    with lock:
        d = load()
        u = find_user(d, session["username"])
        if not check_password_hash(u["password"], str(b.get("password", ""))):
            return err("Your password is wrong")
        code = _new_recovery(u)
        save(d)
    return jsonify({"code": code})


@app.route("/api/forgot-admin", methods=["POST"])
def forgot_admin():
    b = request.get_json() or {}
    name = str(b.get("username", "")).strip().lower()
    key = "rec:" + name
    wait = _rate_locked(key)
    if wait > 0:
        return err(f"Too many wrong attempts. Try again in {wait // 60 + 1} minute(s).", 429)
    code = str(b.get("code", "")).strip().upper()
    new = str(b.get("new", ""))
    with lock:
        d = load()
        u = find_user(d, name)
        if not u or u["role"] != "admin" or not u.get("recovery_hash") \
                or not check_password_hash(u["recovery_hash"], code):
            _rate_fail(key)
            return err("Username or recovery code is wrong", 401)
        if len(new) < 8 or new.lower() == u["username"].lower():
            return err("New password: at least 8 characters and different from your username")
        u["password"] = generate_password_hash(new)
        u.pop("must_change", None)
        next_code = _new_recovery(u)
        save(d)
    FAILS.pop(name, None)
    FAILS.pop(key, None)
    return jsonify({"ok": True, "new_code": next_code})


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
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", roll) or not re.fullmatch(r"[A-Za-z0-9_. -]{1,30}", klass):
        return "Roll no " + roll + ": 3-30 letters/numbers only. Class: letters, numbers, spaces, dots, dashes only"
    if find_user(d, roll):
        return "Roll no " + roll + " already exists"
    d["students"].append({"roll": roll, "name": name, "klass": klass})
    d["users"].append({"username": roll, "role": "student", "name": name,
                       "password": generate_password_hash(roll), "must_change": True})
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
                           "password": generate_password_hash(str(b.get("password") or user)), "must_change": True})
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
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,20}", code) or not re.fullmatch(r"[A-Za-z0-9_. -]{1,30}", klass):
        return err("Code: 2-20 letters/numbers. Class: letters, numbers, spaces, dots, dashes only")
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
        u["must_change"] = True
        save(d)
    return jsonify({"ok": True})


if __name__ == "__main__":
    # open the browser only once (not again after auto-reload)
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(debug=True, host="127.0.0.1", port=5000)

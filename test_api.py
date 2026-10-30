import os
import tempfile
import app as dash
from werkzeug.security import generate_password_hash

# temporary database: your real data.json is never touched
fd, tmp = tempfile.mkstemp(suffix=".json")
os.close(fd)
os.remove(tmp)
dash.DB = tmp
dash.init_db()
_d = dash.load()
for _u in _d["users"]:
    if _u["role"] == "admin":
        _u["password"] = generate_password_hash("admin123")
        _u.pop("must_change", None)
dash.save(_d)

passed = 0
total = 0


def check(name, cond):
    global passed, total
    total += 1
    passed += bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name)


def login(user, pw):
    _d = dash.load()
    for _u in _d["users"]:
        _u.pop("must_change", None)
    dash.save(_d)
    c = dash.app.test_client()
    r = c.post("/api/login", json={"username": user, "password": pw})
    return c, r


try:
    # ---------- not logged in ----------
    anon = dash.app.test_client()
    check("Anonymous: admin API blocked", anon.get("/api/admin/data").status_code == 401)
    check("Anonymous: student API blocked", anon.get("/api/student/me").status_code == 401)
    check("Wrong password rejected", login("admin", "wrong")[1].status_code == 401)

    # ---------- admin sets up the college ----------
    adm, r = login("admin", "admin123")
    check("Admin can log in", r.status_code == 200 and r.get_json()["role"] == "admin")
    adm.post("/api/admin/teachers", json={"username": "teach1", "name": "Teacher One"})
    adm.post("/api/admin/teachers", json={"username": "teach2", "name": "Teacher Two"})
    r = adm.post("/api/admin/students/bulk", json={"text": "ROLL1, Alice, C1\nROLL2, Bob, C1\nROLL3, Cara, C1"})
    check("Bulk add: 3 students", r.get_json()["added"] == 3)
    r = adm.post("/api/admin/subjects", json={"code": "SUB1", "name": "Python", "klass": "C1", "teacher": "teach1"})
    check("Admin adds a subject", r.status_code == 200)
    r = adm.post("/api/admin/subjects", json={"code": "SUB1", "name": "Again", "klass": "C1", "teacher": "teach1"})
    check("Duplicate subject code rejected", r.status_code == 400)
    r = adm.post("/api/admin/students", json={"roll": "roll1", "name": "X", "klass": "C1"})
    check("Duplicate roll no rejected (any letter case)", r.status_code == 400)
    check("Teacher with subjects cannot be deleted", adm.delete("/api/admin/teachers/teach1").status_code == 400)

    # ---------- everyone logs in ----------
    t1, r1 = login("teach1", "teach1")
    t2, r2 = login("teach2", "teach2")
    s1, r3 = login("ROLL1", "ROLL1")
    s2, r4 = login("ROLL2", "ROLL2")
    s3, r5 = login("ROLL3", "ROLL3")
    check("Teachers and students can log in (default password)",
          all(x.status_code == 200 for x in (r1, r2, r3, r4, r5)))

    # ---------- teacher marks 4 classes ----------
    days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    pres = {"ROLL1": [1, 1, 1, 1], "ROLL2": [1, 1, 1, 0], "ROLL3": [1, 1, 0, 0]}
    ok = True
    for i, day in enumerate(days):
        r = t1.post("/api/subjects/SUB1/attendance",
                    json={"date": day, "status": {k: bool(v[i]) for k, v in pres.items()}})
        ok = ok and r.status_code == 200
    check("Teacher saves 4 days of attendance", ok)

    # ---------- student results and the 75% maths ----------
    a = s1.get("/api/student/me").get_json()
    b = s2.get("/api/student/me").get_json()
    c = s3.get("/api/student/me").get_json()
    check("ROLL1: 100% green, can skip exactly 1 class",
          a["overall"]["percent"] == 100.0 and a["overall"]["status"] == "green" and a["overall"]["can_miss"] == 1)
    check("ROLL2: 75% yellow, cannot skip, needs 0",
          b["overall"]["percent"] == 75.0 and b["overall"]["status"] == "yellow"
          and b["overall"]["can_miss"] == 0 and b["overall"]["need"] == 0)
    check("ROLL3: 50% red, must attend next 4 classes",
          c["overall"]["percent"] == 50.0 and c["overall"]["status"] == "red" and c["overall"]["need"] == 4)
    check("Student sees only their own record", a["student"]["roll"] == "ROLL1")

    # ---------- saving the same date again must not duplicate ----------
    t1.post("/api/subjects/SUB1/attendance",
            json={"date": "2026-10-04", "status": {"ROLL1": True, "ROLL2": False, "ROLL3": False}})
    check("Re-saving a date updates it (still 4 classes)",
          s1.get("/api/student/me").get_json()["overall"]["total"] == 4)

    # ---------- security: who can do what ----------
    check("Student blocked from teacher API", s1.get("/api/subjects").status_code == 401)
    check("Student cannot save attendance",
          s1.post("/api/subjects/SUB1/attendance", json={"date": "2026-10-05", "status": {"ROLL1": True}}).status_code == 401)
    check("Teacher blocked from admin API", t1.get("/api/admin/data").status_code == 401)
    check("Teacher blocked from student API", t1.get("/api/student/me").status_code == 401)
    check("Other teacher cannot open this subject", t2.get("/api/subjects/SUB1/students").status_code == 404)
    check("Other teacher cannot save its attendance",
          t2.post("/api/subjects/SUB1/attendance", json={"date": "2026-10-06", "status": {"ROLL1": True}}).status_code == 404)
    check("Other teacher cannot enter its marks",
          t2.post("/api/subjects/SUB1/marks", json={"roll": "ROLL1", "marks": {"mid1": 10}}).status_code == 404)

    # ---------- marks ----------
    check("Teacher saves valid marks",
          t1.post("/api/subjects/SUB1/marks", json={"roll": "ROLL1", "marks": {"mid1": 25, "test": 8}}).status_code == 200)
    check("Mark above the maximum rejected",
          t1.post("/api/subjects/SUB1/marks", json={"roll": "ROLL1", "marks": {"mid1": 40}}).status_code == 400)
    check("Non-number mark rejected",
          t1.post("/api/subjects/SUB1/marks", json={"roll": "ROLL1", "marks": {"mid1": "abc"}}).status_code == 400)
    check("Student sees the saved marks",
          s1.get("/api/student/me").get_json()["subjects"][0]["marks"].get("mid1") == 25)

    # ---------- passwords ----------
    check("Wrong old password rejected",
          s2.post("/api/password", json={"old": "bad", "new": "newpass1"}).status_code == 400)
    check("Student changes own password",
          s1.post("/api/password", json={"old": "ROLL1", "new": "newpass1"}).status_code == 200)
    check("New password works, old one does not",
          login("ROLL1", "newpass1")[1].status_code == 200 and login("ROLL1", "ROLL1")[1].status_code == 401)
    adm.post("/api/admin/reset", json={"username": "ROLL1"})
    check("Admin reset password works", login("ROLL1", "ROLL1")[1].status_code == 200)

    # ---------- admin overview and delete ----------
    check("Admin overview lists 3 students", len(adm.get("/api/admin/overview").get_json()) == 3)
    adm.delete("/api/admin/students/ROLL3")
    data = dash.load()
    check("Deleting a student removes login and attendance",
          len(adm.get("/api/admin/overview").get_json()) == 2
          and dash.find_user(data, "ROLL3") is None
          and all("ROLL3" not in rec["status"] for rec in data["attendance"]))

    # ---------- pages and files ----------
    pages = ["/", "/student", "/teacher", "/admin", "/static/common.js",
             "/static/style.css", "/static/theme3d.css"]
    check("All pages and files load", all(anon.get(p).status_code == 200 for p in pages))
    # ---------- security: lockout, forced password change, input checks ----------
    dash.FAILS.clear()
    codes = [login("ROLL2", "bad")[1].status_code for _ in range(5)]
    check("5 wrong passwords are refused", codes == [401] * 5)
    check("6th attempt is locked out (429)", login("ROLL2", "ROLL2")[1].status_code == 429)
    dash.FAILS.clear()
    check("Login works again after the lock ends", login("ROLL2", "ROLL2")[1].status_code == 200)

    adm.post("/api/admin/students", json={"roll": "NEW1", "name": "New Kid", "klass": "C1"})
    nc = dash.app.test_client()
    r = nc.post("/api/login", json={"username": "NEW1", "password": "NEW1"})
    check("New account must change its password first", r.get_json()["must_change"] is True)
    check("API is blocked until the password is changed", nc.get("/api/student/me").status_code == 403)
    check("Too-short new password is rejected",
          nc.post("/api/password", json={"old": "NEW1", "new": "short"}).status_code == 400)
    check("Strong new password is accepted",
          nc.post("/api/password", json={"old": "NEW1", "new": "Strong#Pass1"}).status_code == 200)
    check("API works after the change", nc.get("/api/student/me").status_code == 200)
    check("Roll no with spaces or symbols is rejected",
          adm.post("/api/admin/students", json={"roll": "a b'c", "name": "X", "klass": "C1"}).status_code == 400)
    check("Bad subject code is rejected",
          adm.post("/api/admin/subjects", json={"code": "S U<B", "name": "X", "klass": "C1", "teacher": "teach1"}).status_code == 400)
    check("Security headers are sent", anon.get("/").headers.get("X-Content-Type-Options") == "nosniff")
    # ---------- forgot password ----------
    dash.RESET_GAP.clear()
    dash.FAILS.clear()
    fp = dash.app.test_client()
    r1 = fp.post("/api/forgot", json={"username": "ROLL2"})
    r2 = fp.post("/api/forgot", json={"username": "nobody-here"})
    check("Forgot password answers the same for real and unknown users", r1.get_json() == r2.get_json())
    reqs = adm.get("/api/admin/requests").get_json()
    check("Request reaches the admin (only for the real user)", [x["username"] for x in reqs] == ["ROLL2"])
    ap = adm.post("/api/admin/requests/approve", json={"username": "ROLL2"}).get_json()
    check("Admin approves and gets a temporary password", len(ap["temp_password"]) >= 8)
    c2 = dash.app.test_client()
    rr = c2.post("/api/login", json={"username": "ROLL2", "password": ap["temp_password"]})
    check("Temporary password works and forces a change", rr.status_code == 200 and rr.get_json()["must_change"] is True)
    check("Request list is empty after approval", adm.get("/api/admin/requests").get_json() == [])

    code = adm.post("/api/admin/recovery-code", json={"password": "admin123"}).get_json()["code"]
    check("Admin can generate a recovery code", len(code) == 19)
    fa = dash.app.test_client()
    check("Wrong recovery code is refused",
          fa.post("/api/forgot-admin", json={"username": "admin", "code": "AAAA-AAAA-AAAA-AAAA", "new": "Brand#New123"}).status_code == 401)
    r = fa.post("/api/forgot-admin", json={"username": "admin", "code": code, "new": "Brand#New123"})
    check("Recovery code resets the admin password", r.status_code == 200 and len(r.get_json()["new_code"]) == 19)
    check("New admin password works", login("admin", "Brand#New123")[1].status_code == 200)
    check("Old recovery code no longer works",
          fa.post("/api/forgot-admin", json={"username": "admin", "code": code, "new": "Another#Pass9"}).status_code == 401)
finally:
    if os.path.exists(tmp):
        os.remove(tmp)

print(f"\n{passed}/{total} tests passed")

import os
import tempfile
import app as dash

# temporary database: your real data.json is never touched
fd, tmp = tempfile.mkstemp(suffix=".json")
os.close(fd)
os.remove(tmp)
dash.DB = tmp
dash.init_db()

passed = 0
total = 0


def check(name, cond):
    global passed, total
    total += 1
    passed += bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name)


def login(user, pw):
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
    r = adm.post("/api/admin/students/bulk", json={"text": "R1, Alice, C1\nR2, Bob, C1\nR3, Cara, C1"})
    check("Bulk add: 3 students", r.get_json()["added"] == 3)
    r = adm.post("/api/admin/subjects", json={"code": "SUB1", "name": "Python", "klass": "C1", "teacher": "teach1"})
    check("Admin adds a subject", r.status_code == 200)
    r = adm.post("/api/admin/subjects", json={"code": "SUB1", "name": "Again", "klass": "C1", "teacher": "teach1"})
    check("Duplicate subject code rejected", r.status_code == 400)
    r = adm.post("/api/admin/students", json={"roll": "r1", "name": "X", "klass": "C1"})
    check("Duplicate roll no rejected (any letter case)", r.status_code == 400)
    check("Teacher with subjects cannot be deleted", adm.delete("/api/admin/teachers/teach1").status_code == 400)

    # ---------- everyone logs in ----------
    t1, r1 = login("teach1", "teach1")
    t2, r2 = login("teach2", "teach2")
    s1, r3 = login("R1", "R1")
    s2, r4 = login("R2", "R2")
    s3, r5 = login("R3", "R3")
    check("Teachers and students can log in (default password)",
          all(x.status_code == 200 for x in (r1, r2, r3, r4, r5)))

    # ---------- teacher marks 4 classes ----------
    days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    pres = {"R1": [1, 1, 1, 1], "R2": [1, 1, 1, 0], "R3": [1, 1, 0, 0]}
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
    check("R1: 100% green, can skip exactly 1 class",
          a["overall"]["percent"] == 100.0 and a["overall"]["status"] == "green" and a["overall"]["can_miss"] == 1)
    check("R2: 75% yellow, cannot skip, needs 0",
          b["overall"]["percent"] == 75.0 and b["overall"]["status"] == "yellow"
          and b["overall"]["can_miss"] == 0 and b["overall"]["need"] == 0)
    check("R3: 50% red, must attend next 4 classes",
          c["overall"]["percent"] == 50.0 and c["overall"]["status"] == "red" and c["overall"]["need"] == 4)
    check("Student sees only their own record", a["student"]["roll"] == "R1")

    # ---------- saving the same date again must not duplicate ----------
    t1.post("/api/subjects/SUB1/attendance",
            json={"date": "2026-10-04", "status": {"R1": True, "R2": False, "R3": False}})
    check("Re-saving a date updates it (still 4 classes)",
          s1.get("/api/student/me").get_json()["overall"]["total"] == 4)

    # ---------- security: who can do what ----------
    check("Student blocked from teacher API", s1.get("/api/subjects").status_code == 401)
    check("Student cannot save attendance",
          s1.post("/api/subjects/SUB1/attendance", json={"date": "2026-10-05", "status": {"R1": True}}).status_code == 401)
    check("Teacher blocked from admin API", t1.get("/api/admin/data").status_code == 401)
    check("Teacher blocked from student API", t1.get("/api/student/me").status_code == 401)
    check("Other teacher cannot open this subject", t2.get("/api/subjects/SUB1/students").status_code == 404)
    check("Other teacher cannot save its attendance",
          t2.post("/api/subjects/SUB1/attendance", json={"date": "2026-10-06", "status": {"R1": True}}).status_code == 404)
    check("Other teacher cannot enter its marks",
          t2.post("/api/subjects/SUB1/marks", json={"roll": "R1", "marks": {"mid1": 10}}).status_code == 404)

    # ---------- marks ----------
    check("Teacher saves valid marks",
          t1.post("/api/subjects/SUB1/marks", json={"roll": "R1", "marks": {"mid1": 25, "test": 8}}).status_code == 200)
    check("Mark above the maximum rejected",
          t1.post("/api/subjects/SUB1/marks", json={"roll": "R1", "marks": {"mid1": 40}}).status_code == 400)
    check("Non-number mark rejected",
          t1.post("/api/subjects/SUB1/marks", json={"roll": "R1", "marks": {"mid1": "abc"}}).status_code == 400)
    check("Student sees the saved marks",
          s1.get("/api/student/me").get_json()["subjects"][0]["marks"].get("mid1") == 25)

    # ---------- passwords ----------
    check("Wrong old password rejected",
          s2.post("/api/password", json={"old": "bad", "new": "newpass1"}).status_code == 400)
    check("Student changes own password",
          s1.post("/api/password", json={"old": "R1", "new": "newpass1"}).status_code == 200)
    check("New password works, old one does not",
          login("R1", "newpass1")[1].status_code == 200 and login("R1", "R1")[1].status_code == 401)
    adm.post("/api/admin/reset", json={"username": "R1"})
    check("Admin reset password works", login("R1", "R1")[1].status_code == 200)

    # ---------- admin overview and delete ----------
    check("Admin overview lists 3 students", len(adm.get("/api/admin/overview").get_json()) == 3)
    adm.delete("/api/admin/students/R3")
    data = dash.load()
    check("Deleting a student removes login and attendance",
          len(adm.get("/api/admin/overview").get_json()) == 2
          and dash.find_user(data, "R3") is None
          and all("R3" not in rec["status"] for rec in data["attendance"]))

    # ---------- pages and files ----------
    pages = ["/", "/student", "/teacher", "/admin", "/static/common.js",
             "/static/style.css", "/static/theme3d.css"]
    check("All pages and files load", all(anon.get(p).status_code == 200 for p in pages))
finally:
    if os.path.exists(tmp):
        os.remove(tmp)

print(f"\n{passed}/{total} tests passed")

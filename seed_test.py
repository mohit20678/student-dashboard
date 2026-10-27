import sys
from datetime import date, timedelta
from werkzeug.security import generate_password_hash
import app as a

SUBJ = [("TST101", "Python"), ("TST102", "Maths"), ("TST103", "DBMS")]
# code: (classes held, present rule for TEST001, present rule for TEST002)
RULES = {
    "TST101": (20, lambda i: i % 3 != 0, lambda i: i % 2 == 0),
    "TST102": (20, lambda i: i not in (4, 11), lambda i: i % 4 != 0),
    "TST103": (12, lambda i: i % 4 != 0, lambda i: i != 1),
}


def remove():
    with a.lock:
        d = a.load()
        d["students"] = [s for s in d["students"] if not s["roll"].startswith("TEST")]
        d["users"] = [u for u in d["users"] if u["username"] not in ("TEST001", "TEST002", "testteacher")]
        d["subjects"] = [s for s in d["subjects"] if not s["code"].startswith("TST")]
        d["attendance"] = [r for r in d["attendance"] if not r["subject"].startswith("TST")]
        d["marks"] = {k: v for k, v in d["marks"].items() if not k.startswith("TST")}
        a.save(d)


def seed():
    remove()
    with a.lock:
        d = a.load()
        a.add_student(d, "TEST001", "Test Student One", "TEST-A")
        a.add_student(d, "TEST002", "Test Student Two", "TEST-A")
        d["users"].append({"username": "testteacher", "role": "teacher", "name": "Test Teacher",
                           "password": generate_password_hash("testteacher")})
        for code, name in SUBJ:
            d["subjects"].append({"code": code, "name": name, "klass": "TEST-A", "teacher": "testteacher"})
            n, r1, r2 = RULES[code]
            for i in range(n):
                day = (date(2026, 9, 1) + timedelta(days=i)).isoformat()
                d["attendance"].append({"date": day, "subject": code,
                                        "status": {"TEST001": r1(i), "TEST002": r2(i)}})
        d["marks"]["TST101|TEST001"] = {"mid1": 24, "mid2": 26, "internal": 8, "test": 9}
        d["marks"]["TST102|TEST001"] = {"mid1": 27, "mid2": 25}
        a.save(d)


if len(sys.argv) > 1 and sys.argv[1] == "remove":
    remove()
    print("Test data removed.")
else:
    seed()
    print("Test data added. Student login: TEST001 / TEST001")

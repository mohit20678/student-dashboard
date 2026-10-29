import os
import app as a

def has(path, *words):
    if not os.path.exists(path):
        return "FILE MISSING"
    t = open(path, encoding="utf-8").read()
    miss = [w for w in words if w not in t]
    return "OK" if not miss else "MISSING: " + ", ".join(miss)

print("=== FILES AND FEATURES ===")
checks = [
    ("app.py", ["def analyse(", "/api/subjects/<code>/analysis", "/api/admin/merit", "MAX ="]),
    ("static/common.js", ["function analysisHTML", "function requireRole"]),
    ("static/theme3d.css", [".rank1 td", ".login-card"]),
    ("static/style.css", [":root"]),
    ("static/login.html", ["doLogin"]),
    ("static/student.html", ["runCalc", "pieCanvas"]),
    ("static/teacher.html", ['id="an"', "loadAnalysis", "saveAllMarks"]),
    ("static/admin.html", ['id="me"', "meOpen", "loadMerit", "bulkAdd"]),
    (".gitignore", ["data.json", ".secret_key", "backups/", "venv/"]),
    ("test_api.py", ["tests passed"]),
]
for path, words in checks:
    print(f"  {path:<22} {has(path, *words)}")

routes = sorted(r.rule for r in a.app.url_map.iter_rules() if r.rule.startswith("/api"))
print(f"\n=== APP LOADS: {len(routes)} API routes ===")
print("  analysis/merit routes:", [r for r in routes if "analysis" in r or "merit" in r])

d = a.load()
teachers = [u for u in d["users"] if u["role"] == "teacher"]
cls = {}
for s in d["students"]:
    cls[s["klass"]] = cls.get(s["klass"], 0) + 1

print("\n=== DATA IN data.json ===")
print(f"  students: {len(d['students'])} | teachers: {len(teachers)} | subjects: {len(d['subjects'])}")
print(f"  attendance records: {len(d['attendance'])} | marks entries: {len(d['marks'])}")

print("\n  Teachers:")
for u in teachers:
    print(f"    {u['username']:<12} {u['name']}")

print("\n  Students:")
for s in d["students"]:
    print(f"    {s['roll']:<10} {s['name']:<22} {s['klass']}")

print("\n  Subjects:")
for s in d["subjects"]:
    held = sum(1 for r in d["attendance"] if r["subject"] == s["code"])
    marked = sum(1 for k in d["marks"] if k.startswith(s["code"] + "|"))
    t = next((u["name"] for u in teachers if u["username"] == s["teacher"]), s["teacher"])
    print(f"    {s['code']} {s['name']} | class {s['klass']} ({cls.get(s['klass'], 0)} students) | teacher {t} | classes marked: {held} | students with marks: {marked}")

problems = []
if not d["students"]:
    problems.append("No students added yet")
for s in d["subjects"]:
    if s["klass"] not in cls:
        problems.append(f"Subject {s['code']} has no students in class {s['klass']} (check the class spelling)")
for k in cls:
    if not any(s["klass"] == k for s in d["subjects"]):
        problems.append(f"Class {k} has students but no subjects")
print("\n=== PROBLEMS ===")
print("  " + ("\n  ".join(problems) if problems else "None found"))

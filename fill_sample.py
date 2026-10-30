import os
import random
import shutil
import sys
import time
from datetime import date, timedelta
import app as a

mode = sys.argv[1] if len(sys.argv) > 1 else "fill"
random.seed(42)

with a.lock:
    d = a.load()
    os.makedirs("backups", exist_ok=True)
    shutil.copy(a.DB, "backups/before_sample_" + time.strftime("%Y%m%d_%H%M") + ".json")

    if mode == "clear":
        d["attendance"] = [r for r in d["attendance"] if not r.get("sample")]
        for k in d.get("sample_marks", []):
            d["marks"].pop(k, None)
        d["sample_marks"] = []
        a.save(d)
        sys.exit("Sample data removed. Real entries were not touched.")

    # last 20 weekdays up to yesterday
    days, day = [], date(2026, 10, 28)
    while len(days) < 20:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.sort()

    attend_p = [0.97, 0.90, 0.80, 0.72, 0.60]   # how often each student comes
    ability = [0.90, 0.80, 0.80, 0.60, 0.50]    # marks level (equal values give tied marks)
    have = {(r["subject"], r["date"]) for r in d["attendance"]}
    clamp = lambda v, hi: max(0, min(hi, v))
    new_att = new_marks = 0

    for sub in d["subjects"]:
        studs = [s for s in d["students"] if s["klass"] == sub["klass"]]
        for day in days:
            if (sub["code"], day.isoformat()) in have:
                continue
            status = {s["roll"]: random.random() < attend_p[i % 5] for i, s in enumerate(studs)}
            d["attendance"].append({"date": day.isoformat(), "subject": sub["code"], "status": status, "sample": True})
            new_att += 1
        for i, s in enumerate(studs):
            key = sub["code"] + "|" + s["roll"]
            if key in d["marks"]:
                continue
            ab = ability[i % 5]
            d["marks"][key] = {
                "mid1": clamp(round(a.MAX["mid1"] * ab), a.MAX["mid1"]),
                "mid2": clamp(round(a.MAX["mid2"] * ab) + random.randint(-3, 3), a.MAX["mid2"]),
                "internal": clamp(round(a.MAX["internal"] * ab) + random.randint(-1, 1), a.MAX["internal"]),
                "test": clamp(round(a.MAX["test"] * ab) + random.randint(-1, 1), a.MAX["test"]),
                "sem": clamp(round(a.MAX["sem"] * ab) + random.randint(-6, 6), a.MAX["sem"]),
            }
            d.setdefault("sample_marks", []).append(key)
            new_marks += 1
    a.save(d)

print(f"Added {new_att} attendance records and marks for {new_marks} student-subject pairs.\n")
print("Overall attendance now:")
for st in d["students"]:
    _, o = a.student_stats(d, st)
    print(f"  {st['roll']}  {st['name']:<20} {o['percent']:>5}%  {o['status']}")

# -*- coding: utf-8 -*-
"""
Генерирует data.json для мини-аппа: расписание всех групп и преподавателей.
Запускать при обновлении расписания (или по расписанию раз в несколько часов):
    python build_data.py
"""
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SUBDOMAIN = "ttpu"
OUT = Path(__file__).parent / "data.json"

LESSON_TYPES = {"lec": "lec", "mar": "lec", "prac": "prac", "sem": "sem", "lab": "lab"}


def api_post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch_tables():
    url = f"https://{SUBDOMAIN}.edupage.org/timetable/server/ttviewer.js?__func=getTTViewerData"
    data = api_post(url, {"__args": [None, date.today().year], "__gsh": "00000000"})
    timetables = [t for t in data["r"]["regular"]["timetables"] if not t.get("hidden")]
    timetables.sort(key=lambda t: t.get("datefrom", ""))
    tt = timetables[-1]
    url = f"https://{SUBDOMAIN}.edupage.org/timetable/server/regulartt.js?__func=regularttGetData"
    data = api_post(url, {"__args": [None, tt["tt_num"]], "__gsh": "00000000"})
    tables = {t["id"]: t["data_rows"] for t in data["r"]["dbiAccessorRes"]["tables"]}
    return tables, tt.get("text", "")


def split_subject(subject):
    m = re.match(r"^(.*?)\s*\((\w+)\)\s*$", subject)
    if m and m.group(2).lower() in LESSON_TYPES:
        return m.group(1), LESSON_TYPES[m.group(2).lower()]
    return subject, ""


def main():
    tables, week_text = fetch_tables()
    periods = {p["period"]: p for p in tables["periods"]}
    subjects = {s["id"]: s for s in tables["subjects"]}
    teachers = {t["id"]: t for t in tables["teachers"]}
    classrooms = {c["id"]: c for c in tables["classrooms"]}
    class_names = {c["id"]: c["name"] for c in tables["classes"]}
    groups = {g["id"]: g for g in tables["groups"]}
    lessons = {l["id"]: l for l in tables["lessons"]}

    by_class = {}    # class_id -> [ [entries]*6 ]
    by_teacher = {}  # teacher_id -> [ [entries]*6 ]

    for card in tables["cards"]:
        lesson = lessons.get(card["lessonid"])
        if not lesson or not card.get("period"):
            continue
        period_num = int(card["period"])
        duration = int(lesson.get("durationperiods", 1) or 1)
        start = periods.get(str(period_num), {}).get("starttime", "?")
        end = periods.get(str(period_num + duration - 1),
                          periods.get(str(period_num), {})).get("endtime", "?")
        subj_name, subj_type = split_subject(
            subjects.get(lesson["subjectid"], {}).get("name", "?"))
        teacher_names = [teachers[t].get("short") or teachers[t].get("name", "")
                         for t in lesson.get("teacherids", []) if t in teachers]
        rooms = ", ".join(classrooms[r].get("short") or classrooms[r].get("name", "")
                          for r in card.get("classroomids", []) if r in classrooms)
        lesson_class_names = ", ".join(class_names.get(c, "?") for c in lesson.get("classids", []))

        base = {"p": period_num, "t": f"{start}–{end}", "s": subj_name, "x": subj_type, "r": rooms}
        days = [i for i, bit in enumerate(card["days"][:6]) if bit == "1"]

        for cid in lesson.get("classids", []):
            subgroups = [g.get("name", "") for gid in lesson.get("groupids", [])
                         if (g := groups.get(gid)) and g.get("classid") == cid
                         and not g.get("entireclass")]
            e = dict(base, tc=", ".join(teacher_names), g=", ".join(filter(None, subgroups)))
            for d in days:
                by_class.setdefault(cid, [[] for _ in range(6)])[d].append(e)

        for tid in lesson.get("teacherids", []):
            e = dict(base, c=lesson_class_names)
            for d in days:
                by_teacher.setdefault(tid, [[] for _ in range(6)])[d].append(e)

    out = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "week": week_text,
        "classes": [],
        "teachers": [],
    }
    for cid, days in by_class.items():
        for d in days:
            d.sort(key=lambda e: e["p"])
        out["classes"].append({"name": class_names.get(cid, "?"), "days": days})
    out["classes"].sort(key=lambda c: c["name"])
    for tid, days in by_teacher.items():
        for d in days:
            d.sort(key=lambda e: e["p"])
        t = teachers.get(tid, {})
        out["teachers"].append({"name": t.get("short") or t.get("name", "?"), "days": days})
    out["teachers"].sort(key=lambda t: t["name"])

    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size = OUT.stat().st_size
    print(f"data.json: {len(out['classes'])} groups, {len(out['teachers'])} teachers, "
          f"{size // 1024} KB, {week_text}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Генерирует data.json для мини-аппа: расписание всех групп и преподавателей.
Запускать при обновлении расписания (или по расписанию раз в несколько часов):
    python build_data.py

EduPage хранит расписание версиями, у каждой своя дата начала. Берём ту, что
действует сегодня, и — если она уже опубликована — следующую. Пока следующей
версии нет, блок "next" в data.json просто отсутствует, и мини-апп не показывает
переключатель недель.
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


def list_timetables():
    """Все видимые версии расписания, по возрастанию даты начала."""
    url = f"https://{SUBDOMAIN}.edupage.org/timetable/server/ttviewer.js?__func=getTTViewerData"
    data = api_post(url, {"__args": [None, date.today().year], "__gsh": "00000000"})
    tts = [t for t in data["r"]["regular"]["timetables"] if not t.get("hidden")]
    tts.sort(key=lambda t: t.get("datefrom", ""))
    return tts


def pick_versions(tts):
    """Текущая версия — последняя из начавшихся; следующая — первая из будущих."""
    today = date.today().isoformat()
    current = None
    upcoming = None
    for t in tts:
        df = t.get("datefrom", "")
        if not df:
            continue
        if df <= today:
            current = t
        elif upcoming is None:
            upcoming = t
    if current is None and tts:
        current = tts[-1]          # ни одна ещё не началась — показываем ближайшую
        if upcoming is current:
            upcoming = None
    return current, upcoming


def fetch_tables(tt_num):
    url = f"https://{SUBDOMAIN}.edupage.org/timetable/server/regulartt.js?__func=regularttGetData"
    data = api_post(url, {"__args": [None, tt_num], "__gsh": "00000000"})
    return {t["id"]: t["data_rows"] for t in data["r"]["dbiAccessorRes"]["tables"]}


def split_subject(subject):
    m = re.match(r"^(.*?)\s*\((\w+)\)\s*$", subject)
    if m and m.group(2).lower() in LESSON_TYPES:
        return m.group(1), LESSON_TYPES[m.group(2).lower()]
    return subject, ""


def build_week(tables):
    """Разворачивает одну версию расписания в списки групп и преподавателей."""
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

    out_classes = []
    for cid, days in by_class.items():
        for d in days:
            d.sort(key=lambda e: e["p"])
        out_classes.append({"name": class_names.get(cid, "?"), "days": days})
    out_classes.sort(key=lambda c: c["name"])

    out_teachers = []
    for tid, days in by_teacher.items():
        for d in days:
            d.sort(key=lambda e: e["p"])
        t = teachers.get(tid, {})
        out_teachers.append({"name": t.get("short") or t.get("name", "?"), "days": days})
    out_teachers.sort(key=lambda t: t["name"])

    return out_classes, out_teachers


def main():
    tts = list_timetables()
    current, upcoming = pick_versions(tts)
    if not current:
        raise SystemExit("нет ни одной видимой версии расписания")

    classes, teachers = build_week(fetch_tables(current["tt_num"]))
    out = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "week": current.get("text", ""),
        "from": current.get("datefrom", ""),
        "classes": classes,
        "teachers": teachers,
    }

    # Следующая неделя попадает в файл, только если она уже выложена на EduPage.
    # Нет версии — нет ключа "next", и переключатель недель в мини-аппе не появится.
    if upcoming:
        n_classes, n_teachers = build_week(fetch_tables(upcoming["tt_num"]))
        out["next"] = {
            "week": upcoming.get("text", ""),
            "from": upcoming.get("datefrom", ""),
            "classes": n_classes,
            "teachers": n_teachers,
        }

    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size = OUT.stat().st_size
    nxt = f", next: {upcoming.get('text', '')}" if upcoming else ", next: не опубликована"
    print(f"data.json: {len(classes)} groups, {len(teachers)} teachers, "
          f"{size // 1024} KB, {out['week']}{nxt}")


if __name__ == "__main__":
    main()

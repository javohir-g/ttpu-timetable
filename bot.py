# -*- coding: utf-8 -*-
"""
Парсер расписания EduPage (ttpu.edupage.org) с отправкой в Telegram.

Использование:
    python bot.py today      - расписание на сегодня
    python bot.py tomorrow   - расписание на завтра
    python bot.py week       - расписание на всю неделю
    python bot.py --dry-run today   - показать в консоли, не отправлять

Настройки в config.json (рядом со скриптом).
"""
import json
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import urllib.request
from datetime import date, timedelta
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def api_post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def get_current_tt_num(subdomain):
    """Номер актуального расписания (свежая неделя)."""
    url = f"https://{subdomain}.edupage.org/timetable/server/ttviewer.js?__func=getTTViewerData"
    data = api_post(url, {"__args": [None, date.today().year], "__gsh": "00000000"})
    regular = data["r"]["regular"]
    timetables = [t for t in regular["timetables"] if not t.get("hidden")]
    # берём таблицу с самой поздней датой начала, не позже сегодняшнего дня + 7
    timetables.sort(key=lambda t: t.get("datefrom", ""))
    return timetables[-1]["tt_num"], timetables[-1].get("text", "")


def fetch_timetable(subdomain, tt_num):
    url = f"https://{subdomain}.edupage.org/timetable/server/regulartt.js?__func=regularttGetData"
    data = api_post(url, {"__args": [None, tt_num], "__gsh": "00000000"})
    tables = {t["id"]: t["data_rows"] for t in data["r"]["dbiAccessorRes"]["tables"]}
    return tables


def build_schedule(tables, class_name):
    """Возвращает {day_index: [lesson, ...]} для указанной группы."""
    classes = {c["name"]: c["id"] for c in tables["classes"]}
    if class_name not in classes:
        available = ", ".join(sorted(classes))
        raise SystemExit(f"Группа '{class_name}' не найдена. Доступные: {available}")
    class_id = classes[class_name]

    periods = {p["period"]: p for p in tables["periods"]}
    subjects = {s["id"]: s for s in tables["subjects"]}
    teachers = {t["id"]: t for t in tables["teachers"]}
    classrooms = {c["id"]: c for c in tables["classrooms"]}
    groups = {g["id"]: g for g in tables["groups"]}
    lessons = {l["id"]: l for l in tables["lessons"]}

    schedule = {}  # day_index -> list of dicts
    for card in tables["cards"]:
        lesson = lessons.get(card["lessonid"])
        if not lesson or class_id not in lesson.get("classids", []):
            continue
        if not card.get("period"):  # неразмещённая карточка
            continue

        subject = subjects.get(lesson["subjectid"], {})
        teacher_names = [
            teachers[tid].get("short") or teachers[tid].get("name", "")
            for tid in lesson.get("teacherids", [])
            if tid in teachers
        ]
        room_names = [
            classrooms[rid].get("short") or classrooms[rid].get("name", "")
            for rid in card.get("classroomids", [])
            if rid in classrooms
        ]
        # подгруппа (если урок не для всей группы)
        group_names = []
        for gid in lesson.get("groupids", []):
            g = groups.get(gid)
            if g and g.get("classid") == class_id and not g.get("entireclass"):
                group_names.append(g.get("name", ""))

        period_num = int(card["period"])
        duration = int(lesson.get("durationperiods", 1) or 1)
        start = periods.get(str(period_num), {}).get("starttime", "?")
        end_period = str(period_num + duration - 1)
        end = periods.get(end_period, periods.get(str(period_num), {})).get("endtime", "?")

        entry = {
            "period": period_num,
            "time": f"{start}–{end}",
            "subject": subject.get("name") or subject.get("short", "?"),
            "teachers": ", ".join(teacher_names),
            "rooms": ", ".join(room_names),
            "groups": ", ".join(n for n in group_names if n),
        }
        for day_index, bit in enumerate(card["days"]):
            if bit == "1":
                schedule.setdefault(day_index, []).append(entry)

    for day in schedule.values():
        day.sort(key=lambda e: e["period"])
    return schedule


def format_day(schedule, day_index, class_name, day_date=None):
    title = DAY_NAMES[day_index] if day_index < len(DAY_NAMES) else f"День {day_index + 1}"
    date_str = f" ({day_date.strftime('%d.%m.%Y')})" if day_date else ""
    lines = [f"📅 <b>{class_name} — {title}{date_str}</b>", ""]
    day = schedule.get(day_index)
    if not day:
        lines.append("🎉 Занятий нет!")
        return "\n".join(lines)
    for e in day:
        line = f"🕐 <b>{e['time']}</b>  {e['subject']}"
        details = []
        if e["teachers"]:
            details.append(f"👤 {e['teachers']}")
        if e["rooms"]:
            details.append(f"🚪 {e['rooms']}")
        if e["groups"]:
            details.append(f"({e['groups']})")
        lines.append(line)
        if details:
            lines.append("      " + "  ".join(details))
    return "\n".join(lines)


def format_week(schedule, class_name):
    parts = []
    for day_index in range(6):
        if day_index in schedule:
            parts.append(format_day(schedule, day_index, class_name))
    return "\n\n".join(parts) if parts else f"📅 <b>{class_name}</b>\n\n🎉 На этой неделе занятий нет!"


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram ограничивает сообщение 4096 символами — режем по дням
    chunks, current = [], ""
    for block in text.split("\n\n"):
        candidate = (current + "\n\n" + block) if current else block
        if len(candidate) > 4000:
            if current:
                chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)

    for chunk in chunks:
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
        api_post(url, payload)


def main():
    args = [a for a in sys.argv[1:]]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    mode = args[0] if args else "today"

    cfg = load_config()
    subdomain = cfg.get("edupage_subdomain", "ttpu")
    class_name = cfg["class_name"]

    tt_num, tt_text = get_current_tt_num(subdomain)
    tables = fetch_timetable(subdomain, tt_num)
    schedule = build_schedule(tables, class_name)

    today = date.today()
    if mode == "week":
        text = format_week(schedule, class_name)
    else:
        target = today + timedelta(days=1) if mode == "tomorrow" else today
        day_index = target.weekday()  # 0 = понедельник
        if day_index == 6:  # воскресенье — занятий нет
            text = f"📅 <b>{class_name} — Воскресенье ({target.strftime('%d.%m.%Y')})</b>\n\n🎉 Выходной!"
        else:
            text = format_day(schedule, day_index, class_name, target)

    if dry_run:
        import re
        print(re.sub(r"</?b>", "", text))
        return

    token = cfg["telegram_bot_token"]
    chat_id = cfg["telegram_chat_id"]
    if not token or "PASTE" in token:
        raise SystemExit("Заполни telegram_bot_token и telegram_chat_id в config.json")
    send_telegram(token, chat_id, text)
    print(f"Отправлено в Telegram ({mode}, расписание: {tt_text})")


if __name__ == "__main__":
    main()

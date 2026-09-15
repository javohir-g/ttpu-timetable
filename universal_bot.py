# -*- coding: utf-8 -*-
"""
Универсальный Telegram-бот расписания EduPage (ttpu.edupage.org).

Любой студент или преподаватель пишет боту /start, выбирает себя —
и получает расписание командами /today, /tomorrow, /week.

Запуск:  python universal_bot.py
Токен бота берётся из config.json (telegram_bot_token).
Настройки пользователей хранятся в users.json.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
USERS_PATH = BASE_DIR / "users.json"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CACHE_TTL = 30 * 60  # перезагружать расписание раз в 30 минут

HELP_TEXT = (
    "📖 <b>Commands:</b>\n"
    "/today — today's timetable\n"
    "/tomorrow — tomorrow's timetable\n"
    "/week — full week\n"
    "/change — change group / teacher\n"
    "/help — this help"
)


# ---------------------------------------------------------------- EduPage API

def api_post(url, payload, timeout=60):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class EdupageData:
    """Кэш данных расписания: группы, преподаватели, уроки."""

    def __init__(self, subdomain):
        self.subdomain = subdomain
        self.fetched_at = 0
        self.tables = None
        self.tt_text = ""

    def refresh_if_stale(self):
        if self.tables is not None and time.time() - self.fetched_at < CACHE_TTL:
            return
        url = f"https://{self.subdomain}.edupage.org/timetable/server/ttviewer.js?__func=getTTViewerData"
        data = api_post(url, {"__args": [None, date.today().year], "__gsh": "00000000"})
        timetables = [t for t in data["r"]["regular"]["timetables"] if not t.get("hidden")]
        timetables.sort(key=lambda t: t.get("datefrom", ""))
        tt_num = timetables[-1]["tt_num"]
        self.tt_text = timetables[-1].get("text", "")

        url = f"https://{self.subdomain}.edupage.org/timetable/server/regulartt.js?__func=regularttGetData"
        data = api_post(url, {"__args": [None, tt_num], "__gsh": "00000000"})
        self.tables = {t["id"]: t["data_rows"] for t in data["r"]["dbiAccessorRes"]["tables"]}
        self.fetched_at = time.time()
        print(f"[edupage] загружено расписание: {self.tt_text}")

    # --- справочники ---

    def classes(self):
        """[(id, name)] отсортировано по имени."""
        self.refresh_if_stale()
        return sorted(((c["id"], c["name"]) for c in self.tables["classes"]), key=lambda x: x[1])

    def class_prefixes(self):
        """Уникальные буквенные префиксы групп (IT, SE, MSc_DS...)."""
        prefixes = {}
        for _, name in self.classes():
            m = re.match(r"[A-Za-z_]+", name)
            if m:
                prefixes.setdefault(m.group(0), 0)
                prefixes[m.group(0)] += 1
        return sorted(prefixes)

    def classes_by_prefix(self, prefix):
        return [(cid, name) for cid, name in self.classes()
                if re.match(r"[A-Za-z_]+", name) and re.match(r"[A-Za-z_]+", name).group(0) == prefix]

    def find_class(self, name):
        for cid, cname in self.classes():
            if cname.lower() == name.lower():
                return cid, cname
        return None

    def search_teachers(self, query):
        """Поиск преподавателя по подстроке имени."""
        self.refresh_if_stale()
        q = query.strip().lower()
        result = []
        for t in self.tables["teachers"]:
            hay = f"{t.get('name', '')} {t.get('short', '')}".lower()
            if q in hay:
                result.append((t["id"], t.get("short") or t.get("name", "?")))
        return result

    def teacher_name(self, teacher_id):
        self.refresh_if_stale()
        for t in self.tables["teachers"]:
            if t["id"] == teacher_id:
                return t.get("short") or t.get("name", "?")
        return None

    # --- расписание ---

    def build_schedule(self, kind, entity_id):
        """kind: 'class' | 'teacher'. Возвращает {day_index: [entry, ...]}."""
        self.refresh_if_stale()
        tables = self.tables
        periods = {p["period"]: p for p in tables["periods"]}
        subjects = {s["id"]: s for s in tables["subjects"]}
        teachers = {t["id"]: t for t in tables["teachers"]}
        classrooms = {c["id"]: c for c in tables["classrooms"]}
        class_names = {c["id"]: c["name"] for c in tables["classes"]}
        groups = {g["id"]: g for g in tables["groups"]}
        lessons = {l["id"]: l for l in tables["lessons"]}

        schedule = {}
        for card in tables["cards"]:
            lesson = lessons.get(card["lessonid"])
            if not lesson or not card.get("period"):
                continue
            if kind == "class" and entity_id not in lesson.get("classids", []):
                continue
            if kind == "teacher" and entity_id not in lesson.get("teacherids", []):
                continue

            subject = subjects.get(lesson["subjectid"], {})
            teacher_names = [
                teachers[tid].get("short") or teachers[tid].get("name", "")
                for tid in lesson.get("teacherids", []) if tid in teachers
            ]
            room_names = [
                classrooms[rid].get("short") or classrooms[rid].get("name", "")
                for rid in card.get("classroomids", []) if rid in classrooms
            ]
            lesson_classes = [class_names.get(cid, "?") for cid in lesson.get("classids", [])]
            group_names = []
            if kind == "class":
                for gid in lesson.get("groupids", []):
                    g = groups.get(gid)
                    if g and g.get("classid") == entity_id and not g.get("entireclass"):
                        group_names.append(g.get("name", ""))

            period_num = int(card["period"])
            duration = int(lesson.get("durationperiods", 1) or 1)
            start = periods.get(str(period_num), {}).get("starttime", "?")
            end = periods.get(str(period_num + duration - 1),
                              periods.get(str(period_num), {})).get("endtime", "?")

            entry = {
                "period": period_num,
                "time": f"{start}–{end}",
                "subject": subject.get("name") or subject.get("short", "?"),
                "teachers": ", ".join(teacher_names),
                "rooms": ", ".join(room_names),
                "groups": ", ".join(n for n in group_names if n),
                "classes": ", ".join(lesson_classes),
            }
            for day_index, bit in enumerate(card["days"]):
                if bit == "1":
                    schedule.setdefault(day_index, []).append(entry)

        for day in schedule.values():
            day.sort(key=lambda e: e["period"])
        return schedule


# ---------------------------------------------------------------- Форматирование

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

# тип пары зашит в название предмета: "Physics 1 (lec)".
# Цветные маркеры повторяют палитру мини-аппа: лаванда/мята/персик/розовый.
LESSON_TYPES = {
    "lec": ("🟣", "Lecture"), "mar": ("🟣", "Lecture"),  # mar = ma'ruza (лекция на узбекском)
    "prac": ("🟢", "Practice"), "sem": ("🩷", "Seminar"), "lab": ("🟠", "Lab"),
}


def split_subject(subject):
    """'Physics 1 (lec)' -> ('Physics 1', ('🟣', 'Lecture'))."""
    m = re.match(r"^(.*?)\s*\((\w+)\)\s*$", subject)
    if m and m.group(2).lower() in LESSON_TYPES:
        return m.group(1), LESSON_TYPES[m.group(2).lower()]
    return subject, None


def _minutes(hhmm):
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def format_entry(e, kind, now_minutes=None):
    """Одна пара: цветной маркер типа + время, предмет, детали. Текущая пара — ▶."""
    start, _, end = e["time"].partition("–")
    subject, lesson_type = split_subject(e["subject"])
    icon = lesson_type[0] if lesson_type else "🕐"
    if now_minutes is not None:
        s, en = _minutes(start), _minutes(end)
        if s is not None and en is not None and s <= now_minutes < en:
            icon = "▶️"
    head = f"{icon} <b>{e['time']}</b>"
    if lesson_type:
        head += f" · {lesson_type[1]}"
    lines = [head, f"<b>{subject}</b>"]
    details = []
    if kind == "class":
        if e["teachers"]:
            details.append(f"👤 {e['teachers']}")
        if e["groups"]:
            details.append(f"({e['groups']})")
    else:
        if e["classes"]:
            details.append(f"👥 {e['classes']}")
    if e["rooms"]:
        details.append(f"🚪 {e['rooms']}")
    if details:
        lines.append(" · ".join(details))
    return "\n".join(lines)


def format_gap(prev_entry, next_entry):
    """Окно между парами длиннее 20 минут: '1h 40m break'."""
    prev_end = _minutes(prev_entry["time"].partition("–")[2])
    next_start = _minutes(next_entry["time"].partition("–")[0])
    if prev_end is None or next_start is None:
        return None
    gap = next_start - prev_end
    if gap <= 20:
        return None
    h, m = divmod(gap, 60)
    dur = (f"{h}h " if h else "") + (f"{m}m" if m else "")
    return f"⏳ {dur.strip()} break"


def day_header(day_index, title_name, day_date=None):
    day_title = DAY_NAMES[day_index] if day_index < len(DAY_NAMES) else f"Day {day_index + 1}"
    if day_date:
        day_title += f", {day_date.day} {MONTH_NAMES[day_date.month - 1]}"
    return f"📅 <b>{day_title}</b> · {title_name}\n──────────────────"


def format_day(schedule, day_index, title_name, kind, day_date=None):
    lines = [day_header(day_index, title_name, day_date)]
    day = schedule.get(day_index)
    if not day:
        lines.append("\nNo classes")
        return "\n".join(lines)
    is_today = day_date == date.today() if day_date else False
    now = None
    if is_today:
        t = time.localtime()
        now = t.tm_hour * 60 + t.tm_min
    for i, e in enumerate(day):
        if i:
            gap = format_gap(day[i - 1], e)
            lines.append("")
            if gap:
                lines.append(gap)
                lines.append("")
        lines.append(format_entry(e, kind, now))
    return "\n".join(lines)


def format_week(schedule, title_name, kind):
    """Неделя: каждый день — сворачиваемая цитата (expandable blockquote)."""
    parts = [f"🗓 <b>Week timetable</b> · {title_name}"]
    for d in range(6):
        if d not in schedule:
            continue
        day = schedule[d]
        body = []
        for i, e in enumerate(day):
            if i:
                gap = format_gap(day[i - 1], e)
                body.append("")
                if gap:
                    body.append(gap)
                    body.append("")
            body.append(format_entry(e, kind))
        dots = "".join((split_subject(e["subject"])[1] or ("⚪",))[0] for e in day)
        header = f"<b>{DAY_NAMES[d]}</b> · {dots}"
        parts.append(f"{header}\n<blockquote expandable>" + "\n".join(body) + "</blockquote>")
    if len(parts) == 1:
        return f"<b>{title_name}</b>\n\nNo classes this week"
    return "\n\n".join(parts)


# ---------------------------------------------------------------- Telegram API

class TelegramBot:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    def request(self, method, payload):
        try:
            return api_post(f"{self.base}/{method}", payload, timeout=70)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            print(f"[telegram] {method} HTTP {e.code}: {body[:200]}")
            return None

    def get_updates(self):
        resp = self.request("getUpdates", {"offset": self.offset, "timeout": 50,
                                           "allowed_updates": ["message", "callback_query"]})
        if not resp or not resp.get("ok"):
            return []
        updates = resp["result"]
        if updates:
            self.offset = updates[-1]["update_id"] + 1
        return updates

    def send(self, chat_id, text, keyboard=None):
        # Telegram ограничивает сообщение 4096 символами — режем по пустым строкам
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
        for i, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
            if keyboard and i == len(chunks) - 1:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            self.request("sendMessage", payload)

    def edit(self, chat_id, message_id, text, keyboard=None):
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        payload = {"chat_id": chat_id, "message_id": message_id,
                   "text": text, "parse_mode": "HTML"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        self.request("editMessageText", payload)

    def typing(self, chat_id):
        self.request("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    def answer_callback(self, callback_id, text=None):
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self.request("answerCallbackQuery", payload)


# ---------------------------------------------------------------- Хранилище пользователей

def load_users():
    if USERS_PATH.exists():
        with open(USERS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_users(users):
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- Логика бота

class ScheduleBot:
    def __init__(self, token, subdomain, miniapp_url=""):
        self.tg = TelegramBot(token)
        self.edupage = EdupageData(subdomain)
        self.users = load_users()          # chat_id -> {"kind", "id", "name"}
        self.pending = {}                  # chat_id -> "teacher_search"
        self.miniapp_url = miniapp_url
        if miniapp_url:
            # кнопка меню (рядом с полем ввода) открывает мини-апп
            self.tg.request("setChatMenuButton", {"menu_button": {
                "type": "web_app", "text": "Timetable",
                "web_app": {"url": miniapp_url}}})

    # --- клавиатуры ---

    def kb_role(self):
        return [[{"text": "🎓 I'm a student", "callback_data": "role:student"},
                 {"text": "👨‍🏫 I'm a teacher", "callback_data": "role:teacher"}]]

    def kb_prefixes(self):
        prefixes = self.edupage.class_prefixes()
        rows, row = [], []
        for p in prefixes:
            row.append({"text": p, "callback_data": f"pfx:{p}"})
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return rows

    def kb_classes(self, prefix):
        rows, row = [], []
        for _, name in self.edupage.classes_by_prefix(prefix):
            row.append({"text": name, "callback_data": f"cls:{name}"})
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "⬅️ Back", "callback_data": "role:student"}])
        return rows

    def kb_teachers(self, matches):
        rows = [[{"text": name, "callback_data": f"tch:{tid}"}] for tid, name in matches[:10]]
        rows.append([{"text": "🔍 Search again", "callback_data": "role:teacher"}])
        return rows

    # --- обработка ---

    def handle_message(self, msg):
        chat_id = str(msg["chat"]["id"])
        text = (msg.get("text") or "").strip()

        if text.startswith("/start"):
            self.pending.pop(chat_id, None)
            self.tg.send(chat_id,
                         "👋 Hi! I'm the <b>Turin Polytechnic University</b> timetable bot.\n\n"
                         "Who are you?", self.kb_role())
            return
        if text.startswith("/help"):
            self.tg.send(chat_id, HELP_TEXT)
            return
        if text.startswith("/change"):
            self.pending.pop(chat_id, None)
            self.tg.send(chat_id, "Who are you?", self.kb_role())
            return
        if text.startswith(("/today", "/tomorrow", "/week")):
            self.send_schedule(chat_id, text.lstrip("/").split("@")[0].split()[0])
            return

        # ожидание ввода фамилии преподавателя
        if self.pending.get(chat_id) == "teacher_search":
            matches = self.edupage.search_teachers(text)
            if not matches:
                self.tg.send(chat_id, "😕 No matches. Try typing the last name differently "
                                      "(as it appears on edupage):")
            elif len(matches) == 1:
                self.set_profile(chat_id, "teacher", matches[0][0], matches[0][1])
            else:
                self.tg.send(chat_id, f"Found {len(matches)} matches, pick yourself:",
                             self.kb_teachers(matches))
            return

        # свободный текст: попробовать как название группы
        found = self.edupage.find_class(text)
        if found:
            self.set_profile(chat_id, "class", found[0], found[1])
        else:
            self.tg.send(chat_id, "Sorry, I didn't get that 🤔 Use /start to set up or /help for help.")

    def handle_callback(self, cb):
        chat_id = str(cb["message"]["chat"]["id"])
        message_id = cb["message"]["message_id"]
        data = cb.get("data", "")
        self.tg.answer_callback(cb["id"])

        if data == "noop":
            return
        if data.startswith("sched:"):
            self.edit_schedule(chat_id, message_id, data[6:])
            return
        if data == "role:student":
            self.pending.pop(chat_id, None)
            self.tg.send(chat_id, "Choose your programme:", self.kb_prefixes())
        elif data == "role:teacher":
            self.pending[chat_id] = "teacher_search"
            self.tg.send(chat_id, "Type your last name (as it appears on edupage):")
        elif data.startswith("pfx:"):
            prefix = data[4:]
            self.tg.send(chat_id, f"<b>{prefix}</b> groups:", self.kb_classes(prefix))
        elif data.startswith("cls:"):
            name = data[4:]
            found = self.edupage.find_class(name)
            if found:
                self.set_profile(chat_id, "class", found[0], found[1])
        elif data.startswith("tch:"):
            tid = data[4:]
            name = self.edupage.teacher_name(tid)
            if name:
                self.set_profile(chat_id, "teacher", tid, name)

    def set_profile(self, chat_id, kind, entity_id, name):
        self.pending.pop(chat_id, None)
        self.users[chat_id] = {"kind": kind, "id": entity_id, "name": name}
        save_users(self.users)
        kb = None
        if self.miniapp_url:
            kb = [[{"text": "🚀 Open Mini App", "web_app": {"url": self.miniapp_url}}]]
        self.tg.send(chat_id, f"Done — <b>{name}</b>\n\n" + HELP_TEXT, kb)

    # --- расписание с навигацией ---

    @staticmethod
    def week_dates():
        """Даты Пн–Сб текущей недели (в воскресенье — следующей)."""
        today = date.today()
        start = today - timedelta(days=today.weekday())
        if today.weekday() == 6:
            start += timedelta(days=7)
        return [start + timedelta(days=i) for i in range(6)]

    def kb_nav(self, day_index=None):
        """Стрелки по дням + быстрые кнопки. day_index=None — недельный вид."""
        rows = []
        if day_index is None:
            rows.append([{"text": DAY_SHORT[d], "callback_data": f"sched:day:{d}"}
                         for d in range(6)])
        else:
            prev_d, next_d = (day_index - 1) % 6, (day_index + 1) % 6
            rows.append([
                {"text": "◀️", "callback_data": f"sched:day:{prev_d}"},
                {"text": f"· {DAY_SHORT[day_index]} ·", "callback_data": "noop"},
                {"text": "▶️", "callback_data": f"sched:day:{next_d}"},
            ])
            rows.append([{"text": "📍 Today", "callback_data": "sched:today"},
                         {"text": "🗓 Week", "callback_data": "sched:week"}])
        return rows

    def render_schedule(self, profile, target):
        """target: 'today' | 'tomorrow' | 'week' | 'day:N'. -> (text, keyboard)"""
        schedule = self.edupage.build_schedule(profile["kind"], profile["id"])
        name, kind = profile["name"], profile["kind"]
        dates = self.week_dates()

        if target == "week":
            return format_week(schedule, name, kind), self.kb_nav(None)

        if target in ("today", "tomorrow"):
            d = date.today() + timedelta(days=1) if target == "tomorrow" else date.today()
            day_index = d.weekday()
            if day_index == 6:
                text = (f"<b>Sunday, {d.day} {MONTH_NAMES[d.month - 1]}</b> · {name}\n"
                        "──────────────────\nDay off")
                return text, self.kb_nav(0)
        else:
            day_index = int(target.split(":")[1])
            d = dates[day_index]

        return format_day(schedule, day_index, name, kind, d), self.kb_nav(day_index)

    def send_schedule(self, chat_id, mode):
        profile = self.users.get(chat_id)
        if not profile:
            self.tg.send(chat_id, "Set up first: /start")
            return
        self.tg.typing(chat_id)
        try:
            text, kb = self.render_schedule(profile, mode)
        except Exception as e:
            print(f"[edupage] ошибка загрузки: {e}")
            self.tg.send(chat_id, "⚠️ Failed to load the timetable from edupage, try again later.")
            return
        self.tg.send(chat_id, text, kb)

    def edit_schedule(self, chat_id, message_id, target):
        profile = self.users.get(chat_id)
        if not profile:
            self.tg.send(chat_id, "Set up first: /start")
            return
        try:
            text, kb = self.render_schedule(profile, target)
        except Exception as e:
            print(f"[edupage] ошибка загрузки: {e}")
            return
        self.tg.edit(chat_id, message_id, text, kb)

    # --- главный цикл ---

    def run(self):
        print("[bot] запущен, жду сообщений... (Ctrl+C для остановки)")
        while True:
            try:
                for update in self.tg.get_updates():
                    try:
                        if "message" in update:
                            self.handle_message(update["message"])
                        elif "callback_query" in update:
                            self.handle_callback(update["callback_query"])
                    except Exception as e:
                        print(f"[bot] ошибка обработки update: {e}")
            except KeyboardInterrupt:
                print("\n[bot] остановлен")
                return
            except Exception as e:
                print(f"[bot] сетевая ошибка: {e}, повтор через 5 сек")
                time.sleep(5)


def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    token = cfg.get("telegram_bot_token", "")
    if not token or "PASTE" in token:
        raise SystemExit("Заполни telegram_bot_token в config.json (токен от @BotFather)")
    ScheduleBot(token, cfg.get("edupage_subdomain", "ttpu"),
                cfg.get("miniapp_url", "")).run()


if __name__ == "__main__":
    main()

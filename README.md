# EduPage → Telegram расписание (TTPU)

Расписание с ttpu.edupage.org в Telegram. Без зависимостей — только стандартная библиотека Python.

Два режима:

| Файл | Что делает |
|---|---|
| `universal_bot.py` | **Универсальный бот** — любой студент или преподаватель настраивает себя сам через /start |
| `bot.py` | Одноразовая отправка расписания одной группы (для планировщика задач) |

## Универсальный бот

1. Создай бота у [@BotFather](https://t.me/BotFather), возьми токен.
2. Впиши токен в `config.json` → `telegram_bot_token`.
3. Запусти:

```
python universal_bot.py
```

Бот работает, пока запущен скрипт (long polling). Каждый пользователь пишет боту сам:

- `/start` → выбирает «🎓 Я студент» (направление → группа) или «👨‍🏫 Я преподаватель» (поиск по фамилии)
- `/today`, `/tomorrow`, `/week` — расписание
- `/change` — сменить группу/преподавателя

Настройки пользователей сохраняются в `users.json` и переживают перезапуск.
Расписание кэшируется 30 минут, свежая неделя подхватывается автоматически.

Студент видит: время, предмет, преподавателя, аудиторию, подгруппу.
Преподаватель видит: время, предмет, группы, аудиторию.

### Автозапуск при старте Windows

```
schtasks /Create /TN "EdupageBot" /TR "pythonw \"C:\Users\User\Desktop\qbit v1.0\edupage-telegram-bot\universal_bot.py\"" /SC ONSTART
```

## Одноразовая отправка (bot.py)

Для ежедневной авторассылки одной группе в фиксированный чат:
заполни `class_name`, `telegram_bot_token`, `telegram_chat_id` в `config.json` и:

```
python bot.py today       # сегодня
python bot.py tomorrow    # завтра
python bot.py week        # неделя
python bot.py --dry-run week   # в консоль, без отправки
```

Ежедневно в 7:00 через планировщик:

```
schtasks /Create /TN "EdupageTimetable" /TR "python \"C:\Users\User\Desktop\qbit v1.0\edupage-telegram-bot\bot.py\" today" /SC DAILY /ST 07:00
```

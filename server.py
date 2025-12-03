import os
import json
import logging
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Any

from flask import Flask, request, jsonify

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ==========================
# 🔐 НАЛАШТУВАННЯ
# ==========================

logging.basicConfig(level=logging.INFO)

# Токен бота і URL фронту краще задавати через змінні середовища на Render
BOT_TOKEN = os.getenv("BOT_TOKEN", "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg")
WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://brozhko.github.io/nahadayka-bot_v1/"  # 🔴 заміни на свій GitHub Pages, якщо інший
)

DATA_FILE = "deadlines.json"


# ==========================
# 🗂 РОБОТА З ФАЙЛОМ ДЕДЛАЙНІВ
# ==========================

def load_deadlines() -> Dict[str, List[Dict[str, Any]]]:
    """Читаємо всі дедлайни з файлу {user_id: [..]}."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        logging.exception("Помилка читання файлу дедлайнів")
        return {}


def save_deadlines(data: Dict[str, List[Dict[str, Any]]]) -> None:
    """Зберігаємо всі дедлайни в файл."""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("Помилка запису файлу дедлайнів")


# ==========================
# 🌐 FLASK API
# ==========================

app = Flask(__name__)


def get_user_id_from_request() -> str:
    """Дістаємо user_id з query або JSON, або debug_user."""
    uid = request.args.get("user_id")
    if not uid:
        try:
            payload = request.get_json(silent=True) or {}
            uid = payload.get("user_id")
        except Exception:
            uid = None
    if not uid:
        uid = "debug_user"
    return str(uid)


@app.get("/api/health")
def api_health():
    """Проста перевірка, що бекенд живий."""
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})


@app.get("/api/deadlines")
def api_get_deadlines():
    user_id = get_user_id_from_request()
    data = load_deadlines()
    items = data.get(user_id, [])
    items_sorted = sorted(items, key=lambda d: d.get("due", ""))
    return jsonify(items_sorted)


@app.post("/api/deadlines")
def api_add_deadline():
    payload = request.get_json(silent=True) or {}
    user_id = get_user_id_from_request()

    title = (payload.get("title") or "").strip()
    due = (payload.get("due") or "").strip()
    description = (payload.get("description") or "").strip()
    source = payload.get("source", "manual")

    if not title or not due:
        return jsonify({"error": "title і due обов'язкові"}), 400

    new_item = {
        "id": payload.get("id") or str(uuid.uuid4()),
        "title": title,
        "due": due,  # фронт шле "YYYY-MM-DD HH:MM"
        "description": description,
        "source": source,
        "created_at": datetime.utcnow().isoformat(),
    }

    data = load_deadlines()
    data.setdefault(user_id, []).append(new_item)
    save_deadlines(data)

    return jsonify(new_item), 201


@app.delete("/api/deadlines/<item_id>")
def api_delete_deadline(item_id):
    user_id = get_user_id_from_request()
    data = load_deadlines()
    items = data.get(user_id, [])
    before = len(items)

    items = [d for d in items if d.get("id") != item_id]
    after = len(items)

    data[user_id] = items
    save_deadlines(data)

    return jsonify({"deleted": before - after})


@app.post("/api/import/google-calendar")
def api_import_google_calendar():
    """Поки що фейковий імпорт з Google Calendar."""
    user_id = get_user_id_from_request()
    data = load_deadlines()

    fake_items = [
        {
            "id": "gcal-" + str(uuid.uuid4()),
            "title": "Пара з Вищої математики",
            "due": "2025-12-10 08:30",
            "description": "Подія з Google Calendar (поки що фейк)",
            "source": "google_calendar",
            "created_at": datetime.utcnow().isoformat(),
        }
    ]

    data.setdefault(user_id, []).extend(fake_items)
    save_deadlines(data)
    return jsonify(fake_items), 201


@app.post("/api/import/gmail")
def api_import_gmail():
    """Поки що фейковий імпорт з Gmail."""
    user_id = get_user_id_from_request()
    data = load_deadlines()

    fake_items = [
        {
            "id": "gmail-" + str(uuid.uuid4()),
            "title": "Лист: дедлайн по лабораторній",
            "due": "2025-12-05 23:59",
            "description": "Подія з Gmail (поки що фейк)",
            "source": "gmail",
            "created_at": datetime.utcnow().isoformat(),
        }
    ]

    data.setdefault(user_id, []).extend(fake_items)
    save_deadlines(data)
    return jsonify(fake_items), 201


# ==========================
# 🤖 TELEGRAM-БОТ
# ==========================

tg_app = ApplicationBuilder().token(BOT_TOKEN).build()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [
        [
            InlineKeyboardButton(
                text="Відкрити Нагадайку",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"Привіт, {user.first_name or 'друже'}! 👋\n\n"
        "Я бот-нагадувач. Натисни кнопку нижче, щоб відкрити мінізастосунок "
        "з дедлайнами."
    )
    await update.message.reply_text(text, reply_markup=reply_markup)


tg_app.add_handler(CommandHandler("start", cmd_start))


def run_bot():
    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
        logging.warning("BOT_TOKEN не заданий, бот не запущений")
        return
    logging.info("Запускаю Telegram-бота (polling)...")
    tg_app.run_polling(allowed_updates=Update.ALL_TYPES)


# ==========================
# 🚀 ТОЧКА ВХОДУ
# ==========================

if __name__ == "__main__":
    # окремий потік для бота
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Render підсовує порт через змінну PORT
    port = int(os.environ.get("PORT", 10000))
    logging.info(f"Запускаю Flask API на 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)


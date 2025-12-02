import json
import logging
from datetime import datetime
from typing import Dict, List, Any

import requests

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==========================
# 🔐 НАЛАШТУВАННЯ
# ==========================
TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"
WEBAPP_URL = "https://brozhko.github.io/nahadayka-bot_v1/?v=2"
BACKEND = "https://nahadayka-backend.onrender.com/api"

DATA_FILE = "deadlines.json"      # локальний файл для нагадувань
WARNING_DAYS = {3, 2, 1}          # за скільки днів нагадувати
CHECK_INTERVAL = 1                # перевірка дедлайнів раз на секунду

logging.basicConfig(level=logging.INFO)


# ==========================
# 📁 ФАЙЛИ
# ==========================
def load_deadlines() -> Dict[str, List[Dict[str, Any]]]:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_deadlines(data: Dict[str, List[Dict[str, Any]]]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ==========================
# ▶️ /start КОМАНДА
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("📱 Відкрити застосунок",
                                web_app=WebAppInfo(url=WEBAPP_URL))]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "✨ *Привіт! Я твій Нагадайка-бот!* ✨\n\n"
        "Натисни кнопку нижче, щоб відкрити застосунок.",
        parse_mode="Markdown",
        reply_markup=markup
    )


# ==========================
# 📨 Дані з WebApp
# ==========================
# ==========================
# 📨 Дані з WebApp
# ==========================
async def handle_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.message.web_app_data:
            return

        raw = update.message.web_app_data.data
        logging.info("RAW WEBAPP DATA: %r", raw)

        try:
            payload = json.loads(raw)
        except Exception as e:
            logging.exception("JSON ERROR while parsing web_app_data")
            await update.message.reply_text(
                "⚠️ Не можу прочитати дані від WebApp (невірний JSON)."
            )
            return

        user_id = str(update.effective_user.id)
        logging.info("WEBAPP PAYLOAD from %s: %s", user_id, payload)

        action = payload.get("action")

        # ----------------------------------
        # 🔄 ІМПОРТ КАЛЕНДАРЯ (SYNC З GOOGLE)
        # ----------------------------------
        if action == "sync":
            try:
                # 1) Попросити бекенд зробити синхронізацію з Google Calendar
                resp = requests.post(
                    f"{BACKEND}/google_sync/{user_id}",
                    timeout=20,
                )

                # Якщо бекенд каже "немає токена" → треба залогінитись у Google
                if resp.status_code in (401, 403):
                    login_resp = requests.get(
                        f"{BACKEND}/google_login/{user_id}",
                        timeout=10,
                    )
                    login_resp.raise_for_status()
                    data = login_resp.json()
                    auth_url = data["auth_url"]

                    keyboard = [[InlineKeyboardButton("Увійти через Google", url=auth_url)]]
                    await update.message.reply_text(
                        "🔑 Спочатку увійди до Google, щоб я зміг імпортувати календар:",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    return

                # Якщо якась інша помилка з бекендом
                if not resp.ok:
                    logging.error("google_sync failed: %s %s", resp.status_code, resp.text)
                    await update.message.reply_text(
                        "⚠️ Не вдалось імпортувати з Google (помилка бекенду)."
                    )
                    return

                data = resp.json()
                imported = data.get("imported", 0)

                await update.message.reply_text(
                    f"✅ Імпорт із Google завершено.\n"
                    f"Знайдено та оновлено дедлайнів: *{imported}*.",
                    parse_mode="Markdown",
                )
            except Exception:
                logging.exception("Google sync failed")
                await update.message.reply_text(
                    "⚠️ Не вдалось імпортувати з Google. "
                    "Спробуй пізніше або натисни \"Імпорт\" ще раз."
                )

            # Після 'sync' далі нічого не робимо
            return

        # ----------------------------------
        # ❌ ВИДАЛЕННЯ ДЕДЛАЙНУ (з WebApp)
        # ----------------------------------
        if action == "delete":
            title = payload["title"]

            data = load_deadlines()
            data[user_id] = [d for d in data.get(user_id, []) if d["title"] != title]
            save_deadlines(data)

            await update.message.reply_text(
                f"❌ Видалено: *{title}*",
                parse_mode="Markdown"
            )
            return

        # ----------------------------------
        # ➕ ДОДАВАННЯ ДЕДЛАЙНУ (з WebApp)
        # ----------------------------------
        title = payload["title"].strip()
        date = payload["date"].strip()

        data = load_deadlines()
        data.setdefault(user_id, [])

        data[user_id].append({
            "title": title,
            "date": date,
            "last_notified": None
        })
        save_deadlines(data)

        await update.message.reply_text(
            f"✅ Дедлайн збережено:\n• *{title}* — {date}",
            parse_mode="Markdown"
        )

    except Exception as e:
        logging.exception("WEBAPP ERROR")
        await update.message.reply_text(f"⚠️ Помилка: {e}")


# ==========================
# ⏰ НАГАДУВАННЯ
# ==========================
async def check_deadlines_job(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    today = datetime.now().date()
    data = load_deadlines()
    changed = False

    for uid, items in data.items():
        for d in items:
            try:
                date_obj = datetime.strptime(
                    d["date"].split()[0], "%Y-%m-%d"
                ).date()
            except Exception:
                continue

            diff = (date_obj - today).days

            if diff in WARNING_DAYS and d.get("last_notified") != diff:
                await bot.send_message(
                    chat_id=int(uid),
                    text=f"⏰ До *{d['title']}* залишилось {diff} дн.",
                    parse_mode="Markdown"
                )
                d["last_notified"] = diff
                changed = True

    if changed:
        save_deadlines(data)


# ==========================
# 🚀 ЗАПУСК БОТА
# ==========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    # БІЛЬШЕ НЕ ДОДАЄМО /sync — імпорт робить бекенд
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp))

    # Періодична перевірка дедлайнів
    app.job_queue.run_repeating(check_deadlines_job, interval=CHECK_INTERVAL, first=5)

    print("🔥 BOT STARTED")
    app.run_polling()


if __name__ == "__main__":
    main()

from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import requests
from datetime import datetime
from typing import Dict, List, Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

DATA_FILE = "deadlines.json"
CLIENT_SECRETS_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"
REDIRECT_URI = "https://nahadayka-backend.onrender.com/api/google_callback"


# ---------------------------
# ФАЙЛОВЕ СХОВИЩЕ ДЕДЛАЙНІВ
# ---------------------------

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


# ---------------------------
# API ДЛЯ ДЕДЛАЙНІВ
# ---------------------------

@app.get("/api/deadlines/<user_id>")
def get_deadlines(user_id: str):
    data = load_deadlines()
    return jsonify(data.get(user_id, []))


@app.post("/api/deadlines/<user_id>")
def add_deadline(user_id: str):
    body = request.get_json(force=True)
    title = (body.get("title") or "").strip()
    date_str = (body.get("date") or "").strip()

    if not title or not date_str:
        return jsonify({"error": "title and date are required"}), 400

    data = load_deadlines()
    user_items = data.setdefault(user_id, [])

    new_item = {
        "title": title,
        "date": date_str,
        "last_notified": None,
    }
    user_items.append(new_item)
    save_deadlines(data)

    return jsonify(new_item), 201


@app.delete("/api/deadlines/<user_id>")
def delete_deadline(user_id: str):
    body = request.get_json(force=True)
    title = (body.get("title") or "").strip()

    if not title:
        return jsonify({"error": "title is required"}), 400

    data = load_deadlines()
    if user_id not in data:
        return jsonify({"deleted": 0})

    before = len(data[user_id])
    data[user_id] = [d for d in data[user_id] if d.get("title") != title]
    after = len(data[user_id])

    save_deadlines(data)
    return jsonify({"deleted": before - after})


# ---------------------------
# GOOGLE LOGIN
# ---------------------------

@app.get("/api/google_login/<user_id>")
def google_login(user_id: str):
    """
    Генерує URL для авторизації в Google.
    state = user_id, щоб у callback знати, кому імпортувати.
    """
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=user_id,
    )

    return jsonify({"auth_url": auth_url})


# ---------------------------
# CALLBACK ПІСЛЯ GOOGLE LOGIN
# ---------------------------

@app.get("/api/google_callback")
def google_callback():
    code = request.args.get("code")
    user_id = request.args.get("state")

    if not code or not user_id:
        return "Authorization failed: missing code or state", 400

    try:
        # Створюємо flow ще раз
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
        )
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Зберігаємо токен користувача (на майбутнє для re-sync)
        token_path = f"token_user_{user_id}.json"
        with open(token_path, "w") as f:
            f.write(creds.to_json())

        imported = import_google_events(user_id, creds)

        # Надсилаємо повідомлення в Telegram
        try:
            requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                params={
                    "chat_id": user_id,
                    "text": f"Імпортовано подій з Google Calendar: {imported}",
                },
                timeout=10,
            )
        except Exception as e:
            print("TG send error:", e)

        return "Готово! Події імпортовано, можеш закрити цю вкладку і повернутися в Telegram."

    except Exception as e:
        print("google_callback error:", e)
        return "Сталася помилка під час імпорту з Google Calendar. Спробуй ще раз.", 500


# ---------------------------
# РУЧНИЙ SYNC З GOOGLE (коли токен уже є)
# Викликається ботом по /api/google_sync/<user_id>
# ---------------------------

@app.post("/api/google_sync/<user_id>")
def google_sync(user_id: str):
    """
    Запускає імпорт подій з Google Calendar у deadlines.json
    для заданого user_id, якщо вже є збережений токен.
    """
    token_path = f"token_user_{user_id}.json"
    if not os.path.exists(token_path):
        # Немає токена → користувач ще не логінився в Google
        return jsonify({"error": "no_token"}), 401

    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        imported = import_google_events(user_id, creds)
        return jsonify({"imported": imported}), 200
    except Exception as e:
        print("google_sync error:", e)
        return jsonify({"error": "sync_failed"}), 500


# ---------------------------
# ІМПОРТ GOOGLE CALENDAR → deadlines.json
# ---------------------------

def import_google_events(user_id: str, creds: Credentials | None = None) -> int:
    """
    Читає події з Google Calendar і додає їх у deadlines.json для цього user_id.
    НІЯКИХ HTTP-запитів на свій же бекенд – тільки локальний файл.
    """
    if creds is None:
        token_path = f"token_user_{user_id}.json"
        if not os.path.exists(token_path):
            return 0
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    service = build("calendar", "v3", credentials=creds)

    now = datetime.utcnow().isoformat() + "Z"
    events = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=50,
        singleEvents=True,
        orderBy="startTime",
    ).execute().get("items", [])

    data = load_deadlines()
    user_items = data.setdefault(user_id, [])

    def is_duplicate(title: str, date_str: str) -> bool:
        return any(d["title"] == title and d["date"] == date_str for d in user_items)

    count = 0

    for ev in events:
        title = ev.get("summary")
        if not title:
            continue

        if "dateTime" in ev["start"]:
            date_raw = ev["start"]["dateTime"][:16].replace("T", " ")
        else:
            date_raw = ev["start"]["date"]

        if is_duplicate(title, date_raw):
            continue

        user_items.append({
            "title": title,
            "date": date_raw,
            "last_notified": None,
        })
        count += 1

    save_deadlines(data)
    return count


# ---------------------------
# HEALTHCHECK
# ---------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------------------------
# RUN (локально/на Render)
# ---------------------------
if __name__ == "__main__":
    import threading
    from bot import main as run_bot   # імпортуємо твою функцію main з bot.py

    # запускаємо бота у фоновому потоці
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # запускаємо Flask API
    app.run(host="0.0.0.0", port=8000, debug=False)

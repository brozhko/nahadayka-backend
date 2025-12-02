# server.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import requests
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

DATA_FILE = "deadlines.json"
CLIENT_SECRETS_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# якщо бекенд один – це ж сам сервіс
BACKEND_API = "https://nahadayka-backend.onrender.com/api"
BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"


# ---------------------------
# ФУНКЦІЇ ФАЙЛОВОГО ЗАПИСУ
# ---------------------------

def load_deadlines():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_deadlines(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------
# API ДЕДЛАЙНІВ (для фронта та бота)
# ---------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


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

    # проста валідація формату дати
    try:
        if len(date_str) == 10:
            datetime.strptime(date_str, "%Y-%m-%d")
        else:
            datetime.strptime(date_str, "%Y-%m-%d %H:%M")
    except ValueError:
        return jsonify({"error": "Bad date format. Use YYYY-MM-DD or YYYY-MM-DD HH:MM"}), 400

    data = load_deadlines()
    data.setdefault(user_id, [])

    new_item = {
        "title": title,
        "date": date_str,
        "last_notified": None,
    }
    data[user_id].append(new_item)
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
# GOOGLE LOGIN (КЛЮЧОВЕ)
# ---------------------------

@app.get("/api/google_login/<user_id>")
def google_login(user_id):
    """
    Генерує посилання для входу в Google.
    state=user_id, щоб у callback знати, кому зберігати токен.
    """
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri="https://nahadayka-backend.onrender.com/api/google_callback",
    )

    auth_url, state = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
        state=user_id,
    )

    # НІЧОГО не зберігаємо, просто віддаємо URL
    return jsonify({"auth_url": auth_url})


# ---------------------------
# CALLBACK ПІСЛЯ GOOGLE LOGIN
# ---------------------------

@app.get("/api/google_callback")
def google_callback():
    code = request.args.get("code")
    user_id = request.args.get("state", "unknown_user")

    if not code:
        return "No code provided", 400

    # Створюємо новий Flow і міняємо code на токен
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri="https://nahadayka-backend.onrender.com/api/google_callback",
    )

    flow.fetch_token(code=code)
    creds = flow.credentials

    # Зберігаємо токен користувача
    token_path = f"token_user_{user_id}.json"
    with open(token_path, "w") as f:
        f.write(creds.to_json())

    # Імпортуємо події з календаря
    imported = import_google_events(user_id)

    # Надсилаємо повідомлення користувачу в Telegram
    requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        params={"chat_id": user_id, "text": f"Імпортовано подій: {imported}"},
    )

    return "Google авторизація успішна! Можете повернутися до Telegram 👌"


# ---------------------------
# ІМПОРТ GOOGLE CALENDAR
# ---------------------------

def import_google_events(user_id: str) -> int:
    token_path = f"token_user_{user_id}.json"
    if not os.path.exists(token_path):
        return 0

    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    service = build("calendar", "v3", credentials=creds)

    now = datetime.utcnow().isoformat() + "Z"
    events = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now,
            maxResults=50,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
        .get("items", [])
    )

    count = 0

    for ev in events:
        title = ev.get("summary")
        if not title:
            continue

        if "dateTime" in ev["start"]:
            date_raw = ev["start"]["dateTime"][:16].replace("T", " ")
        else:
            date_raw = ev["start"]["date"]

        requests.post(
            f"{BACKEND_API}/deadlines/{user_id}",
            json={"title": title, "date": date_raw},
            timeout=10,
        )
        count += 1

    return count


# ---------------------------
# RUN
# ---------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

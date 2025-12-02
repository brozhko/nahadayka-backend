# server.py
from flask import Flask, request, jsonify, redirect
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

BACKEND_API = "https://nahadayka-backend.onrender.com/api"
BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"


# ---------------------------
# ФУНКЦІЇ ФАЙЛОВОГО ЗАПИСУ
# ---------------------------

def load_deadlines():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_deadlines(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)



# ---------------------------
# GOOGLE LOGIN (КЛЮЧОВЕ)
# ---------------------------

@app.get("/api/google_login/<user_id>")
def google_login(user_id):
    """Генерує посилання для входу в Google з state=user_id."""

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri="https://nahadayka-backend.onrender.com/api/google_callback"
    )

    auth_url, state = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
        state=user_id  # ← ПЕРЕДАЄМО КОРИСТУВАЧА
    )

    # Зберігаємо Flow
    with open(f"flow_{user_id}.json", "w") as f:
        f.write(flow.to_json())

    return jsonify({"auth_url": auth_url})



# ---------------------------
# CALLBACK ПІСЛЯ GOOGLE LOGIN
# ---------------------------

@app.get("/api/google_callback")
def google_callback():

    code = request.args.get("code")
    user_id = request.args.get("state")  # ← ОТРИМУЄМО user_id

    if not code or not user_id:
        return "Authorization failed", 400

    # Відновлюємо Flow
    with open(f"flow_{user_id}.json") as f:
        flow = Flow.from_json(f.read())

    flow.redirect_uri = "https://nahadayka-backend.onrender.com/api/google_callback"

    # Отримуємо токени
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Зберігаємо токен користувача
    with open(f"token_user_{user_id}.json", "w") as f:
        f.write(creds.to_json())

    # Імпортуємо події
    imported = import_google_events(user_id)

    # Відправляємо повідомлення в Telegram
    requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        params={"chat_id": user_id, "text": f"Імпортовано подій: {imported}"}
    )

    return "Готово! Google Calendar імпортовано. Можеш повертатися в Telegram."



# ---------------------------
# ІМПОРТ GOOGLE CALENDAR
# ---------------------------

def import_google_events(user_id):

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
        orderBy="startTime"
    ).execute().get("items", [])

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
            json={"title": title, "date": date_raw}
        )

        count += 1

    return count



# ---------------------------
# SERVICE
# ---------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}



# ---------------------------
# RUN
# ---------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

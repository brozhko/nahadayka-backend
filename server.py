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

# ---------------------------
# КОНСТАНТИ
# ---------------------------

DATA_FILE = "deadlines.json"
CLIENT_SECRETS_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"
REDIRECT_URI = "https://nahadayka-backend.onrender.com/api/google_callback"


# ---------------------------
# ФАЙЛОВЕ СХОВИЩЕ
# ---------------------------

def load_deadlines() -> Dict[str, List[Dict[str, Any]]]:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_deadlines(data: Dict[str, List[Dict[str, Any]]]):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------
# API / DEADLINES
# ---------------------------

@app.get("/api/deadlines/<user_id>")
def get_deadlines(user_id: str):
    data = load_deadlines()
    return jsonify(data.get(user_id, []))


@app.post("/api/deadlines/<user_id>")
def add_deadline(user_id: str):
    body = request.get_json(force=True)
    title = (body.get("title") or "").strip()
    date = (body.get("date") or "").strip()

    if not title or not date:
        return jsonify({"error": "missing fields"}), 400

    data = load_deadlines()
    user_list = data.setdefault(user_id, [])

    new_item = {"title": title, "date": date, "last_notified": None}
    user_list.append(new_item)

    save_deadlines(data)
    return jsonify(new_item), 201


@app.delete("/api/deadlines/<user_id>")
def delete_deadline(user_id: str):
    body = request.get_json(force=True)
    title = (body.get("title") or "").strip()

    data = load_deadlines()
    if user_id not in data:
        return jsonify({"deleted": 0})

    before = len(data[user_id])
    data[user_id] = [d for d in data[user_id] if d["title"] != title]
    after = len(data[user_id])

    save_deadlines(data)
    return jsonify({"deleted": before - after})


# ---------------------------
# GOOGLE LOGIN
# ---------------------------

@app.get("/api/google_login/<user_id>")
def google_login(user_id: str):
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=user_id
    )

    return jsonify({"auth_url": auth_url})


# ---------------------------
# CALLBACK від Google
# ---------------------------

@app.get("/api/google_callback")
def google_callback():
    code = request.args.get("code")
    user_id = request.args.get("state")

    if not code or not user_id:
        return "Missing parameters!", 400

    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
        )

        flow.fetch_token(code=code)
        creds = flow.credentials

        # зберігаємо токен
        token_path = f"token_user_{user_id}.json"
        with open(token_path, "w") as f:
            f.write(creds.to_json())

        imported = import_google_events(user_id, creds)

        # повідомляємо користувача в Telegram
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            params={
                "chat_id": user_id,
                "text": f"Імпортовано подій з Google Calendar: {imported}"
            }
        )

        return "Готово! Можеш закрити цю вкладку і повернутись у Telegram."

    except Exception as e:
        print("callback error:", e)
        return "Помилка Google Callback", 500


# ---------------------------
# РУЧНИЙ SYNC (бот викликає)
# ---------------------------

@app.post("/api/google_sync/<user_id>")
def google_sync(user_id: str):
    token_path = f"token_user_{user_id}.json"
    if not os.path.exists(token_path):
        return jsonify({"error": "no_token"}), 401

    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        imported = import_google_events(user_id, creds)
        return jsonify({"imported": imported})
    except Exception as e:
        print("sync error:", e)
        return jsonify({"error": "sync_failed"}), 500


# ---------------------------
# ІМПОРТ ПОДІЙ GOOGLE
# ---------------------------

def import_google_events(user_id: str, creds):
    service = build("calendar", "v3", credentials=creds)

    now = datetime.utcnow().isoformat() + "Z"
    events = service.events().list(
        calendarId="primary",
        timeMin=now,
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute().get("items", [])

    data = load_deadlines()
    user_list = data.setdefault(user_id, [])

    def is_dup(title, date):
        return any(d["title"] == title and d["date"] == date for d in user_list)

    count = 0
    for ev in events:
        title = ev.get("summary")
        if not title:
            continue

        if "dateTime" in ev["start"]:
            date_str = ev["start"]["dateTime"][:16].replace("T", " ")
        else:
            date_str = ev["start"]["date"]

        if is_dup(title, date_str):
            continue

        user_list.append({"title": title, "date": date_str, "last_notified": None})
        count += 1

    save_deadlines(data)
    return count


# ---------------------------
# HEALTH
# ---------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------------------------
# RUN (Render запускає тільки Flask!)
# ---------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)



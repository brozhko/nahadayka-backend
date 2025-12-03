from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
from datetime import datetime
import requests

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


# ===================================================
# APP INIT
# ===================================================
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})


# ===================================================
# CONFIG
# ===================================================
BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"

BACKEND_URL = "https://nahadayka-backend.onrender.com"
WEBAPP_URL = "https://brozhko.github.io/nahadayka-bot_v1/"

DATA_FILE = "deadlines.json"
CLIENT_SECRETS_FILE = "credentials.json"

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
REDIRECT_URI = f"{BACKEND_URL}/api/google_callback"


# ===================================================
# STORAGE
# ===================================================
def load_deadlines():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_deadlines(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===================================================
# DEADLINES API (ADD + UPDATE)
# ===================================================
@app.post("/api/deadlines/<user_id>")
def add_or_update_deadline(user_id):
    body = request.get_json()
    data = load_deadlines()
    data.setdefault(user_id, [])

    # ===================================================
    # 🔄 ОНОВЛЕННЯ last_notified (бот)
    # ===================================================
    if "last_notified_update" in body and "title" in body:
        title = body["title"]
        new_val = body["last_notified_update"]

        for d in data[user_id]:
            if d["title"] == title:
                d["last_notified"] = new_val
                save_deadlines(data)
                return jsonify({"updated": True})

        return jsonify({"error": "not found"}), 404

    # ===================================================
    # ➕ ДОДАВАННЯ НОВОГО ДЕДЛАЙНУ
    # ===================================================
    title = body.get("title", "").strip()
    date = body.get("date", "").strip()

    if not title or not date:
        return jsonify({"error": "title and date required"}), 400

    item = {
        "title": title,
        "date": date,
        "last_notified": None
    }

    data[user_id].append(item)
    save_deadlines(data)
    return jsonify(item), 201


@app.get("/api/deadlines/<user_id>")
def get_deadlines(user_id):
    data = load_deadlines()
    return jsonify(data.get(user_id, []))


@app.delete("/api/deadlines/<user_id>")
def delete_deadline(user_id):
    body = request.get_json()
    title = body.get("title")

    data = load_deadlines()
    if user_id in data:
        data[user_id] = [d for d in data[user_id] if d["title"] != title]
        save_deadlines(data)

    return jsonify({"status": "ok"})


# ===================================================
# GOOGLE LOGIN
# ===================================================
@app.get("/api/google_login/<user_id>")
def google_login(user_id):
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


# ===================================================
# GOOGLE CALLBACK
# ===================================================
@app.get("/api/google_callback")
def google_callback():
    code = request.args.get("code")
    user_id = request.args.get("state")

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    flow.fetch_token(code=code)
    creds = flow.credentials

    # save token
    with open(f"token_{user_id}.json", "w") as f:
        f.write(creds.to_json())

    imported = import_google_events(user_id, creds)

    # notify user
    requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        params={
            "chat_id": user_id,
            "text": f"Імпортовано подій: {imported}\nМожеш повернутись у застосунок."
        }
    )

    return "Готово! Можеш закрити вкладку."


# ===================================================
# GOOGLE SYNC
# ===================================================
@app.post("/api/google_sync/<user_id>")
def google_sync(user_id):
    token_path = f"token_{user_id}.json"

    if not os.path.exists(token_path):
        return jsonify({"error": "no_token"}), 401

    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    imported = import_google_events(user_id, creds)

    return jsonify({"imported": imported})


# ===================================================
# IMPORT GOOGLE EVENTS
# ===================================================
def import_google_events(user_id, creds):
    service = build("calendar", "v3", credentials=creds)

    now = datetime.utcnow().isoformat() + "Z"

    result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=50,
        orderBy="startTime",
        singleEvents=True,
    ).execute()

    events = result.get("items", [])

    data = load_deadlines()
    user_items = data.setdefault(user_id, [])

    def exists(title, date):
        return any(d["title"] == title and d["date"] == date for d in user_items)

    imported = 0

    for ev in events:
        title = ev.get("summary")
        if not title:
            continue

        # Normalize date
        if "dateTime" in ev["start"]:
            date_value = ev["start"]["dateTime"]
            date_value = date_value[:16].replace("T", " ")  # YYYY-MM-DD HH:MM
        else:
            date_value = ev["start"]["date"]  # YYYY-MM-DD

        if exists(title, date_value):
            continue

        user_items.append({
            "title": title,
            "date": date_value,
            "last_notified": None
        })

        imported += 1

    save_deadlines(data)
    return imported


# ===================================================
# ROOT
# ===================================================
@app.get("/")
def home():
    return "Backend works!", 200


# ===================================================
# RUN LOCAL
# ===================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

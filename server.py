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
BOT_TOKEN = os.environ.get("8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg", "")  
BACKEND_URL = "https://nahadayka-backend.onrender.com"
WEBAPP_URL = "https://brozhko.github.io/nahadayka-bot_v1/"

DATA_FILE = "deadlines.json"
CLIENT_SECRETS_FILE = "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]
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
# API: RETURN ALL USERS (FOR CRON)
# ===================================================
@app.get("/api/all")
def all_users():
    return jsonify(load_deadlines())


# ===================================================
# DEADLINES API
# ===================================================
@app.post("/api/deadlines/<user_id>")
def add_or_update_deadline(user_id):
    body = request.get_json()
    data = load_deadlines()
    data.setdefault(user_id, [])

    # update last_notified
    if body and "last_notified_update" in body and "title" in body:
        title = body["title"]
        new_val = body["last_notified_update"]

        for d in data[user_id]:
            if d["title"] == title:
                d["last_notified"] = new_val
                save_deadlines(data)
                return jsonify({"updated": True})

        return jsonify({"error": "not found"}), 404

    title = (body or {}).get("title", "").strip()
    date = (body or {}).get("date", "").strip()

    if not title or not date:
        return jsonify({"error": "title and date required"}), 400

    data[user_id].append({
        "title": title,
        "date": date,
        "last_notified": None
    })

    save_deadlines(data)
    return jsonify({"status": "added"}), 201


@app.get("/api/deadlines/<user_id>")
def get_deadlines(user_id):
    data = load_deadlines()
    return jsonify(data.get(user_id, []))


@app.delete("/api/deadlines/<user_id>")
def delete_deadline(user_id):
    body = request.get_json() or {}
    title = body.get("title")

    data = load_deadlines()
    if user_id in data and title:
        data[user_id] = [d for d in data[user_id] if d["title"] != title]
        save_deadlines(data)

    return jsonify({"status": "ok"})


# ===================================================
# 📷 SCAN IMAGE (stub)
# ===================================================
@app.post("/api/scan_image")
def scan_image():
    """
    Приймає фото (multipart/form-data):
      - image: файл
      - uid: (optional) user_id
    Повертає:
      { "items": [ { "title": "...", "date": "YYYY-MM-DD HH:MM" }, ... ] }
    """
    if "image" not in request.files:
        return jsonify({"items": [], "error": "no_image"}), 400

    file = request.files["image"]
    img_bytes = file.read()

    if not img_bytes:
        return jsonify({"items": [], "error": "empty_file"}), 400

    uid = request.form.get("uid") or request.args.get("uid") or "unknown"
    print(f"[scan_image] uid={uid}, filename={file.filename}, bytes={len(img_bytes)}")

    today = datetime.now().strftime("%Y-%m-%d")
    items = [
        {"title": "Дедлайн з фото (тест 1)", "date": f"{today} 23:59"},
        {"title": "Дедлайн з фото (тест 2)", "date": f"{today} 18:00"},
    ]

    return jsonify({"items": items}), 200


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

    with open(f"token_{user_id}.json", "w") as f:
        f.write(creds.to_json())

    imported_calendar = import_google_calendar(user_id, creds)
    imported_gmail = import_gmail(user_id, creds)

    msg = (
        f"📅 Календар: імпортовано {imported_calendar} подій\n"
        f"📧 Gmail: знайдено {imported_gmail} листів із завданнями"
    )

    # ✅ якщо токена нема — не валимо сервер
    if BOT_TOKEN:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            params={"chat_id": user_id, "text": msg}
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

    return jsonify({
        "calendar": import_google_calendar(user_id, creds),
        "gmail": import_gmail(user_id, creds)
    })


# ===================================================
# IMPORT EVENTS FROM CALENDAR
# ===================================================
def import_google_calendar(user_id, creds):
    try:
        service = build("calendar", "v3", credentials=creds)
    except:
        return 0

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

    imported = 0

    for ev in events:
        title = ev.get("summary")
        if not title:
            continue

        if "dateTime" in ev.get("start", {}):
            date_value = ev["start"]["dateTime"][:16].replace("T", " ")
        else:
            date_value = ev.get("start", {}).get("date")

        if not date_value:
            continue

        if any(d["title"] == title and d["date"] == date_value for d in user_items):
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
# 📧 IMPORT FROM GMAIL (LPNU + KEYWORDS)
# ===================================================
KEYWORDS = [k.lower() for k in ["лаба", "лаб", "завдання", "звіт", "робота", "кр", "практична"]]
LPNU_DOMAIN = "@lpnu.ua"


def import_gmail(user_id, creds):
    try:
        service = build("gmail", "v1", credentials=creds)
    except:
        return 0

    query = f"from:{LPNU_DOMAIN}"

    result = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=50
    ).execute()

    messages = result.get("messages", [])

    data = load_deadlines()
    user_items = data.setdefault(user_id, [])

    added = 0

    for msg in messages:
        full = service.users().messages().get(userId="me", id=msg["id"]).execute()
        headers = full.get("payload", {}).get("headers", [])

        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "Без теми")
        date_header = next((h["value"] for h in headers if h["name"] == "Date"), None)
        if not date_header:
            continue

        try:
            date_obj = datetime.strptime(date_header[:25], "%a, %d %b %Y %H:%M:%S")
            date_str = date_obj.strftime("%Y-%m-%d")
        except:
            continue

        if not any(k in subject.lower() for k in KEYWORDS):
            continue

        if not any(d["title"] == subject for d in user_items):
            user_items.append({
                "title": subject,
                "date": date_str,
                "last_notified": None
            })
            added += 1

    save_deadlines(data)
    return added


# ===================================================
# ROOT
# ===================================================
@app.get("/")
def home():
    return "Backend works!", 200


# ===================================================
# RUN
# ===================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

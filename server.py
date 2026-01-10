from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import base64
from datetime import datetime
import requests
from zoneinfo import ZoneInfo

# OpenAI (ШІ)
from openai import OpenAI

# Google API
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


# ===================================================
# APP INIT
# ===================================================
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

UA_TZ = ZoneInfo("Europe/Kyiv")


# ===================================================
# CONFIG (як ти просив — хардкод)
# ===================================================
BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"

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
# OPENAI
# ===================================================
ai = OpenAI()  # OPENAI_API_KEY має бути в Render env


# ===================================================
# STORAGE
# ===================================================
def load_deadlines():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
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
    body = request.get_json() or {}
    data = load_deadlines()
    data.setdefault(user_id, [])

    # update last_notified
    if "last_notified_update" in body and "title" in body:
        title = str(body.get("title", "")).strip()
        new_val = body.get("last_notified_update")

        for d in data[user_id]:
            if d.get("title") == title:
                d["last_notified"] = new_val
                save_deadlines(data)
                return jsonify({"updated": True})

        return jsonify({"error": "not found"}), 404

    title = str(body.get("title", "")).strip()
    date = str(body.get("date", "")).strip()

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
    title = str(body.get("title", "")).strip()

    data = load_deadlines()
    if user_id in data and title:
        data[user_id] = [d for d in data[user_id] if d.get("title") != title]
        save_deadlines(data)

    return jsonify({"status": "ok"})


# ===================================================
# 🤖 AI SCAN IMAGE (NO OCR) -> JSON deadlines
# ===================================================
@app.post("/api/scan_deadlines_ai")
def scan_deadlines_ai():
    if "image" not in request.files:
        return jsonify({"error": "no_image"}), 400

    file = request.files["image"]
    img_bytes = file.read()
    if not img_bytes:
        return jsonify({"error": "empty_file"}), 400

    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    today = datetime.now(UA_TZ).strftime("%Y-%m-%d")

    prompt = f"""
Ти аналізуєш фото (зошит, чат, дошка, розклад).
Знайди ВСІ дедлайни.

Сьогодні: {today}
Часова зона: Europe/Kyiv

Поверни ТІЛЬКИ JSON:
{{
  "deadlines": [
    {{
      "title": "що треба зробити/здати",
      "due_date": "YYYY-MM-DD або null",
      "due_time": "HH:MM або null",
      "confidence": 0.0
    }}
  ]
}}

Правила:
- Якщо часу нема — due_time = "23:59"
- Якщо дата відносна (завтра/понеділок) — перетвори в конкретну дату
- confidence 0..1
"""

    try:
        resp = ai.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{img_b64}"},
                    ],
                }
            ],
            text={"format": {"type": "json_object"}},
        )
        return jsonify(resp.output_parsed), 200

    except Exception as e:
        return jsonify({"error": "ai_failed", "detail": str(e)}), 500


# ===================================================
# ✅ ADD AI SCANNED -> save to deadlines.json (same format as other deadlines)
# ===================================================
@app.post("/api/add_ai_scanned/<user_id>")
def add_ai_scanned(user_id):
    body = request.get_json() or {}
    deadlines = body.get("deadlines") or []

    if not isinstance(deadlines, list) or not deadlines:
        return jsonify({"error": "deadlines required", "added": 0}), 400

    data = load_deadlines()
    data.setdefault(user_id, [])

    added = 0
    for d in deadlines:
        title = str((d or {}).get("title", "")).strip()
        due_date = (d or {}).get("due_date")
        due_time = (d or {}).get("due_time") or "23:59"

        if not title or not due_date:
            continue

        date_value = f"{due_date} {due_time}"

        exists = any(x.get("title") == title and x.get("date") == date_value for x in data[user_id])
        if exists:
            continue

        data[user_id].append({
            "title": title,
            "date": date_value,
            "last_notified": None
        })
        added += 1

    save_deadlines(data)
    return jsonify({"status": "ok", "added": added}), 200


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
# GOOGLE CALLBACK (saves token + imports calendar & gmail)
# ===================================================
@app.get("/api/google_callback")
def google_callback():
    code = request.args.get("code")
    user_id = request.args.get("state")

    if not code or not user_id:
        return "Missing code/state", 400

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

    if BOT_TOKEN:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            params={"chat_id": user_id, "text": msg},
            timeout=15
        )

    return "Готово! Можеш закрити вкладку."


# ===================================================
# GOOGLE SYNC (manual)
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
    except Exception:
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

        start = ev.get("start", {})
        if "dateTime" in start:
            date_value = start["dateTime"][:16].replace("T", " ")
        else:
            date_value = start.get("date")
            if date_value:
                date_value = f"{date_value} 09:00"

        if not date_value:
            continue

        if any(d.get("title") == title and d.get("date") == date_value for d in user_items):
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
    except Exception:
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

        subject = next((h["value"] for h in headers if h.get("name") == "Subject"), "Без теми")
        date_header = next((h["value"] for h in headers if h.get("name") == "Date"), None)
        if not date_header:
            continue

        # Простіше, як у твоєму коді: ставимо дедлайн на кінець дня
        try:
            date_obj = datetime.strptime(date_header[:25], "%a, %d %b %Y %H:%M:%S")
            date_str = date_obj.strftime("%Y-%m-%d 23:59")
        except Exception:
            continue

        if not any(k in subject.lower() for k in KEYWORDS):
            continue

        if not any(d.get("title") == subject and d.get("date") == date_str for d in user_items):
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
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

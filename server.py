from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import re
import shutil
from datetime import datetime, timedelta
from io import BytesIO
import requests

# OCR
from PIL import Image, ImageOps
import pytesseract
import dateparser
from dateutil import tz

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


# ===================================================
# APP INIT
# ===================================================
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

UA_TZ = tz.gettz("Europe/Kyiv")


# ===================================================
# CONFIG
# ===================================================
BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"

# ⚠️ Якщо Google OAuth потрібен саме на docker-сервісі,
# зміни на https://nahadayka-backend-1.onrender.com і додай redirect URI у Google Console.
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
# ✅ OCR HEALTHCHECK
# ===================================================
@app.get("/api/ocr_health")
def ocr_health():
    info = {
        "which_tesseract": shutil.which("tesseract"),
        "tesseract_version": None,
        "error": None
    }
    try:
        info["tesseract_version"] = str(pytesseract.get_tesseract_version())
    except Exception as e:
        info["error"] = str(e)
    return jsonify(info), 200


# ===================================================
# OCR HELPERS
# ===================================================
def _preprocess_image(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    return img


def _ocr_text_from_bytes(img_bytes: bytes) -> str:
    img = Image.open(BytesIO(img_bytes))
    img = _preprocess_image(img)

    config = "--oem 1 --psm 6"
    # Якщо ukr не встановлений — буде помилка (але в тебе OCR health ок, значить все є)
    text = pytesseract.image_to_string(img, lang="ukr+eng", config=config)
    return text or ""


def _split_lines(text: str) -> list[str]:
    lines = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) >= 3:
            lines.append(line)
    return lines


def _parse_dt(candidate: str):
    return dateparser.parse(
        candidate,
        languages=["uk", "ru", "en"],  # ✅ додали ru
        settings={
            "TIMEZONE": "Europe/Kyiv",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        }
    )


UA_MONTHS = r"(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)"
RU_MONTHS = r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"


def _extract_datetime_from_line(line: str):
    """
    Повертає (date_str 'YYYY-MM-DD HH:MM', title)

    Підтримує:
      - 12.01.2026 14:30
      - 12/01 14:30
      - 2026-01-12 14:30
      - "сьогодні 18:00", "завтра 9:00"
      - "21 січня 2026", "21 січня"
      - "21 января 2026"
    """
    candidates = []

    # dd.mm(.yyyy) [hh:mm]
    candidates += re.findall(
        r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?:\s+\d{1,2}:\d{2})?\b",
        line
    )
    # yyyy-mm-dd [hh:mm]
    candidates += re.findall(
        r"\b\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?\b",
        line
    )

    # 21 січня 2026 (укр) + optional time
    candidates += re.findall(
        rf"\b\d{{1,2}}\s+{UA_MONTHS}(?:\s+\d{{4}})?(?:\s+\d{{1,2}}:\d{{2}})?\b",
        line.lower()
    )
    # 21 января 2026 (рус) + optional time
    candidates += re.findall(
        rf"\b\d{{1,2}}\s+{RU_MONTHS}(?:\s+\d{{4}})?(?:\s+\d{{1,2}}:\d{{2}})?\b",
        line.lower()
    )

    lowered = line.lower()
    if any(w in lowered for w in ["сьогодні", "завтра", "післязавтра"]):
        candidates.append(line)

    time_only = re.findall(r"\b\d{1,2}:\d{2}\b", line)

    dt = None
    used = None

    for c in candidates:
        parsed = _parse_dt(c)
        if parsed:
            dt = parsed
            used = c
            break

    # Якщо є тільки час — "сьогодні/завтра"
    if not dt and time_only:
        parsed_time = dateparser.parse(time_only[0], languages=["uk", "ru", "en"])
        if parsed_time:
            now = datetime.now(UA_TZ)
            dt_candidate = now.replace(
                hour=parsed_time.hour,
                minute=parsed_time.minute,
                second=0,
                microsecond=0
            )
            if dt_candidate < now:
                dt_candidate = dt_candidate + timedelta(days=1)
            dt = dt_candidate
            used = time_only[0]

    if not dt:
        return None, None

    # Якщо в рядку немає часу — ставимо 23:59
    if not re.search(r"\b\d{1,2}:\d{2}\b", line):
        dt = dt.replace(hour=23, minute=59, second=0, microsecond=0)

    date_str = dt.astimezone(UA_TZ).strftime("%Y-%m-%d %H:%M")

    # title = текст без дати
    title = line
    if used and len(used) < len(line):
        title = re.sub(re.escape(used), "", title, flags=re.IGNORECASE).strip(" -–—:;,")
    title = title.strip() or "Дедлайн з фото"

    return date_str, title


def _extract_items_from_text(text: str) -> list[dict]:
    lines = _split_lines(text)
    items = []
    seen = set()

    for line in lines:
        date_str, title = _extract_datetime_from_line(line)
        if not date_str:
            continue

        key = (title.lower(), date_str)
        if key in seen:
            continue
        seen.add(key)

        items.append({"title": title, "date": date_str})

    return items


# ===================================================
# 📷 SCAN IMAGE (REAL OCR)
# ===================================================
@app.post("/api/scan_image")
def scan_image():
    if "image" not in request.files:
        return jsonify({"items": [], "error": "no_image"}), 400

    file = request.files["image"]
    img_bytes = file.read()

    if not img_bytes:
        return jsonify({"items": [], "error": "empty_file"}), 400

    uid = request.form.get("uid") or request.args.get("uid") or "unknown"
    print(f"[scan_image] uid={uid}, filename={file.filename}, bytes={len(img_bytes)}")

    try:
        text = _ocr_text_from_bytes(img_bytes)
    except Exception as e:
        print("[OCR ERROR]", repr(e))
        return jsonify({"items": [], "error": "ocr_failed", "detail": str(e)}), 500

    items = _extract_items_from_text(text)

    return jsonify({
        "items": items,
        "raw_text": (text or "")[:4000]
    }), 200


# ===================================================
# ✅ ADD SCANNED ITEMS
# ===================================================
@app.post("/api/add_scanned/<user_id>")
def add_scanned(user_id):
    body = request.get_json() or {}
    items = body.get("items") or []

    if not isinstance(items, list) or not items:
        return jsonify({"error": "items required", "added": 0}), 400

    data = load_deadlines()
    data.setdefault(user_id, [])

    added = 0
    for it in items:
        title = str((it or {}).get("title", "")).strip()
        date = str((it or {}).get("date", "")).strip()

        if not title or not date:
            continue

        exists = any(d.get("title") == title and d.get("date") == date for d in data[user_id])
        if exists:
            continue

        data[user_id].append({
            "title": title,
            "date": date,
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
# GOOGLE CALLBACK
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

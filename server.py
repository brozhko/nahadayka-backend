from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import base64
import hashlib
from datetime import datetime
import requests
from zoneinfo import ZoneInfo

from flask_sqlalchemy import SQLAlchemy

# OpenAI
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
# CONFIG
# ===================================================
# ✅ BOT TOKEN лишається в коді (як ти хотів)
BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"
BOT_USERNAME = os.getenv("BOT_USERNAME", "nahadayka_bot").strip()


BACKEND_URL = os.getenv("BACKEND_URL", "https://nahadayka-backend.onrender.com").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://brozhko.github.io/nahadayka-bot_v1/").strip()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

CLIENT_SECRETS_FILE = "credentials.json"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]
REDIRECT_URI = f"{BACKEND_URL}/api/google_callback"


# ===================================================
# DB (SQLite local, Postgres on Render via DATABASE_URL)
# ===================================================
db_url = os.environ.get("DATABASE_URL", "").strip()
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///db.sqlite3"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.String(64), unique=True, nullable=False, index=True)


class Deadline(db.Model):
    __tablename__ = "deadlines"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    # ВАЖЛИВО: поле називається date, бо фронт/бот так очікують
    title = db.Column(db.String(255), nullable=False)
    date = db.Column(db.String(32), nullable=False)  # "YYYY-MM-DD HH:MM"
    last_notified = db.Column(db.Integer, nullable=True)


with app.app_context():
    db.create_all()


# ===================================================
# HELPERS: USERS + DEADLINES
# ===================================================
def _get_or_create_user(uid: str) -> User:
    uid = str(uid)
    user = User.query.filter_by(telegram_id=uid).first()
    if not user:
        user = User(telegram_id=uid)
        db.session.add(user)
        db.session.commit()
    return user


def _list_deadlines(uid: str):
    uid = str(uid)
    user = User.query.filter_by(telegram_id=uid).first()
    if not user:
        return []
    rows = Deadline.query.filter_by(user_id=user.id).order_by(Deadline.id.asc()).all()
    return [{"title": r.title, "date": r.date, "last_notified": r.last_notified} for r in rows]


def _all_users_dict():
    out = {}
    users = User.query.all()
    for u in users:
        rows = Deadline.query.filter_by(user_id=u.id).order_by(Deadline.id.asc()).all()
        out[u.telegram_id] = [{"title": r.title, "date": r.date, "last_notified": r.last_notified} for r in rows]
    return out


# ===================================================
# TELEGRAM SEND (з кнопкою web_app)
# ===================================================
def tg_send_message(chat_id: str, text: str):
    if not BOT_TOKEN or BOT_TOKEN.startswith("PASTE_"):
        return

    kb = {
        "inline_keyboard": [
            [{"text": "📲 Відкрити Нагадайку", "web_app": {"url": WEBAPP_URL}}],
        ]
    }

    if BOT_USERNAME and (not BOT_USERNAME.startswith("PASTE_")):
        kb["inline_keyboard"].append(
            [{"text": "🤖 Відкрити бота", "url": f"https://t.me/{BOT_USERNAME}"}]
        )

    payload = {
        "chat_id": str(chat_id),
        "text": text,
        "disable_web_page_preview": True,
        "reply_markup": kb
    }

    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=15
        )
    except Exception as e:
        print("TG send error:", e)


# ===================================================
# AI LIMITS + CACHE
# ===================================================
AI_LIMIT_PER_DAY = int(os.getenv("AI_LIMIT_PER_DAY", "5"))
AI_MIN_CONFIDENCE = float(os.getenv("AI_MIN_CONFIDENCE", "0.5"))
AI_CACHE_FILE = "ai_cache.json"
AI_USAGE_FILE = "ai_usage.json"


def _load_json_file(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json_file(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        # Render може “затирати” файлову систему між деплоями — кеш просто не буде довгим
        pass


def _today_key():
    return datetime.now(UA_TZ).strftime("%Y-%m-%d")


def _img_hash(img_bytes: bytes) -> str:
    return hashlib.sha256(img_bytes).hexdigest()


def _can_use_ai(uid: str):
    usage = _load_json_file(AI_USAGE_FILE, {})
    today = _today_key()
    usage.setdefault(today, {})
    usage[today].setdefault(uid, 0)

    used = int(usage[today][uid])
    remaining = max(0, AI_LIMIT_PER_DAY - used)
    return (used < AI_LIMIT_PER_DAY, remaining)


def _inc_ai_usage(uid: str):
    usage = _load_json_file(AI_USAGE_FILE, {})
    today = _today_key()
    usage.setdefault(today, {})
    usage[today].setdefault(uid, 0)
    usage[today][uid] = int(usage[today][uid]) + 1
    _save_json_file(AI_USAGE_FILE, usage)


def _filter_deadlines_by_confidence(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"deadlines": []}

    items = payload.get("deadlines", [])
    if not isinstance(items, list):
        return {"deadlines": []}

    filtered = []
    for d in items:
        if not isinstance(d, dict):
            continue
        conf = d.get("confidence", 0.0)
        try:
            conf = float(conf)
        except Exception:
            conf = 0.0

        if conf >= AI_MIN_CONFIDENCE:
            filtered.append(d)

    return {"deadlines": filtered, "min_confidence": AI_MIN_CONFIDENCE}


def get_ai_client():
    # ✅ тепер нормально читає ключ з Render ENV
    key = (OPENAI_API_KEY or "").strip()
    if not key or key.startswith("PASTE_"):
        return None
    return OpenAI(api_key=key)


def _openai_response_to_json(resp) -> dict:
    raw = getattr(resp, "output_text", None)

    if not raw:
        try:
            parts = []
            for item in (getattr(resp, "output", None) or []):
                for c in (getattr(item, "content", None) or []):
                    t = getattr(c, "text", None)
                    if t:
                        parts.append(t)
            raw = "\n".join(parts)
        except Exception:
            raw = ""

    try:
        return json.loads(raw) if raw else {"deadlines": []}
    except Exception:
        return {"deadlines": [], "raw_text": (raw or "")[:2000]}


# ===================================================
# API: RETURN ALL USERS (FOR CRON)
# ===================================================
@app.get("/api/all")
def all_users():
    return jsonify(_all_users_dict())


# ===================================================
# DEADLINES API
# ===================================================
@app.post("/api/deadlines/<user_id>")
def add_or_update_deadline(user_id):
    body = request.get_json(silent=True) or {}
    user = _get_or_create_user(user_id)

    if "last_notified_update" in body and "title" in body:
        title = str(body.get("title", "")).strip()
        new_val = body.get("last_notified_update")

        row = Deadline.query.filter_by(user_id=user.id, title=title).first()
        if not row:
            return jsonify({"error": "not found"}), 404

        row.last_notified = new_val
        db.session.commit()
        return jsonify({"updated": True})

    title = str(body.get("title", "")).strip()
    date = str(body.get("date", "")).strip()

    if not title or not date:
        return jsonify({"error": "title and date required"}), 400

    exists = Deadline.query.filter_by(user_id=user.id, title=title, date=date).first()
    if exists:
        return jsonify({"status": "exists"}), 200

    db.session.add(Deadline(user_id=user.id, title=title, date=date, last_notified=None))
    db.session.commit()
    return jsonify({"status": "added"}), 201


@app.get("/api/deadlines/<user_id>")
def get_deadlines(user_id):
    return jsonify(_list_deadlines(user_id))


@app.delete("/api/deadlines/<user_id>")
def delete_deadline(user_id):
    body = request.get_json(silent=True) or {}
    title = str(body.get("title", "")).strip()

    if not title:
        return jsonify({"status": "ok"})

    user = User.query.filter_by(telegram_id=str(user_id)).first()
    if not user:
        return jsonify({"status": "ok"})

    Deadline.query.filter_by(user_id=user.id, title=title).delete()
    db.session.commit()
    return jsonify({"status": "ok"})


# ===================================================
# 🤖 AI SCAN IMAGE
# ===================================================
@app.post("/api/scan_deadlines_ai")
def scan_deadlines_ai():
    if "image" not in request.files:
        return jsonify({"error": "no_image"}), 400

    uid = request.form.get("uid") or request.args.get("uid") or "unknown"

    file = request.files["image"]
    img_bytes = file.read()
    if not img_bytes:
        return jsonify({"error": "empty_file"}), 400

    MAX_MB = 8
    if len(img_bytes) > MAX_MB * 1024 * 1024:
        return jsonify({
            "error": "too_large",
            "message": f"Фото завелике (> {MAX_MB}MB). Зроби інше або стисни."
        }), 413

    img_key = _img_hash(img_bytes)
    cache = _load_json_file(AI_CACHE_FILE, {})
    if img_key in cache:
        cached_payload = cache[img_key]
        filtered = _filter_deadlines_by_confidence(cached_payload)
        return jsonify({"cached": True, "uid": uid, **filtered}), 200

    allowed, remaining = _can_use_ai(uid)
    if not allowed:
        return jsonify({
            "error": "rate_limited",
            "uid": uid,
            "limit_per_day": AI_LIMIT_PER_DAY,
            "message": "Ліміт AI на сьогодні вичерпаний. Спробуй завтра або зменш кількість сканів."
        }), 429

    client = get_ai_client()
    if not client:
        return jsonify({"error": "no_openai_key"}), 500

    mime = (file.mimetype or "").strip().lower()
    if not mime.startswith("image/"):
        mime = "image/jpeg"

    if mime in ("image/heic", "image/heif"):
        return jsonify({
            "error": "unsupported_image",
            "message": "Формат HEIC/HEIF може не підтримуватись. Увімкни 'Most Compatible' в камері або зроби скріншот фото."
        }), 415

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
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": f"data:{mime};base64,{img_b64}"},
                ],
            }],
            text={"format": {"type": "json_object"}},
        )

        payload = _openai_response_to_json(resp)

        cache[img_key] = payload
        _save_json_file(AI_CACHE_FILE, cache)

        _inc_ai_usage(uid)

        filtered = _filter_deadlines_by_confidence(payload)

        return jsonify({
            "cached": False,
            "uid": uid,
            "remaining_today": max(0, remaining - 1),
            **filtered
        }), 200

    except Exception as e:
        return jsonify({"error": "ai_failed", "detail": str(e)}), 500


# ===================================================
# ✅ ADD AI SCANNED -> save to DB
# ===================================================
@app.post("/api/add_ai_scanned/<user_id>")
def add_ai_scanned(user_id):
    body = request.get_json(silent=True) or {}
    deadlines = body.get("deadlines") or []

    if not isinstance(deadlines, list) or not deadlines:
        return jsonify({"error": "deadlines required", "added": 0}), 400

    user = _get_or_create_user(user_id)

    added = 0
    for d in deadlines:
        title = str((d or {}).get("title", "")).strip()
        due_date = (d or {}).get("due_date")
        due_time = (d or {}).get("due_time") or "23:59"

        if not title or not due_date:
            continue

        date_value = f"{due_date} {due_time}"

        exists = Deadline.query.filter_by(user_id=user.id, title=title, date=date_value).first()
        if exists:
            continue

        db.session.add(Deadline(user_id=user.id, title=title, date=date_value, last_notified=None))
        added += 1

    db.session.commit()
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
        state=str(user_id)
    )

    return jsonify({"auth_url": auth_url})


# ===================================================
# GOOGLE CALLBACK (✅ красивіше + авто-повернення)
# ===================================================
@app.get("/api/google_callback")
def google_callback():
    code = request.args.get("code")
    user_id = request.args.get("state")

    if not code or not user_id:
        return "<h3>Missing code/state</h3>", 400

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
    except Exception as e:
        return f"<h3>Google token error</h3><pre>{str(e)}</pre>", 500

    try:
        with open(f"token_{user_id}.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    except Exception as e:
        return f"<h3>Token save error</h3><pre>{str(e)}</pre>", 500

    imported_calendar = 0
    imported_gmail = 0
    try:
        imported_calendar = import_google_calendar(user_id, creds)
    except Exception:
        imported_calendar = 0

    try:
        imported_gmail = import_gmail(user_id, creds)
    except Exception:
        imported_gmail = 0

    msg = (
        f"✅ Google підключено!\n"
        f"📅 Календар: імпортовано {imported_calendar} подій\n"
        f"📧 Gmail: знайдено {imported_gmail} листів із завданнями"
    )
    try:
        tg_send_message(user_id, msg)
    except Exception:
        pass

    tg_link = f"https://t.me/{BOT_USERNAME}?start=google_done"

    html = f"""
<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Готово</title>
  <style>
    :root {{
      --bg1:#0B121C; --bg2:#0F1B2E; --card:#121B2A; --txt:#E8EEF6;
      --muted:rgba(232,238,246,.78); --b:rgba(255,255,255,.10);
      --accent:#2B6CFF; --accent2:#7C5CFF;
    }}
    *{{ box-sizing:border-box; }}
    body {{
      margin:0; min-height:100vh;
      font-family: system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;
      color:var(--txt);
      background:
        radial-gradient(900px 500px at 20% 10%, rgba(43,108,255,.22), transparent 60%),
        radial-gradient(700px 420px at 80% 30%, rgba(124,92,255,.18), transparent 60%),
        linear-gradient(180deg, var(--bg1), var(--bg2));
      display:flex; align-items:center; justify-content:center;
      padding: 24px;
    }}
    .wrap {{ width:min(560px, 100%); }}
    .card {{
      background: rgba(18,27,42,.92);
      border:1px solid var(--b);
      border-radius: 18px;
      padding: 22px;
      box-shadow: 0 18px 55px rgba(0,0,0,.45);
      backdrop-filter: blur(10px);
    }}
    .top {{
      display:flex; gap:14px; align-items:center;
      margin-bottom: 10px;
    }}
    .icon {{
      width:44px; height:44px; border-radius: 14px;
      display:grid; place-items:center;
      background: linear-gradient(135deg, rgba(43,108,255,.25), rgba(124,92,255,.22));
      border: 1px solid var(--b);
      flex: 0 0 auto;
    }}
    h1 {{ margin:0; font-size:20px; }}
    .meta {{ margin: 6px 0 0; color:var(--muted); line-height:1.55; }}
    .row {{
      display:flex; gap:12px; align-items:center; margin-top: 16px; flex-wrap:wrap;
    }}
    .btn {{
      display:inline-flex; align-items:center; justify-content:center;
      gap:10px;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      color:white; text-decoration:none;
      padding: 12px 14px; border-radius: 14px;
      font-weight: 800;
      border: 1px solid rgba(255,255,255,.14);
      box-shadow: 0 12px 30px rgba(43,108,255,.22);
    }}
    .btn:active {{ transform: translateY(1px); }}
    .pill {{
      display:inline-flex; align-items:center; gap:8px;
      padding:10px 12px;
      border-radius: 999px;
      border: 1px solid var(--b);
      color: var(--muted);
      background: rgba(255,255,255,.04);
      font-size: 13px;
    }}
    .spin {{
      width:14px; height:14px;
      border-radius:50%;
      border:2px solid rgba(232,238,246,.25);
      border-top-color: rgba(232,238,246,.85);
      animation: s 0.9s linear infinite;
    }}
    @keyframes s {{ to {{ transform: rotate(360deg); }} }}
    .small {{ margin-top: 10px; color: rgba(232,238,246,.6); font-size: 12.5px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="top">
        <div class="icon">✅</div>
        <div>
          <h1>Google підключено</h1>
          <div class="meta">Можеш повернутись у Telegram. Зараз спробую перекинути автоматично.</div>
        </div>
      </div>

      <div class="row">
        <a class="btn" href="{tg_link}">Повернутись в Telegram</a>
        <div class="pill"><span class="spin"></span>Автоповернення…</div>
      </div>

      <div class="small">
        Якщо кнопка не відкрилась — відкрий чат з ботом вручну і натисни «Меню» → «Синхронізація».
      </div>
    </div>
  </div>

  <script>
    setTimeout(function() {{
      window.location.href = "{tg_link}";
    }}, 900);
  </script>
</body>
</html>
"""
    return html


# ===================================================
# GOOGLE SYNC (manual)
# ===================================================
@app.post("/api/google_sync/<user_id>")
def google_sync(user_id):
    token_path = f"token_{user_id}.json"
    if not os.path.exists(token_path):
        return jsonify({"error": "no_token"}), 401

    creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    cal = import_google_calendar(user_id, creds)
    gm = import_gmail(user_id, creds)
    tg_send_message(user_id, f"🔄 Ручна синхронізація:\n📅 {cal}\n📧 {gm}")

    return jsonify({"calendar": cal, "gmail": gm})


# ===================================================
# IMPORT EVENTS FROM CALENDAR (to DB)
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
    user = _get_or_create_user(user_id)

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

        exists = Deadline.query.filter_by(user_id=user.id, title=title, date=date_value).first()
        if exists:
            continue

        db.session.add(Deadline(user_id=user.id, title=title, date=date_value, last_notified=None))
        imported += 1

    db.session.commit()
    return imported


# ===================================================
# 📧 IMPORT FROM GMAIL (to DB)
# ===================================================
KEYWORDS = [k.lower() for k in ["лаба", "лаб", "завдання", "звіт", "робота", "кр", "практична"]]
LPNU_DOMAIN = "@lpnu.ua"


def import_gmail(user_id, creds):
    try:
        service = build("gmail", "v1", credentials=creds)
    except Exception:
        return 0

    query = f"from:{LPNU_DOMAIN}"
    result = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    messages = result.get("messages", [])

    user = _get_or_create_user(user_id)

    added = 0
    for msg in messages:
        full = service.users().messages().get(userId="me", id=msg["id"]).execute()
        headers = full.get("payload", {}).get("headers", [])

        subject = next((h["value"] for h in headers if h.get("name") == "Subject"), "Без теми")
        date_header = next((h["value"] for h in headers if h.get("name") == "Date"), None)
        if not date_header:
            continue

        if not any(k in subject.lower() for k in KEYWORDS):
            continue

        try:
            base = date_header[:25]
            date_obj = datetime.strptime(base, "%a, %d %b %Y %H:%M:%S")
            date_str = date_obj.strftime("%Y-%m-%d 23:59")
        except Exception:
            continue

        exists = Deadline.query.filter_by(user_id=user.id, title=subject, date=date_str).first()
        if exists:
            continue

        db.session.add(Deadline(user_id=user.id, title=subject, date=date_str, last_notified=None))
        added += 1

    db.session.commit()
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


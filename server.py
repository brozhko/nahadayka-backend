import os
import json
from typing import Dict, List, Any
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify, redirect, url_for
from flask_cors import CORS

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from dateutil import parser as date_parser

# =====================================
#  НАЛАШТУВАННЯ
# =====================================
APP_PORT = int(os.environ.get("PORT", 5000))

# Файл з дедлайнами бекенду
DATA_FILE = "deadlines_backend.json"

# Файл з токенами Google (user_id -> credentials)
TOKENS_FILE = "google_tokens.json"

# Шлях до client_secret (credentials.json з Google Cloud)
GOOGLE_CLIENT_SECRETS_FILE = "credentials.json"

# Ті самі SCOPES, що й у проекті
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Базовий URL твого бекенду на Render
BACKEND_BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    "https://nahadayka-backend.onrender.com"
)

# Redirect URI МАЄ співпадати з тим, що ти вказав у Google Cloud
REDIRECT_URI = f"{BACKEND_BASE_URL}/api/oauth2callback"

app = Flask(__name__)
CORS(app)


# =====================================
#  ХЕЛПЕРИ ДЛЯ ФАЙЛІВ
# =====================================
def load_data() -> Dict[str, List[Dict[str, Any]]]:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_data(data: Dict[str, List[Dict[str, Any]]]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_tokens() -> Dict[str, Any]:
    if not os.path.exists(TOKENS_FILE):
        return {}
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_tokens(data: Dict[str, Any]) -> None:
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =====================================
#  DEADLINES API
#  /api/deadlines/<user_id>
# =====================================
@app.route("/api/deadlines/<user_id>", methods=["GET", "POST", "DELETE"])
def deadlines(user_id: str):
    data = load_data()

    # ------- GET -------
    if request.method == "GET":
        return jsonify(data.get(user_id, [])), 200

    # ------- JSON body -------
    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    # ------- POST: додати дедлайн -------
    if request.method == "POST":
        title = (payload.get("title") or "").strip()
        date = (payload.get("date") or "").strip()

        if not title or not date:
            return jsonify({"error": "title і date обовʼязкові"}), 400

        user_list = data.setdefault(user_id, [])
        new_deadline = {
            "title": title,
            "date": date,
            "last_notified": None,
        }
        user_list.append(new_deadline)
        save_data(data)
        return jsonify(new_deadline), 201

    # ------- DELETE: видалити по title -------
    if request.method == "DELETE":
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title обовʼязковий для видалення"}), 400

        user_list = data.get(user_id, [])
        new_list = [d for d in user_list if d.get("title") != title]

        if len(new_list) == len(user_list):
            return jsonify({"error": "Дедлайн з таким title не знайдено"}), 404

        data[user_id] = new_list
        save_data(data)
        return jsonify({"status": "ok", "deleted_title": title}), 200


# =====================================
#  GOOGLE LOGIN (ОТРИМАТИ auth_url)
#  /api/google_login/<user_id>
# =====================================
@app.route("/api/google_login/<user_id>", methods=["GET"])
def google_login(user_id: str):
    """
    Викликається ботом, коли WebApp надсилає action = "sync".
    Повертаємо auth_url, де користувач залогіниться через Google.
    """
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        # передамо telegram user_id у state, щоб в callback знати, кому зберігати токен
        state=user_id,
        prompt="consent",
    )

    return jsonify({"auth_url": auth_url}), 200


# =====================================
#  OAUTH2 CALLBACK
#  /api/oauth2callback
# =====================================
@app.route("/api/oauth2callback")
def oauth2callback():
    """
    Сюди повертає Google після логіну.
    Тут міняємо code -> tokens, зберігаємо їх і робимо імпорт календаря.
    """
    # Помилки від Google
    error = request.args.get("error")
    if error:
        return f"Google OAuth error: {error}", 400

    # У state ми передавали telegram user_id
    user_id = request.args.get("state")
    code = request.args.get("code")

    if not user_id or not code:
        return "Missing state or code parameter", 400

    # Збираємо той самий Flow
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    # Обмін коду на токени
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Зберігаємо credentials у файл TOKENS_FILE (по user_id)
    tokens = load_tokens()
    tokens[user_id] = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    save_tokens(tokens)

    # Після успішного логіну — одразу імпортуємо календар
    imported_count = import_calendar_for_user(user_id)

    # Проста сторінка, яку побачить користувач
    return f"""
    <html>
      <head><title>Імпорт календаря</title></head>
      <body style="font-family: sans-serif;">
        <h2>✅ Імпорт виконано</h2>
        <p>Імпортовано {imported_count} подій з Google Calendar.</p>
        <p>Тепер можеш повернутись до Telegram і оновити список дедлайнів у WebApp.</p>
      </body>
    </html>
    """


# =====================================
#  ІМПОРТ КАЛЕНДАРЯ ДЛЯ КОРИСТУВАЧА
# =====================================
def import_calendar_for_user(user_id: str) -> int:
    """
    Читає збережені Google-токени користувача,
    тягне події з Calendar і зберігає їх у наші дедлайни.
    Повертає кількість імпортованих подій.
    """
    tokens = load_tokens()
    cred_data = tokens.get(user_id)
    if not cred_data:
        return 0

    creds = Credentials(
        token=cred_data["token"],
        refresh_token=cred_data.get("refresh_token"),
        token_uri=cred_data["token_uri"],
        client_id=cred_data["client_id"],
        client_secret=cred_data["client_secret"],
        scopes=cred_data["scopes"],
    )

    # Якщо токен протух — google-auth сам оновить його
    service = build("calendar", "v3", credentials=creds)

    # Часовий діапазон: від зараз до +90 днів
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=90)).isoformat()

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        )
        .execute()
    )

    events = events_result.get("items", [])

    data = load_data()
    user_list = data.setdefault(user_id, [])

    # Простий захист від дублювання: по (title, date)
    existing_pairs = {(d["title"], d["date"]) for d in user_list}

    imported_count = 0

    for ev in events:
        summary = ev.get("summary") or "Без назви події"

        # Дата початку: або date (цілий день), або dateTime
        start = ev.get("start", {})
        date_str = start.get("dateTime") or start.get("date")
        if not date_str:
            continue

        # Форматуємо у вигляді "YYYY-MM-DD HH:MM"
        try:
            dt = date_parser.parse(date_str)
            # якщо без часу, буде 00:00
            formatted = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            formatted = date_str

        key = (summary, formatted)
        if key in existing_pairs:
            continue

        user_list.append(
            {
                "title": summary,
                "date": formatted,
                "last_notified": None,
            }
        )
        existing_pairs.add(key)
        imported_count += 1

    save_data(data)
    return imported_count


# =====================================
#  MAIN
# =====================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=True)

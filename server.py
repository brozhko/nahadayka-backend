# server.py
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import json
from datetime import datetime
from typing import Dict, List, Any
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import pathlib
import os

DATA_FILE = "deadlines.json"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ==========================
# 🔄 ЛОКАЛЬНІ ФУНКЦІЇ
# ==========================
def load_deadlines() -> Dict[str, List[Dict[str, Any]]]:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except:
        return {}

def save_deadlines(data: Dict[str, List[Dict[str, Any]]]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ==========================
# 🧪 Healthcheck
# ==========================
@app.get("/api/health")
def health():
    return {"status": "ok"}

# ==========================
# 📌 CRUD ДЕДЛАЙНИ
# ==========================
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

    # Валідація формату дати
    try:
        if len(date_str) == 10:
            datetime.strptime(date_str, "%Y-%m-%d")
        else:
            datetime.strptime(date_str, "%Y-%m-%d %H:%M")
    except:
        return jsonify({"error": "Bad date format"}), 400

    data = load_deadlines()
    data.setdefault(user_id, [])

    item = {"title": title, "date": date_str, "last_notified": None}
    data[user_id].append(item)
    save_deadlines(data)

    return jsonify(item), 201

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

# ==========================
# 🔐 GOOGLE OAUTH — КРОК 1
# ==========================
@app.get("/api/google_auth")
def google_auth():
    if not os.path.exists(CREDENTIALS_FILE):
        return jsonify({"error": "credentials.json not found"}), 500

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        redirect_uri="https://nahadayka-backend.onrender.com/api/google_callback"
    )

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )

    # зберігаємо state
    with open("state.txt", "w") as f:
        f.write(state)

    return jsonify({"auth_url": auth_url})

# ==========================
# 🔐 GOOGLE OAUTH — КРОК 2 (callback)
# ==========================
@app.get("/api/google_callback")
def google_callback():
    if not os.path.exists("state.txt"):
        return "STATE not found", 400

    state = open("state.txt").read()

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        state=state,
        redirect_uri="https://nahadayka-backend.onrender.com/api/google_callback"
    )

    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    return "Google авторизація успішна! Можете повернутися в Telegram."

# ==========================
# ▶ RUN
# ==========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

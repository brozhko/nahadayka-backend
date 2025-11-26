# server.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import json
from datetime import datetime
from typing import Dict, List, Any

DATA_FILE = "deadlines.json"

app = Flask(__name__)
# Дозволяємо запити з фронтенду (GitHub Pages, локально тощо)
CORS(app, resources={r"/api/*": {"origins": "*"}})


def load_deadlines() -> Dict[str, List[Dict[str, Any]]]:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_deadlines(data: Dict[str, List[Dict[str, Any]]]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/deadlines/<user_id>")
def get_deadlines(user_id: str):
    data = load_deadlines()
    items = data.get(user_id, [])
    return jsonify(items)


@app.post("/api/deadlines/<user_id>")
def add_deadline(user_id: str):
    body = request.get_json(force=True)
    title = (body.get("title") or "").strip()
    date_str = (body.get("date") or "").strip()

    if not title or not date_str:
        return jsonify({"error": "title and date are required"}), 400

    # валідація формату дати (як у бота)
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


if __name__ == "__main__":
    # локально: python server.py
    app.run(host="0.0.0.0", port=8000, debug=True)

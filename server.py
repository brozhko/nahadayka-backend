import json
import os
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS

DATA_FILE = "deadlines.json"

app = Flask(__name__)
CORS(app)


def load_data():
    """Читаємо всі дедлайни з файлу."""
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_data(data: dict) -> None:
    """Зберігаємо всі дедлайни у файл."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route("/api/health", methods=["GET"])
def health():
    """Проста перевірка, що бекенд живий."""
    return jsonify({"status": "ok"})


@app.route("/api/deadlines", methods=["GET"])
def get_deadlines():
    """Отримати всі дедлайни для одного користувача."""
    telegram_id = request.args.get("telegram_id")

    if not telegram_id:
        return jsonify({"error": "telegram_id is required"}), 400

    data = load_data()
    user_deadlines = data.get(telegram_id, [])

    return jsonify({"deadlines": user_deadlines})


@app.route("/api/deadlines", methods=["POST"])
def add_deadline():
    """
    Додати новий дедлайн.
    Очікує JSON:
    {
      "telegram_id": "123456",
      "title": "Лаба з фізики",
      "due_at": "2025-12-05T14:00:00",
      "subject": "Фізика"
    }
    """
    payload = request.get_json(force=True, silent=True) or {}

    telegram_id = payload.get("telegram_id")
    title = payload.get("title")
    due_at = payload.get("due_at")
    subject = payload.get("subject", "")

    if not telegram_id or not title or not due_at:
        return (
            jsonify(
                {"error": "Fields telegram_id, title and due_at are required"}
            ),
            400,
        )

    # Проста перевірка формату дати
    try:
        datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    except ValueError:
        return jsonify({"error": "due_at must be ISO datetime string"}), 400

    data = load_data()
    user_list = data.setdefault(telegram_id, [])

    new_item = {
        "id": len(user_list) + 1,
        "title": title,
        "due_at": due_at,
        "subject": subject,
    }

    user_list.append(new_item)
    save_data(data)

    return jsonify(new_item), 201


if __name__ == "__main__":
    # Локальний запуск
    app.run(host="0.0.0.0", port=5000, debug=True)

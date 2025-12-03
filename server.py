import json
import os
from flask import Flask, request, jsonify
from flask_cors import CORS

DATA_FILE = "deadlines.json"

app = Flask(__name__)
CORS(app)

# ============================
# HELPERS
# ============================

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ============================
# HEALTH CHECK
# ============================

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

# ============================
# GET DEADLINES
# ============================

@app.route("/api/deadlines/<user_id>", methods=["GET"])
def get_deadlines(user_id):
    data = load_data()
    return jsonify(data.get(user_id, []))

# ============================
# ADD DEADLINE
# ============================

@app.route("/api/deadlines", methods=["POST"])
def add_deadline():
    payload = request.get_json() or {}

    user_id = payload.get("user_id")
    title = payload.get("title")
    date = payload.get("date")  # ISO string from JS

    if not user_id or not title or not date:
        return jsonify({"error": "Fields user_id, title, date required"}), 400

    data = load_data()
    user_list = data.setdefault(user_id, [])

    new_item = {
        "title": title,
        "date": date,
    }

    user_list.append(new_item)
    save_data(data)

    return jsonify(new_item), 201

# ============================
# DELETE DEADLINE
# ============================

@app.route("/api/deadlines/delete", methods=["POST"])
def delete_deadline():
    payload = request.get_json() or {}

    user_id = payload.get("user_id")
    title = payload.get("title")
    date = payload.get("date")

    if not user_id or not title or not date:
        return jsonify({"error": "Fields user_id, title, date required"}), 400

    data = load_data()

    if user_id not in data:
        return jsonify({"status": "not_found"}), 404

    before = len(data[user_id])
    data[user_id] = [
        d for d in data[user_id]
        if not (d["title"] == title and d["date"] == date)
    ]

    save_data(data)

    return jsonify({"removed": before - len(data[user_id])})

# ============================
# IMPORT GOOGLE (placeholder)
# ============================

@app.route("/api/import/google", methods=["POST"])
def import_google():
    payload = request.get_json() or {}
    user_id = payload.get("user_id")

    print("Google import requested by:", user_id)

    # ПОКИ ПРОСТО ПОВЕРТАЄМО SUCCESS
    # (пізніше додамо API Google Calendar)
    return jsonify({"status": "ok"})

# ============================
# START
# ============================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

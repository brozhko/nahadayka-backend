import requests
from datetime import datetime

BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"
BACKEND = "https://nahadayka-backend.onrender.com/api"

WARNING_DAYS = {3, 2, 1}

def get_all_users():
    """Отримує ID всіх користувачів із backend JSON."""
    try:
        data = requests.get(f"{BACKEND}/all").json()
        return data.keys()
    except:
        return []

def get_deadlines(uid):
    try:
        return requests.get(f"{BACKEND}/deadlines/{uid}").json()
    except:
        return []

def update_last_notified(uid, title, value):
    try:
        requests.post(f"{BACKEND}/deadlines/{uid}", json={
            "title": title,
            "last_notified_update": value
        })
    except:
        pass

def send_message(uid, text):
    requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        params={"chat_id": uid, "text": text}
    )

def run_checker():
    today = datetime.now().date()

    users = get_all_users()
    for uid in users:
        items = get_deadlines(uid)

        for d in items:
            title = d["title"]
            date_str = d["date"]
            last_notified = d.get("last_notified")

            # Парсимо дату
            try:
                base = date_str.split()[0]
                date_obj = datetime.strptime(base, "%Y-%m-%d").date()
            except:
                continue

            diff = (date_obj - today).days

            # Нагадуємо за 3, 2, 1 день
            if diff in WARNING_DAYS and last_notified != diff:
                send_message(uid, f"До «{title}» лишилось {diff} дні(в)")
                update_last_notified(uid, title, diff)


if __name__ == "__main__":
    run_checker()

import requests
from datetime import datetime

BOT_TOKEN = "8593319031:AAF5UQTx7g8hKMgkQxXphGM5nsi-GQ_hOZg"
BACKEND = "https://nahadayka-backend.onrender.com/api"

WARNING_DAYS = {3, 2, 1}

# ----------------------------
# Правильні українські форми
# ----------------------------
def plural_days(n: int) -> str:
    n = abs(int(n))
    if n == 1:
        return "день"
    if 2 <= n <= 4:
        return "дні"
    return "днів"


def get_all_users():
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

            # Парсимо базову частину дати
            try:
                base = date_str.split()[0]
                date_obj = datetime.strptime(base, "%Y-%m-%d").date()
            except:
                continue

            diff = (date_obj - today).days

            if diff in WARNING_DAYS and last_notified != diff:
                msg = f"📌 До «{title}» залишилось: {diff} {plural_days(diff)}"
                send_message(uid, msg)
                update_last_notified(uid, title, diff)


if __name__ == "__main__":
    run_checker()

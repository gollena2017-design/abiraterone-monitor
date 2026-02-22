import requests
import os
import json
import re
from bs4 import BeautifulSoup
from datetime import datetime

URL = "https://eliky.in.ua/medicament/10986"

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

STATE_FILE = "last_record.json"


def fetch():
    r = requests.get(URL, timeout=60)
    r.raise_for_status()
    return r.text


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=30
    )


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def extract_records(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    records = []

    # шукаємо блоки з датою + абіратерон
    pattern = re.compile(
        r"(\d{2}\.\d{2}\.\d{4}).{0,120}?(абіратерон|abiraterone).{0,200}?",
        re.IGNORECASE | re.DOTALL
    )

    for match in pattern.finditer(text):
        chunk = match.group(0)

        date_match = re.search(r"\d{2}\.\d{2}\.\d{4}", chunk)
        qty_match = re.search(r"(\d+)\s*(таб|tab)", chunk, re.IGNORECASE)

        date = date_match.group(0) if date_match else None
        qty = qty_match.group(1) if qty_match else "невідомо"

        # пробуємо витягнути назву лікарні (рядок між датою і кількістю)
        lines = [l.strip() for l in chunk.split("\n") if len(l.strip()) > 5]
        facility = lines[1] if len(lines) > 1 else "невідомий заклад"

        if date:
            records.append({
                "date": date,
                "facility": facility,
                "qty": qty
            })

    return records


def newest_record(records):
    if not records:
        return None

    def to_date(r):
        return datetime.strptime(r["date"], "%d.%m.%Y")

    return sorted(records, key=to_date)[-1]


def main():
    html = fetch()
    records = extract_records(html)

    if not records:
        print("❗ Абіратерон не знайдено")
        return

    latest = newest_record(records)
    saved = load_state()

    if saved == latest:
        print("ℹ️ Нових поставок немає")
        return

    # нове постачання!
    message = (
        f"🆕 <b>Нове надходження Абіратерону</b>\n\n"
        f"📅 Дата: <b>{latest['date']}</b>\n"
        f"🏥 Заклад: {latest['facility']}\n"
        f"📦 Кількість: {latest['qty']} таб.\n\n"
        f"🔎 Перевірити:\n"
        f"<a href='{URL}'>Є-Ліки</a>\n"
        f"<a href='https://unci.org.ua/'>Національний інститут раку</a>"
    )

    send(message)
    save_state(latest)
    print("✅ Надіслано повідомлення про нове постачання")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Агент моніторингу ЄЛіки — Абіратерон
Запускається через GitHub Actions кожну годину.
Токен і Chat ID зберігаються у GitHub Secrets.
"""

import os
import json
import hashlib
import logging
import requests
from bs4 import BeautifulSoup

URL = "https://eliky.in.ua/medicament/10986"
STATE_FILE = "state.json"

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def fetch_records() -> list[dict]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(URL, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    records = []

    table = soup.find("table")
    if not table:
        log.warning("Таблицю не знайдено на сторінці!")
        return records

    rows = table.find_all("tr")[1:]  # пропускаємо заголовок
    for row in rows:
        cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cols) >= 5:
            records.append({
                "region":   cols[0],
                "form":     cols[1],
                "hospital": cols[2],
                "quantity": cols[3],
                "date":     cols[4],
            })

    return records


def record_id(rec: dict) -> str:
    key = f"{rec['hospital']}|{rec['quantity']}|{rec['date']}"
    return hashlib.md5(key.encode()).hexdigest()


def load_state() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_state(known: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(known), f, ensure_ascii=False, indent=2)


def send_telegram(text: str) -> None:
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }
    r = requests.post(api_url, json=payload, timeout=15)
    r.raise_for_status()
    log.info("✅ Telegram повідомлення надіслано.")


def format_message(rec: dict) -> str:
    return (
        f"💊 <b>АБІРАТЕРОН — новий запис</b>\n\n"
        f"🏥 <b>Лікарня:</b> {rec['hospital']}\n"
        f"📍 <b>Область:</b> {rec['region']}\n"
        f"💊 <b>Кількість:</b> {rec['quantity']}\n"
        f"📅 <b>Дата оновлення:</b> {rec['date']}\n\n"
        f"🔗 <a href='{URL}'>Переглянути на ЄЛіки</a>"
    )


def main():
    log.info("=== Перевірка ЄЛіки ===")

    known_ids = load_state()
    log.info(f"Відомих записів у стані: {len(known_ids)}")

    records = fetch_records()
    log.info(f"Знайдено записів на сторінці: {len(records)}")

    new_count = 0
    for rec in records:
        rid = record_id(rec)
        if rid not in known_ids:
            log.info(f"🆕 Новий: {rec['hospital']} | {rec['quantity']} | {rec['date']}")
            send_telegram(format_message(rec))
            known_ids.add(rid)
            new_count += 1

    if new_count == 0:
        log.info("Нових записів немає.")
    else:
        log.info(f"Надіслано {new_count} повідомлень.")

    save_state(known_ids)
    log.info("Стан збережено.")


if __name__ == "__main__":
    main()

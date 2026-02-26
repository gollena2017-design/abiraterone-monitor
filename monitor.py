#!/usr/bin/env python3
"""
Агент моніторингу Абіратерону — два джерела:
  1. https://eliky.in.ua/medicament/10986  — таблиця залишків по лікарнях
  2. https://unci.org.ua/bezoplatni-liky   — таблиця НІР, пошук по назві

Запускається через GitHub Actions. Токен і Chat ID — у GitHub Secrets.
"""

import os
import json
import hashlib
import logging
import requests
from bs4 import BeautifulSoup

ELIKY_URL = "https://eliky.in.ua/medicament/10986"
UNCI_URL  = "https://unci.org.ua/bezoplatni-liky"
STATE_FILE = "state.json"

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID        = os.environ["CHAT_ID"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Стан ──────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return {"eliky_ids": data, "unci_ids": [], "unci_update_date": ""}
            return data
    return {"eliky_ids": [], "unci_ids": [], "unci_update_date": ""}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def make_id(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=15)
    r.raise_for_status()
    log.info("Telegram надіслано.")


# ── Джерело 1: ЄЛіки ─────────────────────────────────────────────────────────

def fetch_eliky() -> list:
    resp = requests.get(ELIKY_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    records = []
    table = soup.find("table")
    if not table:
        log.warning("ЄЛіки: таблицю не знайдено")
        return records
    for row in table.find_all("tr")[1:]:
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


def format_eliky(rec: dict) -> str:
    return (
        "💊 <b>АБІРАТЕРОН — ЄЛіки (новий запис)</b>\n\n"
        "🏥 <b>Лікарня:</b> {hospital}\n"
        "📍 <b>Область:</b> {region}\n"
        "💊 <b>Кількість:</b> {quantity}\n"
        "📅 <b>Дата оновлення:</b> {date}\n\n"
        "🔗 <a href='" + ELIKY_URL + "'>Переглянути на ЄЛіки</a>"
    ).format(**rec)


def check_eliky(state: dict) -> None:
    log.info("── Перевірка ЄЛіки ──")
    known = set(state["eliky_ids"])
    try:
        records = fetch_eliky()
    except Exception as e:
        log.error(f"ЄЛіки: помилка — {e}")
        return
    log.info(f"ЄЛіки: знайдено {len(records)} записів")
    new_count = 0
    for rec in records:
        rid = make_id(rec["hospital"], rec["quantity"], rec["date"])
        if rid not in known:
            log.info(f"Новий ЄЛіки: {rec['hospital']} | {rec['quantity']} | {rec['date']}")
            try:
                send_telegram(format_eliky(rec))
            except Exception as e:
                log.error(f"Telegram помилка: {e}")
            known.add(rid)
            new_count += 1
    log.info(f"ЄЛіки: нових записів {new_count}")
    state["eliky_ids"] = list(known)


# ── Джерело 2: НІР (unci.org.ua) ─────────────────────────────────────────────
# Заголовки таблиці:
# 0=Назва | 1=Діюча речовина | 2=Приміщення | 3=Наказ |
# 4=Од.вим. | 5=Кіль-ть од. | 6=Термін | 7=Форма випуску | 8=№ партії

ABIRATERONE_KEYWORDS = ["абіратерон", "abiraterone"]


def fetch_unci():
    resp = requests.get(UNCI_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Дата оновлення сторінки
    update_date = ""
    for tag in soup.find_all(string=True):
        text = tag.strip()
        if "оновлено" in text.lower() and len(text) < 80:
            update_date = text
            break

    # Рядки з абіратероном
    records = []
    table = soup.find("table")
    if not table:
        log.warning("НІР: таблицю не знайдено")
        return update_date, records

    for row in table.find_all("tr")[1:]:
        cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if not cols:
            continue
        name_lc = cols[0].lower()
        subst_lc = cols[1].lower() if len(cols) > 1 else ""
        if any(kw in name_lc or kw in subst_lc for kw in ABIRATERONE_KEYWORDS):
            def c(i): return cols[i] if len(cols) > i else ""
            records.append({
                "name":     c(0),
                "subst":    c(1),
                "storage":  c(2),
                "order":    c(3),
                "unit":     c(4),
                "quantity": c(5),   # ← "Кіль-ть од." — індекс 5
                "expiry":   c(6),
                "form":     c(7),
                "batch":    c(8),
            })

    return update_date, records


def format_unci(rec: dict, update_date: str) -> str:
    return (
        "🏥 <b>АБІРАТЕРОН — НІР (новий запис)</b>\n\n"
        "💊 <b>Назва:</b> {name}\n"
        "🧪 <b>Діюча речовина:</b> {subst}\n"
        "📦 <b>Приміщення:</b> {storage}\n"
        "🔢 <b>Кількість:</b> {quantity} {unit}\n"
        "📅 <b>Термін придатності:</b> {expiry}\n"
        "💉 <b>Форма випуску:</b> {form}\n"
        "🔖 <b>Партія:</b> {batch}\n"
        "🗓 <b>Дата оновлення сторінки:</b> " + update_date + "\n\n"
        "🔗 <a href='" + UNCI_URL + "'>Переглянути на сайті НІР</a>"
    ).format(**rec)


def check_unci(state: dict) -> None:
    log.info("── Перевірка НІР ──")
    known = set(state["unci_ids"])
    last_update = state.get("unci_update_date", "")

    try:
        update_date, records = fetch_unci()
    except Exception as e:
        log.error(f"НІР: помилка — {e}")
        return

    log.info(f"НІР: дата оновлення — «{update_date}»")

    if not records:
        log.info("НІР: Абіратерону на сторінці не знайдено")
        if update_date and update_date != last_update and last_update:
            try:
                send_telegram(
                    "🏥 <b>НІР — сторінку оновлено</b>\n\n"
                    f"🗓 <b>Нова дата:</b> {update_date}\n"
                    "❌ Абіратерону у списку <b>не знайдено</b>\n\n"
                    f"🔗 <a href='{UNCI_URL}'>Переглянути</a>"
                )
            except Exception as e:
                log.error(f"Telegram помилка: {e}")
        state["unci_update_date"] = update_date
        return

    log.info(f"НІР: знайдено {len(records)} записів з Абіратероном")
    new_count = 0
    for rec in records:
        rid = make_id(rec["name"], rec["quantity"], rec["expiry"], rec["batch"], update_date)
        if rid not in known:
            log.info(f"Новий НІР: {rec['name']} | {rec['quantity']} {rec['unit']} | партія {rec['batch']}")
            try:
                send_telegram(format_unci(rec, update_date))
            except Exception as e:
                log.error(f"Telegram помилка: {e}")
            known.add(rid)
            new_count += 1

    log.info(f"НІР: нових записів {new_count}")
    state["unci_ids"] = list(known)
    state["unci_update_date"] = update_date


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("════ Моніторинг Абіратерону ════")
    state = load_state()
    check_eliky(state)
    check_unci(state)
    save_state(state)
    log.info("════ Готово. Стан збережено. ════")


if __name__ == "__main__":
    main()

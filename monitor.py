#!/usr/bin/env python3
"""
Агент моніторингу Абіратерону — два джерела:
  1. https://eliky.in.ua/medicament/10986  — кожні 30 хв
  2. https://unci.org.ua/bezoplatni-liky   — чт/пт о 8:00 і 17:00 UTC

Логіка НІР:
  - Четвер 08:00 і 17:00 — завжди перевіряємо
  - П'ятниця 08:00 і 17:00 — перевіряємо тільки якщо в четвер оновлення НЕ знайшли
  - Якщо оновлення знайдено (незалежно від наявності абіратерону) — ставимо прапор
    на весь тиждень, п'ятниця пропускає НІР
  - Прапор скидається кожного понеділка (новий тиждень)
"""

import os
import json
import hashlib
import logging
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup

ELIKY_URL  = "https://eliky.in.ua/medicament/10986"
UNCI_URL   = "https://unci.org.ua/bezoplatni-liky"
STATE_FILE = "state.json"

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID        = os.environ["CHAT_ID"]
CHECK_SOURCE   = os.environ.get("CHECK_SOURCE", "all").strip().lower()

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
                data = {"eliky_ids": data, "unci_ids": [], "unci_update_date": ""}
            # Додаємо нові поля якщо їх немає (сумісність зі старим стейтом)
            data.setdefault("unci_found_this_week", False)
            data.setdefault("unci_week_number", 0)
            return data
    return {
        "eliky_ids": [],
        "unci_ids": [],
        "unci_update_date": "",
        "unci_found_this_week": False,  # чи знайшли оновлення НІР цього тижня
        "unci_week_number": 0,          # номер тижня коли знайшли
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def make_id(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()


def current_week() -> int:
    """Номер поточного тижня в році (ISO)."""
    return datetime.now(timezone.utc).isocalendar()[1]


def is_friday() -> bool:
    return datetime.now(timezone.utc).weekday() == 4  # 0=пн, 4=пт


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
                send_telegram(
                    "💊 <b>АБІРАТЕРОН — ЄЛіки (новий запис)</b>\n\n"
                    f"🏥 <b>Лікарня:</b> {rec['hospital']}\n"
                    f"📍 <b>Область:</b> {rec['region']}\n"
                    f"💊 <b>Кількість:</b> {rec['quantity']}\n"
                    f"📅 <b>Дата оновлення:</b> {rec['date']}\n\n"
                    f"🔗 <a href='{ELIKY_URL}'>Переглянути на ЄЛіки</a>"
                )
            except Exception as e:
                log.error(f"Telegram помилка: {e}")
            known.add(rid)
            new_count += 1
    log.info(f"ЄЛіки: нових записів {new_count}")
    state["eliky_ids"] = list(known)


# ── Джерело 2: НІР (unci.org.ua) ─────────────────────────────────────────────
# Колонки: 0=Назва | 1=Діюча речовина | 2=Приміщення | 3=Наказ |
#          4=Од.вим. | 5=Кіль-ть од. | 6=Термін | 7=Форма випуску | 8=№ партії

ABIRATERONE_KEYWORDS = ["абіратерон", "abiraterone"]


def fetch_unci():
    resp = requests.get(UNCI_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    update_date = ""
    for tag in soup.find_all(string=True):
        text = tag.strip()
        if "оновлено" in text.lower() and len(text) < 80:
            update_date = text
            break

    records = []
    table = soup.find("table")
    if not table:
        log.warning("НІР: таблицю не знайдено")
        return update_date, records

    for row in table.find_all("tr")[1:]:
        cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if not cols:
            continue
        name_lc  = cols[0].lower()
        subst_lc = cols[1].lower() if len(cols) > 1 else ""
        if any(kw in name_lc or kw in subst_lc for kw in ABIRATERONE_KEYWORDS):
            def c(i): return cols[i] if len(cols) > i else ""
            records.append({
                "name":     c(0),
                "subst":    c(1),
                "storage":  c(2),
                "order":    c(3),
                "unit":     c(4),
                "quantity": c(5),
                "expiry":   c(6),
                "form":     c(7),
                "batch":    c(8),
            })

    return update_date, records


def should_skip_unci(state: dict) -> bool:
    """
    Повертає True якщо сьогодні п'ятниця І цього тижня НІР вже оновлювався.
    Також скидає прапор якщо настав новий тиждень.
    """
    week = current_week()

    # Новий тиждень — скидаємо прапор
    if state["unci_week_number"] != week:
        log.info(f"НІР: новий тиждень ({week}), скидаємо прапор оновлення")
        state["unci_found_this_week"] = False
        state["unci_week_number"] = week

    if is_friday() and state["unci_found_this_week"]:
        log.info("НІР: пропускаємо — сьогодні п'ятниця, оновлення вже знайдено в четвер ✓")
        return True

    return False


def check_unci(state: dict) -> None:
    log.info("── Перевірка НІР ──")

    if should_skip_unci(state):
        return

    known = set(state["unci_ids"])
    last_update = state.get("unci_update_date", "")

    try:
        update_date, records = fetch_unci()
    except Exception as e:
        log.error(f"НІР: помилка — {e}")
        return

    log.info(f"НІР: дата оновлення — «{update_date}»")

    # Сайт оновився (дата змінилась) — ставимо прапор незалежно від вмісту
    if update_date and update_date != last_update and last_update:
        log.info("НІР: сайт оновився цього тижня — ставимо прапор, п'ятниця буде пропущена")
        state["unci_found_this_week"] = True
        state["unci_week_number"] = current_week()

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
                send_telegram(
                    "🏥 <b>АБІРАТЕРОН — НІР (новий запис)</b>\n\n"
                    f"💊 <b>Назва:</b> {rec['name']}\n"
                    f"🧪 <b>Діюча речовина:</b> {rec['subst']}\n"
                    f"📦 <b>Приміщення:</b> {rec['storage']}\n"
                    f"🔢 <b>Кількість:</b> {rec['quantity']} {rec['unit']}\n"
                    f"📅 <b>Термін придатності:</b> {rec['expiry']}\n"
                    f"💉 <b>Форма випуску:</b> {rec['form']}\n"
                    f"🔖 <b>Партія:</b> {rec['batch']}\n"
                    f"🗓 <b>Дата оновлення НІР:</b> {update_date}\n\n"
                    f"🔗 <a href='{UNCI_URL}'>Переглянути на сайті НІР</a>"
                )
            except Exception as e:
                log.error(f"Telegram помилка: {e}")
            known.add(rid)
            new_count += 1

    log.info(f"НІР: нових записів {new_count}")
    state["unci_ids"] = list(known)
    state["unci_update_date"] = update_date


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info(f"════ Моніторинг Абіратерону | CHECK_SOURCE={CHECK_SOURCE} ════")
    state = load_state()

    if CHECK_SOURCE in ("eliky", "all"):
        check_eliky(state)

    if CHECK_SOURCE in ("unci", "all"):
        check_unci(state)

    save_state(state)
    log.info("════ Готово. Стан збережено. ════")


if __name__ == "__main__":
    main()

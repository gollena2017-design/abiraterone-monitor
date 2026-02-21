import requests
import hashlib
import os

URL1 = "https://eliky.in.ua/medicament/10986"
URL2 = "https://unci.org.ua/bezoplatni-liky"

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

STATE_FILE = "state.txt"

def fetch(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.text

def normalize(text):
    return hashlib.sha256(text.encode()).hexdigest()

def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=30
    )

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(state)

def main():
    eliky = fetch(URL1)
    unci  = fetch(URL2)

    combined_hash = normalize(eliky + unci)
    old_hash = load_state()

    if combined_hash != old_hash:
        # Перевірка на наявність слова "абіратерон"
        if "абірат" in (eliky+unci).lower() or "abiraterone" in (eliky+unci).lower():
            send(f"🔔 Є оновлення по Абіратерону!\nПеревір сайти:\n<a href='{URL1}'>Eliky</a>\n<a href='{URL2}'>UNCI</a>")

        save_state(combined_hash)
    else:
        print("ℹ️ Оновлень немає")

if __name__ == "__main__":
    main()

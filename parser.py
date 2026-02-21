import requests
from bs4 import BeautifulSoup
import hashlib
import os

URL1 = "https://eliky.in.ua/medicament/10986"
URL2 = "https://unci.org.ua/bezoplatni-liky"

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

def fetch(url):
    r = requests.get(url, timeout=60)
    return r.text

def normalize(text):
    return hashlib.sha256(text.encode()).hexdigest()

def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=30
    )

eliky = fetch(URL1)
unci  = fetch(URL2)

combined = normalize(eliky + unci)

state_file = "state.txt"
old = ""

if os.path.exists(state_file):
    old = open(state_file).read()

if combined != old:
    if "абірат" in (eliky+unci).lower() or "abiraterone" in (eliky+unci).lower():
        send("🔔 Зміни по Абіратерону!\nПеревір:\nhttps://eliky.in.ua/medicament/10986\nhttps://unci.org.ua/bezoplatni-liky")

    open(state_file, "w").write(combined)

import requests
import json
import os
import sys
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
PHPSESSID = os.environ["VKUSVILL_PHPSESSID"]
VV_CARD = os.environ["VKUSVILL_VV_CARD"]

DATA_FILE = "seen_items.json"
ALERT_FLAG_FILE = "session_expired.flag"


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://vkusvill.ru/",
    })
    s.cookies.set("__Host-PHPSESSID", PHPSESSID, domain="vkusvill.ru")
    s.cookies.set("_vv_card", VV_CARD, domain="vkusvill.ru")
    return s


# Фильтр "Скоро исчезнут с полок" (id=284) на /offers/ — это и есть зелёные ценники
GREEN_TAGS_URL = "https://vkusvill.ru/offers/?F%5B212%5D%5B%5D=284&F%5BDEF_3%5D=1&sf4=Y"
PAGE_SIZE = 24
MAX_PAGES = 40


def get_discounted_items(session):
    items = {}
    prev_ids = None

    for page in range(1, MAX_PAGES + 1):
        url = f"{GREEN_TAGS_URL}&PAGEN_1={page}"
        try:
            resp = session.get(url, timeout=15)
        except Exception as e:
            print(f"  Ошибка запроса стр. {page}: {e}")
            break

        print(f"  стр. {page}: {url} -> {resp.status_code}")
        if resp.status_code != 200:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".ProductCard")
        if not cards:
            break

        current_ids = {card.get("data-id") for card in cards}
        if current_ids == prev_ids:
            # За последней реальной страницей сайт повторяет "хвост" — стоп
            break
        prev_ids = current_ids

        for card in cards:
            item_id = card.get("data-id")
            name_el = card.select_one(".js-product-v-tizer__title-text")
            weight_el = card.select_one(".ProductCard__linkWeight")
            price_el = card.select_one(".js-datalayer-catalog-list-price")
            old_price_el = card.select_one(".js-datalayer-catalog-list-price-old")
            link_el = card.select_one(".ProductCard__link")
            notice_el = card.select_one(".ProductCard__notice")

            if not (item_id and name_el and price_el):
                continue

            name = name_el.get_text(strip=True)
            if weight_el:
                name += f", {weight_el.get_text(strip=True)}"

            price = price_el.get_text(strip=True)
            old_price = old_price_el.get_text(strip=True) if old_price_el else ""

            discount = ""
            if old_price.isdigit() and price.isdigit() and int(old_price) > 0:
                discount = str(round((1 - int(price) / int(old_price)) * 100))

            items[item_id] = {
                "name": name,
                "price": price,
                "old_price": old_price,
                "discount": discount,
                "link": "https://vkusvill.ru" + link_el.get("href", "") if link_el else "",
                "notice": notice_el.get_text(strip=True) if notice_el else "",
            }

        if len(cards) < PAGE_SIZE:
            break

    print(f"  Всего найдено зелёных ценников: {len(items)}")
    return items


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
    except Exception as e:
        print(f"Ошибка Telegram: {e}")


def load_seen():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_seen(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def format_item(item_id, info):
    line = f"🟢 <b>{info['name']}</b>\n"
    if info["old_price"]:
        line += f"  {info['old_price']} → <b>{info['price']}</b>"
    else:
        line += f"  <b>{info['price']}</b>"
    if info["discount"]:
        line += f" (-{info['discount']}%)"
    if info["link"]:
        line += f"\n  {info['link']}"
    return line


def check_and_notify():
    session = make_session()
    current = get_discounted_items(session)

    if not current:
        print("Товары не найдены — возможно, изменилась структура сайта или истекла сессия")
        if not os.path.exists(ALERT_FLAG_FILE):
            send_telegram(
                "⚠️ <b>Не удалось получить зелёные ценники ВкусВилл</b>\n"
                "Возможно, истекла сессия — обновите секреты "
                "VKUSVILL_PHPSESSID / VKUSVILL_VV_CARD в GitHub. Либо сайт "
                "изменил разметку и бота нужно поправить."
            )
            with open(ALERT_FLAG_FILE, "w") as f:
                f.write("1")
        else:
            print("Предупреждение уже отправлялось, не спамим.")
        sys.exit(0)

    if os.path.exists(ALERT_FLAG_FILE):
        os.remove(ALERT_FLAG_FILE)
        send_telegram("✅ Снова получаю зелёные ценники, всё восстановилось.")

    seen = load_seen()
    new_items = {k: v for k, v in current.items() if k not in seen}

    if new_items:
        items_list = list(new_items.items())
        for i in range(0, len(items_list), 10):
            chunk = items_list[i:i + 10]
            msg = f"🟢 <b>Зелёные ценники ВкусВилл</b> ({len(new_items)} шт.)\n\n"
            msg += "\n\n".join(format_item(k, v) for k, v in chunk)
            send_telegram(msg)
        print(f"Отправлено новых зелёных ценников: {len(new_items)}")
    else:
        print(f"Новых зелёных ценников нет. Всего сейчас: {len(current)}")

    save_seen(current)


if __name__ == "__main__":
    check_and_notify()

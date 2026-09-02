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


def get_discounted_items(session):
    """Пробует разные способы получить скидки."""
    items = {}

    api_urls = [
        "https://vkusvill.ru/api/v1/products/?filter[discount]=1&limit=100",
        "https://vkusvill.ru/api/v2/products/?filter[discount]=1&limit=100",
        "https://vkusvill.ru/api/v1/goods/?filter[discount]=1&limit=100",
        "https://vkusvill.ru/api/v2/goods/?filter[discount]=1&limit=100",
    ]

    for url in api_urls:
        try:
            resp = session.get(url, timeout=15)
            print(f"  {url} -> {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                products = (data.get("items") or data.get("data") or
                            data.get("products") or data.get("goods") or [])
                if products:
                    for p in products:
                        name = p.get("title") or p.get("name", "")
                        price = str(p.get("price", ""))
                        old_price = str(p.get("old_price") or p.get("price_old") or "")
                        discount = str(p.get("discount") or "")
                        link = "https://vkusvill.ru" + p.get("url", p.get("link", ""))
                        personal = bool(p.get("is_personal") or p.get("personal"))
                        if name:
                            items[name] = {
                                "price": price,
                                "old_price": old_price,
                                "discount": discount,
                                "link": link,
                                "personal": personal,
                            }
                    if items:
                        print(f"  OK: получено {len(items)} товаров")
                        return items
        except Exception as e:
            print(f"  Ошибка: {e}")

    scrape_urls = [
        "https://vkusvill.ru/personal/",
        "https://vkusvill.ru/goods/?filter%5Bdiscount%5D=1",
        "https://vkusvill.ru/sale/",
    ]

    for url in scrape_urls:
        try:
            resp = session.get(url, timeout=15)
            print(f"  Парсинг {url} -> {resp.status_code}")
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")

            selectors = [
                ".ProductCard", ".product-card", "[class*='ProductCard']",
                "[class*='product_card']", ".goods-item", "[class*='GoodsCard']",
            ]
            cards = []
            for sel in selectors:
                cards = soup.select(sel)
                if cards:
                    print(f"  Найдено {len(cards)} карточек по селектору '{sel}'")
                    break

            for card in cards:
                has_discount = card.select_one("[class*='old'], [class*='discount'], [class*='sale']")
                if not has_discount:
                    continue

                name_el = card.select_one("[class*='title'], [class*='name'], h3, h2")
                price_el = card.select_one("[class*='price']:not([class*='old'])")
                old_el = card.select_one("[class*='old']")
                link_el = card.select_one("a[href]")

                if name_el:
                    name = name_el.get_text(strip=True)
                    price = price_el.get_text(strip=True) if price_el else ""
                    old_price = old_el.get_text(strip=True) if old_el else ""
                    link = "https://vkusvill.ru" + link_el["href"] if link_el else ""
                    items[name] = {
                        "price": price,
                        "old_price": old_price,
                        "discount": "",
                        "link": link,
                        "personal": False,
                    }

            if items:
                print(f"  OK: спарсено {len(items)} товаров со скидкой")
                return items

        except Exception as e:
            print(f"  Ошибка парсинга: {e}")

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


def format_item(name, info):
    icon = "⭐️" if info.get("personal") else "🟢"
    line = f"{icon} <b>{name}</b>\n"
    if info["old_price"]:
        line += f"  {info['old_price']} → <b>{info['price']}</b>"
    else:
        line += f"  <b>{info['price']}</b>"
    if info["discount"]:
        line += f" (-{info['discount']}%)"
    if info["link"] and info["link"] != "https://vkusvill.ru":
        line += f"\n  {info['link']}"
    return line


def check_and_notify():
    session = make_session()
    current = get_discounted_items(session)

    if not current:
        print("Товары не найдены — возможно, истёк сеанс или нет скидок")
        if not os.path.exists(ALERT_FLAG_FILE):
            send_telegram(
                "⚠️ <b>Не удалось получить скидки ВкусВилл</b>\n"
                "Похоже, истекла сессия (PHPSESSID). Нужно обновить секреты "
                "VKUSVILL_PHPSESSID / VKUSVILL_VV_CARD в GitHub."
            )
            with open(ALERT_FLAG_FILE, "w") as f:
                f.write("1")
        else:
            print("Предупреждение уже отправлялось, не спамим.")
        sys.exit(0)

    if os.path.exists(ALERT_FLAG_FILE):
        os.remove(ALERT_FLAG_FILE)
        send_telegram("✅ Сессия снова рабочая, проверка скидок восстановлена.")

    seen = load_seen()
    new_items = {k: v for k, v in current.items() if k not in seen}

    if new_items:
        personal = {k: v for k, v in new_items.items() if v.get("personal")}
        regular = {k: v for k, v in new_items.items() if not v.get("personal")}

        if personal:
            msg = f"⭐️ <b>Твои персональные скидки!</b> ({len(personal)} шт.)\n\n"
            msg += "\n\n".join(format_item(k, v) for k, v in list(personal.items())[:10])
            send_telegram(msg)

        for i in range(0, len(regular), 10):
            chunk = list(regular.items())[i:i + 10]
            msg = f"🟢 <b>Зелёные ценники ВкусВилл</b> ({len(regular)} шт.)\n\n"
            msg += "\n\n".join(format_item(k, v) for k, v in chunk)
            send_telegram(msg)

        print(f"Отправлено: {len(personal)} персональных + {len(regular)} магазинных")
    else:
        print(f"Новых скидок нет. Всего со скидкой: {len(current)}")

    save_seen(current)


if __name__ == "__main__":
    check_and_notify()

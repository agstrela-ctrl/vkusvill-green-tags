import requests
import json
import os
import sys
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
VKUSVILL_COOKIE = os.environ["VKUSVILL_COOKIE"]
PROXY_URL = os.environ["PROXY_URL"]

DATA_FILE = "seen_items.json"
ALERT_FLAG_FILE = "session_expired.flag"


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://vkusvill.ru/cart/",
        "X-Requested-With": "XMLHttpRequest",
        # Полная строка cookie из настоящего браузера (включая BXVV_UTK — токен
        # "запомнить меня"). Без него сайт не до конца узнаёт пользователя и
        # отдаёт общую подборку рекомендаций вместо точных зелёных ценников.
        "Cookie": VKUSVILL_COOKIE,
    })
    # ВкусВилл блокирует запросы с IP облачных дата-центров (GitHub Actions),
    # поэтому ходим через российский прокси, чтобы IP совпадал с обычным пользователем
    s.proxies = {"http": PROXY_URL, "https": PROXY_URL}
    return s


# Настоящий источник зелёных ценников — тот же запрос, что подгружает маленький
# слайдер "Зелёные ценники" в корзине (ленивая подгрузка при скролле).
GREEN_LABELS_AJAX = "https://vkusvill.ru/ajax/index_page_lazy_load.php"
PAGE_SIZE = 24
MAX_PAGES = 10


def get_discounted_items(session):
    items = {}

    for page in range(1, MAX_PAGES + 1):
        data = {"code": "cart_green_labels", "version": "default", "is_app": ""}
        if page > 1:
            data["page"] = page
            data["needSplitter"] = "Y"

        try:
            resp = session.post(GREEN_LABELS_AJAX, data=data, timeout=15)
        except Exception as e:
            print(f"  Ошибка запроса стр. {page}: {e}")
            break

        if resp.status_code != 200:
            print(f"  стр. {page}: {resp.status_code}")
            break

        try:
            result = resp.json()
        except ValueError:
            print(f"  Ответ не JSON. Тело: {resp.text[:300]!r}")
            break

        print(
            f"  стр. {page}: success={result.get('success')!r} "
            f"count_prods_avail={result.get('count_prods_avail')!r}"
        )
        if result.get("success") != "Y" or not result.get("html"):
            break

        for chunk in result["html"].split("|#||"):
            soup = BeautifulSoup(chunk, "html.parser")
            for card in soup.select(".ProductCard"):
                item_id = card.get("data-id")
                name_el = card.select_one(".js-product-v-tizer__title-text")
                price_el = card.select_one(".js-datalayer-catalog-list-price")
                old_price_el = card.select_one(".js-datalayer-catalog-list-price-old")
                link_el = card.select_one(".ProductCard__link")

                if not (item_id and name_el and price_el):
                    continue

                price = price_el.get_text(strip=True)
                old_price = old_price_el.get_text(strip=True) if old_price_el else ""

                discount = ""
                if old_price.isdigit() and price.isdigit() and int(old_price) > 0:
                    discount = str(round((1 - int(price) / int(old_price)) * 100))

                items[item_id] = {
                    "name": name_el.get_text(strip=True),
                    "price": price,
                    "old_price": old_price,
                    "discount": discount,
                    "link": "https://vkusvill.ru" + link_el.get("href", "") if link_el else "",
                }

        count_pages = result.get("count_pages", 1)
        if page >= count_pages:
            break

    print(f"  Всего найдено зелёных ценников: {len(items)}")
    return items


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        if resp.status_code != 200:
            print(f"Telegram ответил {resp.status_code}: {resp.text[:300]}")
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
                "Возможно, истекла сессия — обновите секрет VKUSVILL_COOKIE "
                "в GitHub. Либо сайт изменил разметку и бота нужно поправить."
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

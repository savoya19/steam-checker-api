import requests
import re
import os
import time
from urllib.parse import urlparse, quote
from dotenv import load_dotenv

# --------- НАСТРОЙКИ ---------
# СЮДА ВСТАВЛЯЕШЬ ССЫЛКУ НА ПРОФИЛЬ (как раньше, когда инвентарь уже выводился)
PROFILE_URL = ("https://steamcommunity.com/profiles/76561198390944700")

APP_ID = 730
CONTEXT_ID = 2
CURRENCY = 5                # 5 = RUB
REQUEST_TIMEOUT = 8         # таймаут для запросов к Steam
SLEEP_BETWEEN_PRICE_REQ = 0.1  # пауза между запросами цен (можно 0.0 если что)

load_dotenv("kod.env")      # грузим куки, если есть


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

def get_steamid64_from_profile_url(profile_url: str, cookies: dict | None = None) -> str:
    """
    /profiles/7656... → сразу берём из URL
    /id/Ник → один запрос к странице профиля, парсим "steamid":"..."
    """
    print("STEP 1: определяем steamid64")
    parsed = urlparse(profile_url)
    path = parsed.path

    # /profiles/7656...
    m = re.match(r"^/profiles/(\d+)/?$", path)
    if m:
        steamid = m.group(1)
        print("  steamid из URL (profiles):", steamid)
        return steamid

    # /id/NAME → тянем HTML
    print("  URL вида /id/... → делаем запрос к странице профиля")
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(profile_url, headers=headers, cookies=cookies or None, timeout=REQUEST_TIMEOUT)
    print("  [PROFILE] STATUS:", resp.status_code)

    resp.raise_for_status()
    html = resp.text

    m = re.search(r'"steamid"\s*:\s*"(\d+)"', html)
    if not m:
        raise RuntimeError("Не удалось извлечь steamid64 из HTML профиля")

    steamid = m.group(1)
    print("  steamid из HTML:", steamid)
    return steamid


def build_inventory_url(profile_url: str,
                        app_id: int = APP_ID,
                        context_id: int = CONTEXT_ID,
                        cookies: dict | None = None) -> str:
    """
    Новый эндпоинт:
    https://steamcommunity.com/inventory/STEAMID/APP/CONTEXT?l=english&count=2000
    """
    print("STEP 2: строим URL инвентаря")
    steamid64 = get_steamid64_from_profile_url(profile_url, cookies=cookies)
    url = (
        f"https://steamcommunity.com/inventory/{steamid64}/{app_id}/{context_id}"
        f"?l=english&count=2000"
    )
    print("  inventory URL:", url)
    return url


# ===== ИНВЕНТАРЬ =====

def get_inventory(profile_url: str, cookies: dict | None = None,
                  app_id: int = APP_ID, context_id: int = CONTEXT_ID):
    print("STEP 3: запрашиваем инвентарь")

    url = build_inventory_url(profile_url, app_id, context_id, cookies=cookies)

    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(url, headers=headers, cookies=cookies or None, timeout=REQUEST_TIMEOUT)
    print("  [INV] STATUS:", resp.status_code)

    if resp.status_code != 200:
        print("  [INV] Тело ответа (300 символов):")
        print(resp.text[:300])
        raise RuntimeError(f"[INV] Steam вернул HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        print("  [INV] Не JSON, сырое тело (300 символов):")
        print(resp.text[:300])
        raise RuntimeError("[INV] Ответ Steam не является JSON")

    if not data.get("success"):
        print("  [INV] success != 1, полный JSON:")
        print(data)
        raise RuntimeError(f"[INV] Steam error: {data}")

    assets = data.get("assets", [])
    descriptions = data.get("descriptions", [])

    print(f"  [INV] assets: {len(assets)}, descriptions: {len(descriptions)}")

    # индекс описаний по classid/instanceid
    desc_index: dict[str, dict] = {}
    for d in descriptions:
        classid = d.get("classid")
        instanceid = d.get("instanceid", "0")
        if not classid:
            continue
        key = f"{classid}_{instanceid}"
        desc_index[key] = d
        if classid not in desc_index:
            desc_index[classid] = d

    items = []
    for a in assets:
        assetid = a.get("assetid")
        classid = a.get("classid")
        instanceid = a.get("instanceid", "0")

        if not classid:
            continue

        key = f"{classid}_{instanceid}"
        desc = desc_index.get(key) or desc_index.get(classid, {})

        items.append({
            "assetid": assetid,
            "classid": classid,
            "instanceid": instanceid,
            "name": desc.get("market_hash_name") or desc.get("name"),
            "type": desc.get("type"),
        })

    print(f"STEP 4: итоговых предметов: {len(items)}")
    return items


# ===== ЦЕНЫ С МАРКЕТА =====

def get_item_price(app_id: int, market_hash_name: str, currency: int = CURRENCY, retries: int = 3):
    url = (
        "https://steamcommunity.com/market/priceoverview/"
        f"?appid={app_id}&currency={currency}&market_hash_name={quote(market_hash_name)}"
    )
    headers = {"User-Agent": "Mozilla/5.0"}

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.Timeout:
            print(f"[PRICE] TIMEOUT {attempt}/{retries} для: {market_hash_name}")
        except requests.exceptions.RequestException as e:
            print(f"[PRICE] REQ ERROR {attempt}/{retries} для {market_hash_name}: {e}")
        else:
            if resp.status_code != 200:
                print(f"[PRICE] HTTP {resp.status_code} {attempt}/{retries} для {market_hash_name}")
            else:
                try:
                    data = resp.json()
                except ValueError:
                    print(f"[PRICE] BAD JSON {attempt}/{retries} для {market_hash_name}: {resp.text[:200]}")
                else:
                    if data.get("success"):
                        # УСПЕХ
                        return {
                            "lowest_price": data.get("lowest_price"),
                            "median_price": data.get("median_price"),
                            "volume": data.get("volume"),
                        }
                    else:
                        print(f"[PRICE] NO PRICE {attempt}/{retries} для {market_hash_name}: {data}")

        # если не удалось — подождать и попробовать ещё
        if attempt < retries:
            time.sleep(0.5 * attempt)

    # все попытки провалились
    print(f"[PRICE] НЕ УДАЛОСЬ получить цену для: {market_hash_name}")
    return None


def add_prices_to_items(items, app_id: int = APP_ID, currency: int = CURRENCY):
    print("[PRICE] Начинаем подтягивать цены...")

    all_names = [item.get("name") for item in items if item.get("name")]
    unique_names = list(dict.fromkeys(all_names))  # сохраняем порядок
    print(f"[PRICE] Всего предметов: {len(items)}, уникальных имён: {len(unique_names)}")

    price_cache = {}
    success_count = 0

    for i, name in enumerate(unique_names, start=1):
        if SLEEP_BETWEEN_PRICE_REQ > 0:
            time.sleep(SLEEP_BETWEEN_PRICE_REQ)

        price = get_item_price(app_id, name, currency)
        price_cache[name] = price
        if price is not None:
            success_count += 1

        if i % 10 == 0 or i == len(unique_names):
            print(f"[PRICE] {i}/{len(unique_names)} обработано, успешных: {success_count}")

    # прикрепляем цену к каждому предмету
    for item in items:
        name = item.get("name")
        if not name:
            continue
        item["price"] = price_cache.get(name)

    print(f"[PRICE] Цены добавлены. Успешно для {success_count} из {len(unique_names)} уникальных предметов.")
    return items


def parse_price_to_float(raw: str | None) -> float | None:
    if not raw:
        return None

    clean = re.sub(r"[^0-9,\.]", "", raw)
    clean = clean.replace(",", ".")
    if clean.count(".") > 1:
        parts = clean.split(".")
        clean = parts[0] + "." + "".join(parts[1:])

    if not clean:
        return None

    try:
        return float(clean)
    except ValueError:
        return None


# ===== MAIN =====

if __name__ == "__main__":
    print("STEP 0: main стартует")

    cookies = {
        "sessionid": os.getenv("SESSIONID"),
        "steamLoginSecure": os.getenv("STEAMLOGINSECURE"),
    }
    cookies = {k: v for k, v in cookies.items() if v}  # убираем пустые
    print("STEP 0.1: cookies загружены:", cookies)

    try:
        print("STEP 0.2: профиль:", PROFILE_URL)

        # 1) инвентарь (то, что уже работало)
        items = get_inventory(PROFILE_URL, cookies=cookies)

        # 2) подгружаем цены
        items = add_prices_to_items(items, app_id=APP_ID, currency=CURRENCY)

        # 3) считаем сумму
        total = 0.0
        for item in items:
            price_info = item.get("price") or {}
            value = parse_price_to_float(price_info.get("lowest_price"))
            if value is not None:
                total += value

        print("\n=== ПРЕДМЕТЫ ===")
        for item in items:
            price = item.get("price") or {}
            print(
                item["name"],
                "|", item["type"],
                "| lowest:", price.get("lowest_price"),
                "| median:", price.get("median_price"),
            )

        print("\nИтого по инвентарю:", total, "RUB")

    except Exception as e:
        print("\n[ERROR] Ошибка во время выполнения:", repr(e))

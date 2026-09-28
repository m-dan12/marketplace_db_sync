"""
Ночная выгрузка аналитики WB (3 кабинета) + Ozon (3 кабинета) + Selsup
в Google Drive, папка "!База Данных для ИИ / Выгрузка авто".
Рассчитан на запуск Планировщиком Windows в 03:00.
"""
import io
import mimetypes
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from openpyxl import Workbook
from openpyxl.styles import PatternFill

import yandex_market as ym

load_dotenv(r"C:\Users\Proftex1\MarketplaceGateway\.env")

SECRETS_DIR = r"C:\Users\Proftex1\MarketplaceGateway\.secrets"
OAUTH_CLIENT_FILE = os.path.join(SECRETS_DIR, "oauth_client.json")
OAUTH_TOKEN_FILE = os.path.join(SECRETS_DIR, "drive_token.json")
SCOPES = ["https://www.googleapis.com/auth/drive"]
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "nightly_export.log")

TODAY = datetime.now(timezone.utc).date()
YESTERDAY = TODAY - timedelta(days=1)
DATE_FROM_30D = TODAY - timedelta(days=30)
DATE_FROM_7D = TODAY - timedelta(days=7)
DATE_FROM_60D = TODAY - timedelta(days=60)
DATE_PREV30_END = TODAY - timedelta(days=31)  # предыдущий 30-дневный период: [60д назад .. 31д назад]

FOLDERS = {
    "wb": {
        "sk": "1tPgKcLB6XIvOqgWx2f7UoQT4GImDIvlb",
        "mg": "1zrr4-fuNl6ZvFp-POomZVjFbeGq-8xzX",
        "tl": "1NIL1GyYcpNp7s1vTPQyJUgpKwueddPDF",
    },
    "ozon": {
        "sk": "1qgjsgRybwCR8USflKGhZXDo4tZoBEMTf",
        "tl": "1avXyQXe9Xgm4C07xpqdxIWecnbJxFIwy",
        "mg": "1y4x1koJOy5Z0IJv19h1umyoLWtSsD_R-",
    },
    "selsup": "1muuHAoSKaBb6Id6iSL-HenGLeC6ni9ig",
    "ym": {
        "root": "1o7MuKlZ8mxx1OOOcJQGlnk9ezLW7Tbh4",
        "sk": "1Z6PDijfP93IaT0z71ewriMHAAbg1eu65",
        "mg": "12v94YvUjT5Ei5aYzXhBiFf10THUsChSA",
        "tl": "1AvB7Xv9tA_QoPXH45a0OSLeZkwDhGVUa",
    },
}

WB_CABINETS = [
    ("sk", "Сказка", os.environ["WB_API_KEY"]),
    ("mg", "Milky Garden", os.environ["WB_API_KEY_MILKY"]),
    ("tl", "Timeless", os.environ["WB_API_KEY_TIMELESS"]),
]

OZON_CABINETS = [
    ("sk", "Профтекс", os.environ["OZON_CLIENT_ID"], os.environ["OZON_API_KEY"]),
    ("tl", "Timeless", os.environ["OZON_CLIENT_ID_TIMELESS"], os.environ["OZON_API_KEY_TIMELESS"]),
    ("mg", "Milky Garden", os.environ["OZON_CLIENT_ID_MILKY"], os.environ["OZON_API_KEY_MILKY"]),
]

YM_CABINETS = [
    ("sk", "Сказка", os.environ["YM_API_KEY_SKAZKA"], 68759571,
     [(62467504, "FBY"), (70998236, "FBS"), (76309083, "FBS Express")]),
    ("mg", "Milky Garden", os.environ["YM_API_KEY_MILKY"], 965585,
     [(21941050, "FBY"), (23554006, "FBS")]),
    ("tl", "Timeless", os.environ["YM_API_KEY_TIMELESS"], 216451065,
     [(148827642, "FBS"), (149211664, "FBY")]),
]

SELSUP_TOKEN = os.environ["SELSUP_API_TOKEN"]
SELSUP_WAREHOUSES = [
    (10001, "FBS"), (10020, "Kvant"),
    (10023, "Технический"), (10016, "Фурнитура Профтекс"), (10018, "Фурнитура ИП"),
    (10019, "Возвраты"), (10017, "Временное хранение"),
]

STATISTICS_BASE = "https://statistics-api.wildberries.ru"
PRICES_BASE = "https://discounts-prices-api.wildberries.ru"
COMMON_BASE = "https://common-api.wildberries.ru"
SELSUP_BASE = "https://api.selsup.ru"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_drive_service():
    creds = None
    if os.path.exists(OAUTH_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(OAUTH_TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(OAUTH_TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        else:
            raise SystemExit(f"OAuth token invalid/missing: {OAUTH_TOKEN_FILE}. Re-run the interactive auth flow.")
    return build("drive", "v3", credentials=creds)


_archive_folder_cache = {}


def get_or_create_archive_folder(service, parent_folder_id):
    """Подпапка "Архив" внутри кабинета. Кэшируется на время запуска."""
    if parent_folder_id in _archive_folder_cache:
        return _archive_folder_cache[parent_folder_id]
    q = (
        f"'{parent_folder_id}' in parents and name = 'Архив' and trashed = false "
        f"and mimeType = 'application/vnd.google-apps.folder'"
    )
    existing = service.files().list(q=q, fields="files(id)").execute().get("files", [])
    if existing:
        archive_id = existing[0]["id"]
    else:
        meta = {
            "name": "Архив", "parents": [parent_folder_id],
            "mimeType": "application/vnd.google-apps.folder",
        }
        archive_id = service.files().create(body=meta, fields="id").execute()["id"]
    _archive_folder_cache[parent_folder_id] = archive_id
    return archive_id


def upload_bytes(service, folder_id, filename, data: bytes, archive=True):
    """archive=True: перед перезаписью существующего файла копирует его
    предыдущую версию в подпапку "Архив" этого кабинета с датой во имени,
    чтобы ежедневные обновления (остатки, заказы и т.п.) не терялись.
    archive=False - для файлов, которые и так именуются датой (Selsup)."""
    mime_type, _ = mimetypes.guess_type(filename)
    mime_type = mime_type or "application/octet-stream"
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)

    q = f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
    existing = service.files().list(q=q, fields="files(id)").execute().get("files", [])

    if existing:
        if archive:
            try:
                archive_folder_id = get_or_create_archive_folder(service, folder_id)
                base, ext = os.path.splitext(filename)
                archive_name = f"{base} {YESTERDAY.strftime('%d.%m.%Y')}{ext}"
                service.files().copy(
                    fileId=existing[0]["id"],
                    body={"name": archive_name, "parents": [archive_folder_id]},
                ).execute()
            except Exception:
                log(f"  ERROR archiving {filename}:\n{traceback.format_exc()}")
        service.files().update(fileId=existing[0]["id"], media_body=media).execute()
        action = "updated"
    else:
        service.files().create(body={"name": filename, "parents": [folder_id]}, media_body=media).execute()
        action = "created"
    log(f"  {action}: {filename}")


def workbook_to_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


MAX_RATE_LIMIT_WAIT = 120  # секунд - WB иногда шлёт абсурдный x-ratelimit-reset (часы/дни)


def get_with_retry(url, headers, params=None, max_retries=5):
    for attempt in range(max_retries):
        resp = httpx.get(url, headers=headers, params=params, timeout=60.0)
        if resp.status_code == 429:
            reset = min(int(resp.headers.get("x-ratelimit-reset", 90)), MAX_RATE_LIMIT_WAIT)
            log(f"    rate limited on {url}, waiting {reset + 5}s...")
            time.sleep(reset + 5)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def get_with_retry_post(url, headers, json_body, max_retries=5):
    for attempt in range(max_retries):
        try:
            resp = httpx.post(url, headers=headers, json=json_body, timeout=60.0)
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
            log(f"    network error on {url}: {e}, retry in 10s...")
            time.sleep(10)
            continue
        if resp.status_code == 429:
            reset = min(int(resp.headers.get("x-ratelimit-reset", 90)), MAX_RATE_LIMIT_WAIT)
            log(f"    rate limited on {url}, waiting {reset + 5}s...")
            time.sleep(reset + 5)
            continue
        if resp.status_code >= 500:
            log(f"    server error {resp.status_code} on {url}, retry in 15s...")
            time.sleep(15)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def wb_orders_xlsx(headers, api_date_from, date_from=None, date_to=None):
    """api_date_from: WB API lastChangeDate lower bound (should be earliest of any window used).
    date_from/date_to: optional client-side filter on order creation date (inclusive)."""
    resp = get_with_retry(
        f"{STATISTICS_BASE}/api/v1/supplier/orders", headers, {"dateFrom": api_date_from.isoformat(), "flag": 0}
    )
    orders = resp.json()
    if date_from is not None:
        orders = [o for o in orders if o.get("date", "")[:10] >= date_from.isoformat()]
    if date_to is not None:
        orders = [o for o in orders if o.get("date", "")[:10] <= date_to.isoformat()]
    wb = Workbook()
    ws = wb.active
    ws.title = "Заказы"
    cols = [
        "date", "lastChangeDate", "warehouseName", "regionName", "supplierArticle", "nmId",
        "barcode", "subject", "brand", "techSize", "totalPrice", "discountPercent",
        "finishedPrice", "priceWithDisc", "isCancel", "gNumber", "srid",
    ]
    ws.append(cols)
    for o in orders:
        ws.append([o.get(c, "") for c in cols])
    return workbook_to_bytes(wb), len(orders)


def wb_sales_xlsx(headers, date_from):
    resp = get_with_retry(
        f"{STATISTICS_BASE}/api/v1/supplier/sales", headers, {"dateFrom": date_from.isoformat(), "flag": 0}
    )
    sales = resp.json()
    wb = Workbook()
    ws = wb.active
    ws.title = "Продажи"
    cols = [
        "date", "lastChangeDate", "warehouseName", "regionName", "supplierArticle", "nmId",
        "barcode", "subject", "brand", "techSize", "totalPrice", "discountPercent",
        "spp", "forPay", "finishedPrice", "priceWithDisc", "saleID", "orderType", "gNumber", "srid",
    ]
    ws.append(cols)
    for s in sales:
        ws.append([s.get(c, "") for c in cols])
    return workbook_to_bytes(wb), len(sales)


def wb_vendor_code_map(headers):
    """nmID -> vendorCode (артикул продавца) через Content API карточек товаров.
    ВАЖНО: API отдаёт максимум 100 карточек за страницу вне зависимости от
    запрошенного limit — останавливаемся только когда страница пустая."""
    mapping = {}
    page_limit = 100
    cursor = {"limit": page_limit}
    seen_nmids = set()
    for _ in range(10000):
        resp = get_with_retry_post(
            "https://content-api.wildberries.ru/content/v2/get/cards/list", headers,
            {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
        )
        data = resp.json()
        cards = data.get("cards", [])
        if not cards:
            break
        for c in cards:
            mapping[c.get("nmID")] = c.get("vendorCode")
        new_cursor = data.get("cursor", {})
        next_nmid = new_cursor.get("nmID")
        if not next_nmid or next_nmid in seen_nmids:
            break
        seen_nmids.add(next_nmid)
        cursor = {"limit": page_limit, "updatedAt": new_cursor["updatedAt"], "nmID": next_nmid}
        time.sleep(0.2)
    return mapping


def wb_stocks_xlsx(headers):
    """Отчёт "Остатки на складах" (v1/warehouse_remains): создать -> статус -> скачать.
    Разбивка по складам в отдельных колонках + артикул продавца (джойн по nmId)."""
    resp = get_with_retry(
        "https://seller-analytics-api.wildberries.ru/api/v1/warehouse_remains", headers,
        {"groupByNm": "true", "groupByBarcode": "true", "groupBySize": "true"},
    )
    task_id = resp.json()["data"]["taskId"]

    for _ in range(60):
        time.sleep(4)
        r = httpx.get(
            f"https://seller-analytics-api.wildberries.ru/api/v1/warehouse_remains/tasks/{task_id}/status",
            headers=headers, timeout=30,
        )
        if r.status_code == 429:
            time.sleep(30)
            continue
        r.raise_for_status()
        if r.json().get("data", {}).get("status") == "done":
            break
    else:
        raise TimeoutError("warehouse_remains report generation timed out")

    for attempt in range(6):
        r = httpx.get(
            f"https://seller-analytics-api.wildberries.ru/api/v1/warehouse_remains/tasks/{task_id}/download",
            headers=headers, timeout=60,
        )
        if r.status_code == 429:
            time.sleep(30)
            continue
        r.raise_for_status()
        break
    rows = r.json()

    vendor_codes = wb_vendor_code_map(headers)

    all_wh_names = []
    for row in rows:
        for w in row.get("warehouses", []):
            if w["warehouseName"] not in all_wh_names:
                all_wh_names.append(w["warehouseName"])

    wb = Workbook()
    ws = wb.active
    ws.title = "Остатки"
    ws.append(["nmId", "vendorCode", "barcode", "techSize", "volume"] + all_wh_names)
    for row in rows:
        wh_map = {w["warehouseName"]: w["quantity"] for w in row.get("warehouses", [])}
        ws.append([
            row.get("nmId"), vendor_codes.get(row.get("nmId"), ""), row.get("barcode"),
            row.get("techSize"), row.get("volume"),
        ] + [wh_map.get(w, 0) for w in all_wh_names])
    return workbook_to_bytes(wb), len(rows)


def wb_prices_xlsx(headers):
    all_items = []
    offset = 0
    limit = 1000
    while True:
        resp = get_with_retry(f"{PRICES_BASE}/api/v2/list/goods/filter", headers, {"limit": limit, "offset": offset})
        batch = resp.json().get("data", {}).get("listGoods", [])
        if not batch:
            break
        all_items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(90)
    wb = Workbook()
    ws = wb.active
    ws.title = "Цены и скидки"
    ws.append(["nmID", "vendorCode", "techSizeName", "sizeID", "price", "discount", "discountedPrice"])
    for item in all_items:
        for s in item.get("sizes", []):
            ws.append([
                item.get("nmID"), item.get("vendorCode"), s.get("techSizeName"), s.get("sizeID"),
                s.get("price"), item.get("discount"), s.get("discountedPrice"),
            ])
    return workbook_to_bytes(wb), len(all_items)


def get_with_retry_patient(url, headers, params=None, max_retries=10, wait_s=75):
    """Как get_with_retry, но с фиксированной длинной паузой и большим числом
    попыток - для эндпоинтов с очень узким лимитом (финансовый отчёт reportDetailByPeriod,
    у него лимит по факту около 1 запроса/минуту)."""
    for attempt in range(max_retries):
        resp = httpx.get(url, headers=headers, params=params, timeout=60.0)
        if resp.status_code == 429:
            log(f"    rate limited (patient) on {url}, попытка {attempt+1}/{max_retries}, waiting {wait_s}s...")
            time.sleep(wait_s)
            continue
        if resp.status_code >= 500:
            time.sleep(10)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def wb_finance_xlsx(headers, date_from):
    all_rows = []
    rrdid = 0
    while True:
        resp = get_with_retry_patient(
            f"{STATISTICS_BASE}/api/v5/supplier/reportDetailByPeriod",
            headers,
            {"dateFrom": date_from.isoformat(), "dateTo": TODAY.isoformat(), "rrdid": rrdid, "limit": 100000},
        )
        batch = resp.json()
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < 100000:
            break
        rrdid = batch[-1]["rrd_id"]
        time.sleep(1)
    wb = Workbook()
    ws = wb.active
    ws.title = "Финансовый отчет"
    cols = [
        "rr_dt", "sa_name", "nm_id", "subject_name", "quantity", "retail_price",
        "retail_amount", "ppvz_for_pay", "delivery_rub", "penalty", "storage_fee",
        "acceptance", "commission_percent", "sale_dt", "doc_type_name",
    ]
    ws.append(cols)
    for r in all_rows:
        ws.append([r.get(c, "") for c in cols])
    return workbook_to_bytes(wb), len(all_rows)


def wb_news_xlsx(headers, date_from):
    resp = get_with_retry(f"{COMMON_BASE}/api/communications/v2/news", headers, {"from": date_from.strftime("%Y-%m-%d")})
    items = resp.json().get("data", [])
    wb = Workbook()
    ws = wb.active
    ws.title = "Новости"
    ws.append(["id", "date", "header", "content"])
    for it in items:
        ws.append([it.get("id"), it.get("date"), it.get("header"), it.get("content")])
    return workbook_to_bytes(wb), len(items)


def ozon_stocks_xlsx(client_id, api_key):
    headers = {"Client-Id": client_id, "Api-Key": api_key}
    items = []
    cursor = ""
    while True:
        body = {"filter": {"visibility": "ALL"}, "limit": 1000}
        if cursor:
            body["cursor"] = cursor
        r = httpx.post(
            "https://api-seller.ozon.ru/v4/product/info/stocks", headers=headers, json=body, timeout=60
        )
        r.raise_for_status()
        data = r.json()
        batch = data.get("items", [])
        items.extend(batch)
        cursor = data.get("cursor", "")
        if not cursor or not batch:
            break
    wb = Workbook()
    ws = wb.active
    ws.title = "Остатки"
    ws.append(["offer_id", "product_id", "type", "present", "reserved", "sku"])
    for item in items:
        for s in item.get("stocks", []):
            ws.append([item.get("offer_id"), item.get("product_id"), s.get("type"), s.get("present"), s.get("reserved"), s.get("sku")])

    ws2 = wb.create_sheet("Остатки по складам FBO")
    ws2.append(["offer_id", "sku", "товар", "склад", "доступно", "резерв", "в пути"])
    wh_rows = 0
    offset = 0
    while True:
        body = {"limit": 1000, "offset": offset, "warehouse_type": "ALL"}
        r = httpx.post("https://api-seller.ozon.ru/v2/analytics/stock_on_warehouses", headers=headers, json=body, timeout=60)
        r.raise_for_status()
        batch = r.json().get("result", {}).get("rows", [])
        if not batch:
            break
        for row in batch:
            ws2.append([
                row.get("item_code"), row.get("sku"), row.get("item_name"),
                row.get("warehouse_name"), row.get("free_to_sell_amount"),
                row.get("reserved_amount"), row.get("promised_amount"),
            ])
            wh_rows += 1
        if len(batch) < 1000:
            break
        offset += 1000

    return workbook_to_bytes(wb), len(items) + wh_rows


def ozon_prices_xlsx(client_id, api_key):
    headers = {"Client-Id": client_id, "Api-Key": api_key}
    items = []
    cursor = ""
    while True:
        body = {"filter": {"visibility": "ALL"}, "limit": 1000, "cursor": cursor}
        r = httpx.post("https://api-seller.ozon.ru/v5/product/info/prices", headers=headers, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        batch = data.get("items", [])
        items.extend(batch)
        cursor = data.get("cursor", "")
        if not cursor or not batch:
            break
    wb = Workbook()
    ws = wb.active
    ws.title = "Цены"
    ws.append(["offer_id", "product_id", "price", "old_price", "min_price", "marketing_seller_price"])
    for item in items:
        price = item.get("price", {})
        ws.append([
            item.get("offer_id"), item.get("product_id"), price.get("price"),
            price.get("old_price"), price.get("min_price"), price.get("marketing_seller_price"),
        ])
    return workbook_to_bytes(wb), len(items)


def ozon_postings_xlsx(client_id, api_key, date_from, date_to=None):
    if date_to is None:
        date_to = TODAY
    headers = {"Client-Id": client_id, "Api-Key": api_key}
    all_rows = []
    for endpoint, key_name in (("v3/posting/fbs/list", "postings"), ("v2/posting/fbo/list", "result")):
        offset = 0
        while True:
            if "fbs" in endpoint:
                body = {
                    "dir": "ASC", "filter": {"since": f"{date_from.isoformat()}T00:00:00Z", "to": f"{date_to.isoformat()}T23:59:59Z"},
                    "limit": 1000, "offset": offset, "with": {"analytics_data": True},
                }
            else:
                body = {
                    "dir": "ASC", "filter": {"since": f"{date_from.isoformat()}T00:00:00Z", "to": f"{date_to.isoformat()}T23:59:59Z"},
                    "limit": 1000, "offset": offset, "with": {"analytics_data": True},
                }
            r = httpx.post(f"https://api-seller.ozon.ru/{endpoint}", headers=headers, json=body, timeout=60)
            r.raise_for_status()
            data = r.json()
            batch = data.get("result", data).get(key_name, data.get("result", [])) if isinstance(data.get("result"), dict) else data.get("result", [])
            if not batch:
                break
            for row in batch:
                row["_source"] = key_name
            all_rows.extend(batch)
            if len(batch) < 1000:
                break
            offset += 1000
    wb = Workbook()
    ws = wb.active
    ws.title = "Заказы"
    ws.append([
        "posting_number", "status", "order_date", "in_process_at", "source",
        "склад_отгрузки", "город_доставки", "регион",
        "offer_id", "sku", "product_name", "quantity", "price", "currency",
    ])
    total = 0
    for r in all_rows:
        ad = r.get("analytics_data") or {}
        base = [
            r.get("posting_number"), r.get("status"),
            r.get("order_date") or r.get("created_at") or r.get("in_process_at"),
            r.get("in_process_at"), r.get("_source"),
            ad.get("warehouse_name") or ad.get("warehouse"), ad.get("city"), ad.get("region"),
        ]
        products = r.get("products") or []
        if not products:
            ws.append(base + [None, None, None, None, None, None])
            total += 1
            continue
        for p in products:
            ws.append(base + [
                p.get("offer_id"), p.get("sku"), p.get("name"),
                p.get("quantity"), p.get("price"), p.get("currency_code"),
            ])
            total += 1
    return workbook_to_bytes(wb), total


OZON_NEWS_CHAT_TYPES = ["SELLER_API_NOTIFICATIONS", "SELLER_API_UPDATES"]


def ozon_news_xlsx(client_id, api_key):
    """Новости/уведомления Ozon - у Ozon нет отдельного news-эндпоинта, они идут
    через системные чаты SELLER_API_NOTIFICATIONS (общие новости, гайды) и
    SELLER_API_UPDATES (технические изменения API)."""
    headers = {"Client-Id": client_id, "Api-Key": api_key}
    wb = Workbook()
    ws = wb.active
    ws.title = "Новости"
    ws.append(["chat_type", "chat_id", "created_at", "user", "text"])
    total = 0

    for chat_type in OZON_NEWS_CHAT_TYPES:
        offset = 0
        chat_ids = []
        for _ in range(50):  # защита от зацикливания
            body = {"limit": 100, "offset": offset, "filter": {"chat_status": "All"}}
            r = get_with_retry_post("https://api-seller.ozon.ru/v3/chat/list", headers, body)
            data = r.json()
            chats = data.get("chats", [])
            for c in chats:
                if (c.get("chat", {}) or {}).get("chat_type") == chat_type:
                    chat_ids.append((c.get("chat", {}) or {}).get("chat_id"))
            if len(chats) < 100:
                break
            offset += 100
            time.sleep(0.5)

        for chat_id in chat_ids:
            if not chat_id:
                continue
            from_message_id = None
            seen_message_ids = set()
            for _ in range(200):  # защита от зацикливания на одной странице
                body = {"chat_id": chat_id, "limit": 100}
                if from_message_id:
                    body["from_message_id"] = from_message_id
                r = get_with_retry_post("https://api-seller.ozon.ru/v3/chat/history", headers, body)
                data = r.json()
                messages = data.get("messages", [])
                if not messages:
                    break
                new_ids = [m.get("message_id") for m in messages]
                if all(mid in seen_message_ids for mid in new_ids):
                    break  # страница не сдвигается - выходим, а не крутимся вечно
                seen_message_ids.update(new_ids)
                for m in messages:
                    text_parts = m.get("data") or []
                    text = " ".join(str(t) for t in text_parts) if isinstance(text_parts, list) else str(text_parts)
                    ws.append([chat_type, chat_id, m.get("created_at"), m.get("user", {}).get("type"), text])
                    total += 1
                if not data.get("has_next"):
                    break
                from_message_id = messages[-1].get("message_id")
                time.sleep(0.5)

    return workbook_to_bytes(wb), total


_ozon_accrual_types_cache = None


def _ozon_accrual_types(headers):
    """Справочник id -> человекочитаемое название типа начисления. Кэшируется в
    процессе (общий для всех кабинетов, но заголовки одинаковы по сути справочника)."""
    global _ozon_accrual_types_cache
    if _ozon_accrual_types_cache is not None:
        return _ozon_accrual_types_cache
    r = get_with_retry_post("https://api-seller.ozon.ru/v1/finance/accrual/types", headers, {})
    types = {t["id"]: t.get("description") or t.get("name") for t in r.json().get("accrual_types", [])}
    _ozon_accrual_types_cache = types
    return types


def _ozon_posting_numbers(client_id, api_key, date_from, date_to):
    """Собирает все posting_number за период (FBS+FBO) - нужны для запроса начислений,
    т.к. /v1/finance/accrual/postings принимает только явный список номеров (1-200 за раз)."""
    headers = {"Client-Id": client_id, "Api-Key": api_key}
    numbers = []
    for endpoint, key_name in (("v3/posting/fbs/list", "postings"), ("v2/posting/fbo/list", "result")):
        offset = 0
        while True:
            body = {
                "dir": "ASC",
                "filter": {"since": f"{date_from.isoformat()}T00:00:00Z", "to": f"{date_to.isoformat()}T23:59:59Z"},
                "limit": 1000, "offset": offset, "with": {"analytics_data": False},
            }
            r = get_with_retry_post(f"https://api-seller.ozon.ru/{endpoint}", headers, body)
            data = r.json()
            batch = data.get("result", data).get(key_name, data.get("result", [])) if isinstance(data.get("result"), dict) else data.get("result", [])
            if not batch:
                break
            numbers.extend(p.get("posting_number") for p in batch if p.get("posting_number"))
            if len(batch) < 1000:
                break
            offset += 1000
    return list(dict.fromkeys(numbers))  # без дублей, порядок не важен


def ozon_finance_xlsx(client_id, api_key, date_from):
    """v3/finance/transaction/list отключён Ozon 06.07.2026 (obsolete method).
    Новый путь: собрать posting_number за период, затем пачками по 200 запросить
    начисления через v1/finance/accrual/postings."""
    headers = {"Client-Id": client_id, "Api-Key": api_key}
    accrual_types = _ozon_accrual_types(headers)

    posting_numbers = _ozon_posting_numbers(client_id, api_key, date_from, TODAY)

    wb = Workbook()
    ws = wb.active
    ws.title = "Финансы"
    ws.append(["posting_number", "type_id", "type_name", "accrual_date", "amount", "currency", "sku", "quantity", "seller_price"])
    total = 0
    for i in range(0, len(posting_numbers), 200):
        chunk = posting_numbers[i:i + 200]
        r = get_with_retry_post(
            "https://api-seller.ozon.ru/v1/finance/accrual/postings", headers,
            {"posting_numbers": chunk},
        )
        for pa in r.json().get("posting_accruals", []):
            pn = pa.get("posting_number")
            for acc in pa.get("accruals", []) or []:
                accrued = acc.get("accrued") or {}
                seller_price = acc.get("seller_price") or {}
                ws.append([
                    pn, acc.get("type_id"), accrual_types.get(acc.get("type_id"), acc.get("type_id")),
                    acc.get("accrual_date"), accrued.get("amount"), accrued.get("currency"),
                    acc.get("sku"), acc.get("quantity"), seller_price.get("amount"),
                ])
                total += 1
        time.sleep(0.3)
    return workbook_to_bytes(wb), total


SELSUP_ORG_NAMES = {
    100943: "Timeless (ИП Островская В.П.)",
    100974: "ИП Ральников",
    100976: "Ронжина",
    100978: "Прегер",
    100980: "ПРОФТЕКС (Сказка)",
    100982: "Назарова",
    100984: "Яковлев (Milky Garden)",
}


def _selsup_org_map(headers, sku_ids):
    """Батч-запросами к product/find выясняем organizationId по skuId."""
    org_map = {}
    ids = list(sku_ids)
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        resp = get_with_retry(f"{SELSUP_BASE}/api/product/find", headers, {"ids": chunk, "limit": 200})
        for row in resp.json().get("rows", []):
            org_map[row.get("id")] = row.get("organizationId")
        time.sleep(0.3)
    return org_map


def selsup_stocks_xlsx():
    headers = {"Authorization": SELSUP_TOKEN}
    wb = Workbook()
    ws = wb.active
    ws.title = "Остатки"
    cols = [
        "warehouseId", "warehouseName", "skuId", "article", "wbSize",
        "ozonArticle", "cellName", "quantity", "availableQuantity",
        "calculatedQuantity", "modifyDate", "organizationId", "organizationName",
    ]
    ws.append(cols)
    total = 0
    for wh_id, wh_name in SELSUP_WAREHOUSES:
        resp = get_with_retry(f"{SELSUP_BASE}/api/wms/stock/all", headers, {"warehouseId": wh_id})
        items = [it for it in resp.json() if it.get("quantity")]  # без нулевых "призрачных" остатков
        org_map = _selsup_org_map(headers, {it.get("skuId") for it in items if it.get("skuId")})
        for it in items:
            product = (it.get("sku") or {}).get("product") or {}
            cell = it.get("cell") or {}
            org_id = org_map.get(it.get("skuId"))
            ws.append([
                wh_id, wh_name, it.get("skuId"), product.get("anyArticle"),
                product.get("wildberriesSizeId"), product.get("ozonArticle"),
                cell.get("fullName"), it.get("quantity"), it.get("availableQuantity"),
                it.get("calculatedQuantity"), it.get("modifyDate"),
                org_id, SELSUP_ORG_NAMES.get(org_id, f"Неизвестно ({org_id})"),
            ])
        total += len(items)
    return workbook_to_bytes(wb), total


SELSUP_INCOME_OPS = {"PUT", "CONFIRMED", "FOUND"}
SELSUP_OUTCOME_OPS = {"TAKE", "TAKE_MARKETPLACE", "SALE_PRODUCT"}


def _selsup_history_page(headers, op, page):
    resp = get_with_retry(
        f"{SELSUP_BASE}/api/wms/findItemHistory", headers,
        {"operation": op, "limit": 500, "page": page, "count": True},
    )
    return resp.json()


def _selsup_product_info_map(headers, sku_ids):
    """Батч-запросами к product/find выясняем артикул и название по skuId."""
    info_map = {}
    ids = list(sku_ids)
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        resp = get_with_retry(f"{SELSUP_BASE}/api/product/find", headers, {"ids": chunk, "limit": 200})
        for row in resp.json().get("rows", []):
            model = ((row.get("view") or {}).get("model") or {})
            info_map[row.get("id")] = (model.get("article"), row.get("name"))
        time.sleep(0.3)
    return info_map


def selsup_movements_yesterday_xlsx():
    """Приёмки и отгрузки Selsup за вчерашний день (findItemHistory).
    Свежие записи в конце (возрастание id = по времени), поэтому идём
    от последней страницы назад, пока не пройдём весь вчерашний день."""
    headers = {"Authorization": SELSUP_TOKEN}
    yesterday = (TODAY - timedelta(days=1)).isoformat()

    all_rows = []
    for op_set, op_group in ((SELSUP_INCOME_OPS, "Приёмка"), (SELSUP_OUTCOME_OPS, "Отгрузка")):
        for op in op_set:
            first = _selsup_history_page(headers, op, 1)
            total = first.get("total") or 0
            if not total:
                continue
            total_pages = (total // 500) + 1

            page = total_pages
            while page >= 1:
                data = _selsup_history_page(headers, op, page)
                rows = data.get("rows", [])
                if not rows:
                    page -= 1
                    continue
                oldest_date = (rows[0].get("date") or "")[:10]
                for r in rows:
                    date_str = (r.get("date") or "")[:10]
                    if date_str == yesterday:
                        r["_op_group"] = op_group
                        all_rows.append(r)
                if oldest_date and oldest_date < yesterday:
                    break
                page -= 1
                time.sleep(0.2)

    wb = Workbook()
    ws = wb.active
    ws.title = "Приёмки и отгрузки"
    ws.append([
        "тип", "operation", "date", "warehouseId", "orderId", "orderType",
        "skuId", "артикул", "название", "cellName", "quantity", "userId",
    ])
    product_info = _selsup_product_info_map(
        headers, {(r.get("item") or {}).get("skuId") for r in all_rows if (r.get("item") or {}).get("skuId")}
    )
    for r in all_rows:
        order = r.get("order") or {}
        item = r.get("item") or {}
        cell = item.get("cell") or {}
        sku_id = item.get("skuId")
        article, name = product_info.get(sku_id, (None, None))
        ws.append([
            r.get("_op_group"), r.get("operation"), r.get("date"), r.get("warehouseId"),
            r.get("orderId"), order.get("type"), sku_id, article, name, cell.get("fullName"),
            r.get("itemTotalQuantity"), r.get("userId"),
        ])
    return workbook_to_bytes(wb), len(all_rows)


ADV_BASE = "https://advert-api.wildberries.ru"


ADV_STATUS_LABELS = {
    4: "готова к запуску",
    7: "завершена",
    8: "отклонена",
    9: "активна",
    11: "приостановлена",
}
ADV_STATUS_FILL = {
    "активна": PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"),
    "приостановлена": PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"),
    "завершена": PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid"),
    "отклонена": PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"),
    "готова к запуску": PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid"),
}


def wb_advertising_report_xlsx(headers, date_from, date_to):
    """Сводный отчёт по рекламе WB: По товарам / Списания / Итого по кампаниям."""
    # список кампаний (все статусы/типы)
    r = get_with_retry(f"{ADV_BASE}/adv/v1/promotion/count", headers)
    campaign_ids = []
    status_map = {}
    for group in r.json().get("adverts", []):
        status_label = ADV_STATUS_LABELS.get(group.get("status"), str(group.get("status")))
        for it in group.get("advert_list", []):
            campaign_ids.append(it["advertId"])
            status_map[it["advertId"]] = status_label
    campaign_ids = list(dict.fromkeys(campaign_ids))

    # списания
    r = get_with_retry(f"{ADV_BASE}/adv/v1/upd", headers, {"from": date_from.isoformat(), "to": date_to.isoformat()})
    upd_rows = r.json() if isinstance(r.json(), list) else []

    # детальная статистика (до 50 id за раз)
    fullstats = []
    for i in range(0, len(campaign_ids), 50):
        chunk = campaign_ids[i:i + 50]
        for attempt in range(6):
            resp = httpx.get(
                f"{ADV_BASE}/adv/v3/fullstats", headers=headers,
                params={"ids": ",".join(map(str, chunk)), "beginDate": date_from.isoformat(), "endDate": date_to.isoformat()},
                timeout=60,
            )
            if resp.status_code == 429:
                time.sleep(65)
                continue
            resp.raise_for_status()
            break
        fullstats.extend(resp.json() or [])
        if i + 50 < len(campaign_ids):
            time.sleep(65)

    wb = Workbook()

    ws1 = wb.active
    ws1.title = "По товарам"
    ws1.append(["Дата", "ID кампании", "Статус", "nmId", "Товар", "Показы", "Клики", "CTR%", "CPC", "Затраты", "Заказы", "Корзина", "Ср.позиция"])
    row_count = 0
    for camp in fullstats:
        status_label = status_map.get(camp["advertId"], "")
        fill = ADV_STATUS_FILL.get(status_label)
        booster_by_date = {b["date"]: b.get("avg_position") for b in camp.get("boosterStats", [])}
        for day in camp.get("days", []):
            date = day["date"][:10]
            for app in day.get("apps", []):
                for nm in app.get("nms", []):
                    ws1.append([
                        date, camp["advertId"], status_label, nm["nmId"], nm["name"],
                        nm["views"], nm["clicks"], nm["ctr"], nm["cpc"], nm["sum"],
                        nm["orders"], nm["atbs"], booster_by_date.get(date),
                    ])
                    row_count += 1
                    if fill:
                        ws1.cell(row=ws1.max_row, column=3).fill = fill

    ws2 = wb.create_sheet("Списания")
    ws2.append(["Дата/время", "Кампания", "ID", "Тип оплаты", "Сумма"])
    for u in upd_rows:
        ws2.append([u.get("updTime"), u.get("campName"), u.get("advertId"), u.get("paymentType"), u.get("updSum")])

    ws3 = wb.create_sheet("Итого по кампаниям")
    ws3.append(["ID кампании", "Статус", "Затраты", "Показы", "Клики", "CTR%", "Заказы", "Сумма заказов", "ДРР%"])
    for camp in fullstats:
        spend = camp.get("sum", 0)
        revenue = camp.get("sum_price", 0)
        drr = round(spend / revenue * 100, 1) if revenue else None
        status_label = status_map.get(camp["advertId"], "")
        ws3.append([camp["advertId"], status_label, spend, camp.get("views"), camp.get("clicks"), camp.get("ctr"), camp.get("orders"), revenue, drr])
        if status_label in ADV_STATUS_FILL:
            ws3.cell(row=ws3.max_row, column=2).fill = ADV_STATUS_FILL[status_label]

    # кампании без данных за период (нет показов) — тоже показываем со статусом
    seen_ids = {camp["advertId"] for camp in fullstats}
    for aid in campaign_ids:
        if aid not in seen_ids:
            status_label = status_map.get(aid, "")
            ws3.append([aid, status_label, 0, 0, 0, None, 0, 0, None])
            if status_label in ADV_STATUS_FILL:
                ws3.cell(row=ws3.max_row, column=2).fill = ADV_STATUS_FILL[status_label]

    return workbook_to_bytes(wb), row_count


def run_wb_cabinet(service, key, name, api_key, do_finance):
    headers = {"Authorization": api_key}
    folder_id = FOLDERS["wb"][key]
    log(f"WB [{name}]")

    try:
        data, n = wb_orders_xlsx(headers, DATE_FROM_30D)
        upload_bytes(service, folder_id, "Лента заказов (30 дней).xlsx", data)
        log(f"  orders 30d: {n} rows")
    except Exception:
        log(f"  ERROR orders 30d:\n{traceback.format_exc()}")

    try:
        data, n = wb_orders_xlsx(headers, DATE_FROM_7D)
        upload_bytes(service, folder_id, "Лента заказов (7 дней).xlsx", data)
        log(f"  orders 7d: {n} rows")
    except Exception:
        log(f"  ERROR orders 7d:\n{traceback.format_exc()}")

    try:
        data, n = wb_orders_xlsx(headers, DATE_FROM_60D, date_from=DATE_FROM_60D, date_to=DATE_PREV30_END)
        upload_bytes(service, folder_id, "Лента заказов (предыдущие 30 дней).xlsx", data)
        log(f"  orders prev30d: {n} rows")
    except Exception:
        log(f"  ERROR orders prev30d:\n{traceback.format_exc()}")

    try:
        data, n = wb_sales_xlsx(headers, DATE_FROM_30D)
        upload_bytes(service, folder_id, "Продажи (30 дней).xlsx", data)
        log(f"  sales: {n} rows")
    except Exception:
        log(f"  ERROR sales:\n{traceback.format_exc()}")

    try:
        data, n = wb_advertising_report_xlsx(headers, DATE_FROM_30D, TODAY)
        upload_bytes(service, folder_id, "Реклама.xlsx", data)
        log(f"  advertising: {n} rows")
    except Exception:
        log(f"  ERROR advertising:\n{traceback.format_exc()}")

    try:
        data, n = wb_stocks_xlsx(headers)
        upload_bytes(service, folder_id, "Остатки.xlsx", data)
        log(f"  stocks: {n} rows")
    except Exception:
        log(f"  ERROR stocks:\n{traceback.format_exc()}")

    try:
        data, n = wb_prices_xlsx(headers)
        upload_bytes(service, folder_id, "Цены и скидки.xlsx", data)
        log(f"  prices: {n} rows")
    except Exception:
        log(f"  ERROR prices:\n{traceback.format_exc()}")

    try:
        data, n = wb_news_xlsx(headers, DATE_FROM_30D)
        upload_bytes(service, folder_id, "Новости.xlsx", data)
        log(f"  news: {n} rows")
    except Exception:
        log(f"  ERROR news:\n{traceback.format_exc()}")

    if do_finance:
        try:
            data, n = wb_finance_xlsx(headers, DATE_FROM_30D)
            upload_bytes(service, folder_id, "Финансовый отчет (30 дней).xlsx", data)
            log(f"  finance: {n} rows")
        except Exception:
            log(f"  ERROR finance:\n{traceback.format_exc()}")


def run_ozon_cabinet(service, key, name, client_id, api_key):
    folder_id = FOLDERS["ozon"][key]
    log(f"Ozon [{name}]")

    try:
        data, n = ozon_stocks_xlsx(client_id, api_key)
        upload_bytes(service, folder_id, "Остатки.xlsx", data)
        log(f"  stocks: {n} rows")
    except Exception:
        log(f"  ERROR stocks:\n{traceback.format_exc()}")

    try:
        data, n = ozon_prices_xlsx(client_id, api_key)
        upload_bytes(service, folder_id, "Цены.xlsx", data)
        log(f"  prices: {n} rows")
    except Exception:
        log(f"  ERROR prices:\n{traceback.format_exc()}")

    try:
        data, n = ozon_postings_xlsx(client_id, api_key, DATE_FROM_30D)
        upload_bytes(service, folder_id, "Заказы (30 дней).xlsx", data)
        log(f"  postings 30d: {n} rows")
    except Exception:
        log(f"  ERROR postings 30d:\n{traceback.format_exc()}")

    try:
        data, n = ozon_postings_xlsx(client_id, api_key, DATE_FROM_7D)
        upload_bytes(service, folder_id, "Заказы (7 дней).xlsx", data)
        log(f"  postings 7d: {n} rows")
    except Exception:
        log(f"  ERROR postings 7d:\n{traceback.format_exc()}")

    try:
        data, n = ozon_postings_xlsx(client_id, api_key, DATE_FROM_60D, DATE_PREV30_END)
        upload_bytes(service, folder_id, "Заказы (предыдущие 30 дней).xlsx", data)
        log(f"  postings prev30d: {n} rows")
    except Exception:
        log(f"  ERROR postings prev30d:\n{traceback.format_exc()}")

    try:
        data, n = ozon_finance_xlsx(client_id, api_key, DATE_FROM_30D)
        upload_bytes(service, folder_id, "Финансы (30 дней).xlsx", data)
        log(f"  finance: {n} rows")
    except Exception:
        log(f"  ERROR finance:\n{traceback.format_exc()}")

    try:
        data, n = ozon_news_xlsx(client_id, api_key)
        upload_bytes(service, folder_id, "Новости.xlsx", data)
        log(f"  news: {n} rows")
    except Exception:
        log(f"  ERROR news:\n{traceback.format_exc()}")


def run_selsup(service):
    log("Selsup")
    try:
        data, n = selsup_stocks_xlsx()
        upload_bytes(service, FOLDERS["selsup"], f"Остатки {TODAY.strftime('%d.%m.%Y')}.xlsx", data, archive=False)
        log(f"  stocks: {n} rows")
    except Exception:
        log(f"  ERROR stocks:\n{traceback.format_exc()}")

    try:
        data, n = selsup_movements_yesterday_xlsx()
        yesterday_str = (TODAY - timedelta(days=1)).strftime("%d.%m.%Y")
        upload_bytes(service, FOLDERS["selsup"], f"Приёмки и отгрузки {yesterday_str}.xlsx", data, archive=False)
        log(f"  movements: {n} rows")
    except Exception:
        log(f"  ERROR movements:\n{traceback.format_exc()}")


def run_ym_cabinet(service, key, name, api_key, business_id, campaigns):
    folder_id = FOLDERS["ym"][key]
    log(f"YM [{name}]")

    try:
        data, n = ym.orders_xlsx(campaigns, api_key, DATE_FROM_30D, TODAY)
        upload_bytes(service, folder_id, "Заказы (30 дней).xlsx", data)
        log(f"  orders: {n} rows")
    except Exception:
        log(f"  ERROR orders:\n{traceback.format_exc()}")

    try:
        data, n = ym.stocks_xlsx(campaigns, api_key)
        upload_bytes(service, folder_id, "Остатки.xlsx", data)
        log(f"  stocks: {n} rows")
    except Exception:
        log(f"  ERROR stocks:\n{traceback.format_exc()}")

    try:
        data, n = ym.prices_xlsx(business_id, api_key)
        upload_bytes(service, folder_id, "Цены.xlsx", data)
        log(f"  prices: {n} rows")
    except Exception:
        log(f"  ERROR prices:\n{traceback.format_exc()}")

    try:
        data, n = ym.offers_xlsx(business_id, api_key)
        upload_bytes(service, folder_id, "Карточки товаров.xlsx", data)
        log(f"  offers: {n} rows")
    except Exception:
        log(f"  ERROR offers:\n{traceback.format_exc()}")

    try:
        # реализация считается по последнему полному месяцу
        month_date = TODAY.replace(day=1) - timedelta(days=1)
        primary_campaign = campaigns[0][0]
        data = ym.realization_report_bytes(primary_campaign, api_key, month_date.year, month_date.month)
        upload_bytes(service, folder_id, f"Реализация {month_date.strftime('%Y-%m')}.xlsx", data)
        log(f"  realization: OK ({month_date.strftime('%Y-%m')})")
    except Exception:
        log(f"  ERROR realization:\n{traceback.format_exc()}")

    try:
        data = ym.shows_sales_report_bytes(business_id, api_key, DATE_FROM_30D, TODAY)
        upload_bytes(service, folder_id, "Аналитика продаж (воронка, 30 дней).xlsx", data)
        log("  shows-sales: OK")
    except Exception:
        log(f"  ERROR shows-sales:\n{traceback.format_exc()}")

    try:
        data = ym.united_orders_report_bytes(business_id, api_key, DATE_FROM_30D, TODAY)
        upload_bytes(service, folder_id, "Транзакции по заказам и товарам (30 дней).xlsx", data)
        log("  united-orders: OK")
    except Exception:
        log(f"  ERROR united-orders:\n{traceback.format_exc()}")


def main():
    log("=== Запуск ночной выгрузки ===")
    service = get_drive_service()

    for i, (key, name, api_key) in enumerate(WB_CABINETS):
        run_wb_cabinet(service, key, name, api_key, do_finance=True)

    for key, name, client_id, api_key in OZON_CABINETS:
        run_ozon_cabinet(service, key, name, client_id, api_key)

    for key, name, api_key, business_id, campaigns in YM_CABINETS:
        run_ym_cabinet(service, key, name, api_key, business_id, campaigns)

    run_selsup(service)

    log("=== Выгрузка завершена ===")


if __name__ == "__main__":
    main()

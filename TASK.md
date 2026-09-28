# ТЗ: marketplace_db_sync

Автономный агент, читающий этот файл, не видел предыдущих обсуждений — документ
самодостаточен. Не трогай ничего за пределами этого репозитория: реальный
сервер, Google Drive, чужие проекты — тебе недоступны и не нужны на этом этапе.

## 1. Назначение

Компания продаёт текстиль (постельное бельё и т.п.) через 3 личных кабинета —
**skazka** (юрлицо ООО «Профтекс», бренд «Сказка»), **milky_garden** (Milky
Garden), **timeless** (Timeless) — на Ozon и Wildberries одновременно, плюс
ведёт складской учёт через SelSup (WMS-система).

Раз в сутки на сервере компании (Windows) уже работает скрипт
`nightly_export.py`, который тянет из API Ozon/WB/SelSup остатки и
заказы/продажи за 7/30/предыдущие 30 дней и кладёт их как xlsx-файлы на
Google Drive — для ручного просмотра и как источник для custom GPT.

**Твоя задача: написать НОВЫЙ, отдельный проект**, который тянет **те же
данные** из тех же API, но кладёт их в Postgres вместо xlsx. Это фундамент
для будущих аналитических инструментов (расчёт скорости продаж, потребности
в пополнении, распределение товара по складам) — им нужен SQL-запрашиваемый
источник, а не набор xlsx-файлов.

Настоящий исходник этого скрипта лежит в этом репозитории в
[`reference/nightly_export.py`](reference/nightly_export.py) — **только для
справки по реальным API-контрактам** (все ключевые детали уже продублированы
текстом в §4 ниже; если где-то расхождение — верь файлу, а не пересказу).
**Не копировать его структуру и не модифицировать сам файл** — см.
[`reference/README.md`](reference/README.md) о том, почему его архитектура
не подходит для нового проекта.

## 2. Архитектура

Требование пользователя — Clean Architecture / Ports & Adapters, без
фреймворков, ручной DI (один композиционный корень). Структура каталогов:

```
domain/            # dataclasses: OrderLine, SaleLine, StockSnapshot, и т.п. Без внешних зависимостей.
application/
    ports.py        # typing.Protocol: MarketplaceSource, StockRepository, OrderRepository, SyncRunRepository...
    use_cases/       # SyncStocksUseCase, SyncOrdersUseCase, SyncSalesUseCase — принимают порты через конструктор
infrastructure/
    sources/
        wb/          # WBStocksSource, WBOrdersSource, WBSalesSource — реализуют MarketplaceSource
        ozon/        # OzonStocksSource, OzonOrdersSource
        selsup/      # SelsupStocksSource
    persistence/
        postgres/    # реализация портов-репозиториев поверх psycopg, schema.sql
    config/
        accounts.py  # конфигурация кабинетов (см. §5)
interface/
    cli/
        main.py      # точка входа, argparse, композиционный корень (здесь и только здесь
                       # создаются конкретные адаптеры и передаются в use cases)
tests/
    application/     # тесты use cases на фейках портов (Protocol реализуется вручную, без моков библиотек)
```

Принципы (не опционально — это явное требование к архитектуре):
- Порты — `typing.Protocol` (структурная типизация, без `ABC`/наследования).
- `application/` не импортирует ничего из `infrastructure/`.
- Один и тот же порт репозитория (например `StockRepository`) должен уметь
  иметь два адаптера — Postgres (боевой) и in-memory fake (тесты) — без
  изменений в `use_cases/`.
- Никакого `async`/`asyncio` — проект синхронный и пакетный (ночной прогон
  раз в сутки), это осознанный выбор простоты, не пробел.
- HTTP-клиент — `httpx`, не `requests`.
- Retry/backoff на HTTP-запросах к API маркетплейсов обязателен (см. §7) —
  все три API отдают 429/5xx под нагрузкой.

## 3. Учётные записи (кабинеты)

Используй **эти** ключи кабинетов везде в коде и в БД (не короткие
`sk`/`mg`/`tl`, которые использует старый xlsx-скрипт — они для
единообразия с остальной кодовой базой компании):

| account key     | Ozon юрлицо/бренд | WB бренд      |
|------------------|--------------------|----------------|
| `skazka`         | ООО «Профтекс»     | Сказка         |
| `milky_garden`   | Milky Garden       | Milky Garden   |
| `timeless`       | Timeless           | Timeless       |

Конфигурация в `infrastructure/config/accounts.py` — по аналогии с обычной
структурой в этой кодовой базе: `Dict[str, Dict]`, ключи `skazka` /
`milky_garden` / `timeless`, значения — имена переменных окружения с
учётными данными (не сами значения!). Пример ожидаемых переменных окружения
(итоговый `.env` заполняется реальными секретами уже после тебя, при
деплое — тебе реальные значения не нужны и не будут доступны):

```
WB_API_KEY=              # skazka
WB_API_KEY_MILKY=
WB_API_KEY_TIMELESS=

OZON_CLIENT_ID=          # skazka
OZON_API_KEY=
OZON_CLIENT_ID_TIMELESS=
OZON_API_KEY_TIMELESS=
OZON_CLIENT_ID_MILKY=
OZON_API_KEY_MILKY=

SELSUP_API_TOKEN=        # общий на все кабинеты, кабинет определяется по organizationId в ответе API

DATABASE_URL=            # postgres://user:pass@host:5432/dbname
```

Положи `.env.example` с этими именами (без значений) в корень репозитория,
и убедись что `.env` в `.gitignore` (файл уже создан и туда включён — не
трогай существующий `.gitignore`, просто проверь).

## 4. Источники данных — точные контракты API

### 4.1 Wildberries

Аутентификация: заголовок `Authorization: <api_key>` (без `Bearer`), свой
ключ на кабинет (см. §3).

**Заказы** (лента заказов, не путать с продажами):
```
GET https://statistics-api.wildberries.ru/api/v1/supplier/orders
    ?dateFrom=<ISO date>&flag=0
```
Возвращает список объектов за окно `[dateFrom, сейчас]` (WB сам не
поддерживает `dateTo` — фильтруй по датам на своей стороне, если нужно
конкретное окно вроде "предыдущие 30 дней"). Поля на строку: `date`,
`lastChangeDate`, `warehouseName`, `regionName`, `supplierArticle`, `nmId`,
`barcode`, `subject`, `brand`, `techSize`, `totalPrice`, `discountPercent`,
`finishedPrice`, `priceWithDisc`, `isCancel` (bool — заказ отменён),
`gNumber`, `srid` (уникальный id строки заказа — natural key для upsert).

**Продажи** (подтверждённые выкупы, отдельная сущность от заказов):
```
GET https://statistics-api.wildberries.ru/api/v1/supplier/sales
    ?dateFrom=<ISO date>&flag=0
```
Поля: те же плюс `spp`, `forPay`, `saleID` (natural key), `orderType`, минус
`isCancel`.

**Остатки** (асинхронный отчёт — создать задачу, дождаться готовности, скачать):
```
GET https://seller-analytics-api.wildberries.ru/api/v1/warehouse_remains
    ?groupByNm=true&groupByBarcode=true&groupBySize=true
    -> {"data": {"taskId": "..."}}

GET .../api/v1/warehouse_remains/tasks/{taskId}/status   # поллинг до status=="done"
GET .../api/v1/warehouse_remains/tasks/{taskId}/download # -> список строк
```
Каждая строка: `nmId`, `barcode`, `techSize`, `volume`, `warehouses`
(список `{warehouseName, quantity}` — на артикул может быть несколько
складов, разворачивай в отдельные строки БД). `vendorCode` (артикул
продавца) в этом отчёте не приходит — его нужно отдельно сматчить по
`nmId` через Content API:
```
POST https://content-api.wildberries.ru/content/v2/get/cards/list
Body: {"settings": {"cursor": {"limit": 100}, "filter": {"withPhoto": -1}}}
```
Постранично (курсор в ответе `cursor.nmID`/`cursor.updatedAt`, передавать
обратно в следующий запрос), пока страница не станет пустой. **Важно**: API
всегда отдаёт максимум 100 карточек за раз, вне зависимости от
запрошенного `limit`. Строит `nmId -> vendorCode`.

Обрабатывай 429 отдельно — при таком статусе ждать и повторять, не считать
ошибкой (см. §7).

### 4.2 Ozon

Аутентификация: заголовки `Client-Id: <id>`, `Api-Key: <key>`, свои на
кабинет (см. §3).

**Остатки:**
```
POST https://api-seller.ozon.ru/v4/product/info/stocks
Body: {"filter": {"visibility": "ALL"}, "limit": 1000, "cursor": "<из предыдущего ответа>"}
```
Курсорная пагинация (`cursor` в ответе, пустой/отсутствующий — конец).
Каждый `item` имеет `offer_id`, `product_id`, и список `stocks[]`, каждый
элемент — `{type, present, reserved, sku}` (`type` — тип склада FBO/FBS,
может быть несколько строк на один `offer_id`).

**Остатки по складам FBO (опционально, отдельная детализация):**
```
POST https://api-seller.ozon.ru/v2/analytics/stock_on_warehouses
Body: {"limit": 1000, "offset": <N>, "warehouse_type": "ALL"}
```
Офсетная пагинация. Строка: `item_code` (=offer_id), `sku`, `item_name`,
`warehouse_name`, `free_to_sell_amount`, `reserved_amount`,
`promised_amount`.

**Заказы (постинги)** — два эндпоинта, объединить в одну таблицу:
```
POST https://api-seller.ozon.ru/v3/posting/fbs/list
POST https://api-seller.ozon.ru/v2/posting/fbo/list
Body: {"dir": "ASC", "filter": {"since": "<ISO datetime>Z", "to": "<ISO datetime>Z"},
       "limit": 1000, "offset": <N>, "with": {"analytics_data": true}}
```
Офсетная пагинация (шаг 1000, пока ответ не короче лимита). На постинг:
`posting_number` (+`offer_id` — natural key на строку, постинг может
содержать несколько товарных позиций в `products[]`), `status`,
`order_date`/`created_at`/`in_process_at`, `analytics_data.warehouse_name`,
`.city`, `.region`; на каждую позицию в `products[]`: `offer_id`, `sku`,
`name`, `quantity`, `price`, `currency_code`.

### 4.3 SelSup

Аутентификация: заголовок `Authorization: <token>` (без `Bearer`), один
токен на все кабинеты — кабинет определяется по `organizationId` в самих
данных, не по токену.

**Остатки по складам:**
```
GET https://api.selsup.ru/api/wms/stock/all?warehouseId=<id>
```
Вызывать по каждому складу из списка ниже. Каждая строка:
`skuId`, `sku.product.anyArticle` (артикул), `sku.product.wildberriesSizeId`,
`sku.product.ozonArticle`, `cell.fullName`, `quantity`,
`availableQuantity`, `calculatedQuantity`, `modifyDate`. **Пропускай строки
с `quantity == 0`** (в API это "призрачные" нулевые остатки — так делает и
эталонный скрипт).

Склады (id, название) — зашивай как константу:
```python
SELSUP_WAREHOUSES = [
    (10001, "FBS"), (10020, "Kvant"), (10023, "Технический"),
    (10016, "Фурнитура Профтекс"), (10018, "Фурнитура ИП"),
    (10019, "Возвраты"), (10017, "Временное хранение"),
]
```
`10020 "Kvant"` — это склад «Квант», второй физический склад компании (это
подтверждённый факт, важно не переименовывать/не путать с другими).

Чтобы определить кабинет (organizationId -> account key), нужны батч-запросы
к `POST https://api.selsup.ru/api/product/find` с `{"ids": [...skuId...],
"limit": 200}` — в ответе `rows[].organizationId`. Маппинг id -> кабинет
(подтверждённые реальные значения, тоже зашить константой):
```python
SELSUP_ORGANIZATION_IDS = {100980: "skazka", 100984: "milky_garden", 100943: "timeless"}
```

**Вне scope этого проекта:** приёмки/отгрузки SelSup (`findItemHistory`,
операции `PUT/CONFIRMED/FOUND/TAKE/...`) — эта область данных уже
покрывается отдельным существующим проектом `priemka` (синхронизация
событий приёмки в свою БД). Не дублируй — тяни только остатки (`stock/all`),
не историю движений.

## 5. Схема Postgres (рекомендуемая, можно уточнять детали типов/индексов)

Ключевое архитектурное отличие от xlsx-версии: **не** хранить отдельно
"заказы за 7 дней" / "за 30 дней" / "за предыдущие 30 дней" как три разных
среза. Тянуть **одно широкое окно** (например 65 дней) заказов/продаж за
ночь, аплоадить строки через `UPSERT` по natural key, а нужные периоды
(7д/30д/пред.30д) — это `WHERE date >= ...` при чтении из БД, не при записи.
Это и есть смысл перехода на БД — иначе никакой выгоды перед xlsx нет.

Остатки — снэпшот на каждую ночь (не перезаписывать предыдущий день, а
добавлять новую строку с `snapshot_date`). Это специально: наличие полной
истории остатков по дням в будущем позволит считать скорость продаж точнее
(на основе фактических дней наличия товара, а не календарных) — не
реализовывать эту логику сейчас, только не терять историю.

Примерные таблицы (создай как SQL-миграцию `infrastructure/persistence/postgres/schema.sql`,
как в проекте `priemka` — там один файл схемы, применяется при первом
подключении):

```sql
CREATE TABLE wb_orders (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    srid TEXT NOT NULL,
    order_date DATE NOT NULL,
    last_change_date TIMESTAMPTZ,
    warehouse_name TEXT, region_name TEXT,
    supplier_article TEXT, nm_id BIGINT, barcode TEXT,
    subject TEXT, brand TEXT, tech_size TEXT,
    total_price NUMERIC, discount_percent NUMERIC,
    finished_price NUMERIC, price_with_disc NUMERIC,
    is_cancel BOOLEAN, g_number TEXT,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, srid)
);

CREATE TABLE wb_sales (
    -- аналогично wb_orders, natural key (account, sale_id), плюс spp/for_pay/order_type, без is_cancel
);

CREATE TABLE wb_stocks (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    nm_id BIGINT NOT NULL, vendor_code TEXT, barcode TEXT,
    tech_size TEXT, volume NUMERIC,
    warehouse_name TEXT NOT NULL, quantity NUMERIC NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, snapshot_date, nm_id, barcode, warehouse_name)
);

CREATE TABLE ozon_orders (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    posting_number TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    status TEXT, order_date TIMESTAMPTZ, source TEXT,
    warehouse_name TEXT, city TEXT, region TEXT,
    sku BIGINT, product_name TEXT, quantity INT, price NUMERIC, currency TEXT,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, posting_number, offer_id)
);

CREATE TABLE ozon_stocks (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    offer_id TEXT NOT NULL, product_id BIGINT, sku BIGINT,
    stock_type TEXT NOT NULL, present INT, reserved INT,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, snapshot_date, offer_id, stock_type)
);

CREATE TABLE selsup_stocks (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    warehouse_id INT NOT NULL, warehouse_name TEXT,
    sku_id BIGINT NOT NULL, article TEXT, wb_size TEXT, ozon_article TEXT,
    cell_name TEXT, quantity NUMERIC, available_quantity NUMERIC,
    calculated_quantity NUMERIC, modify_date TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, snapshot_date, warehouse_id, sku_id)
);

CREATE TABLE sync_runs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,       -- 'wb_orders' | 'wb_sales' | 'wb_stocks' | 'ozon_orders' | 'ozon_stocks' | 'selsup_stocks'
    account TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
    status TEXT NOT NULL, records_fetched INT NOT NULL DEFAULT 0,
    error_message TEXT
);
```

## 6. CLI

По образцу `priemka`:
```
python -m interface.cli.main sync <wb|ozon|selsup|all> [--account <name|all>]
python -m interface.cli.main status
```
`sync` без доп. флагов дат — всегда тянет фиксированное окно (см. §5) и
делает upsert; отдельные флаги `--since`/`--until` не нужны на этом этапе.
Каждый запуск источника оборачивается записью в `sync_runs` (started_at,
finished_at, status, records_fetched, error_message при исключении) — не
должен ронять весь процесс, если упал один источник/кабинет (логировать и
продолжать остальные, как это устроено в `priemka`).

## 7. Устойчивость к ошибкам API

Все три API отдают периодические 429/5xx. Нужна общая retry-обёртка
(`shared/http_retry.py` или похоже) с экспоненциальной задержкой и
ограниченным числом попыток (5-6), отдельно уважающая `Retry-After`/429 не
как фатальную ошибку. Не глотай исключения молча — логируй и **пробрасывай**
наверх до уровня use case/CLI, где они уже пишутся в `sync_runs.error_message`
и не прерывают остальные источники/кабинеты (см. §6).

## 8. Тестирование

- Unit-тесты на `application/use_cases/` через фейковые реализации портов
  (обычный класс, реализующий `Protocol` вручную, без `unittest.mock` —
  так сделано в `priemka/tests/application/fakes.py`, придерживайся того же
  стиля).
- Никаких реальных HTTP-запросов и реального Postgres в тестах.
- `requirements.txt`: `httpx`, `python-dotenv`, `psycopg[binary]` (или
  `psycopg2-binary`), `pytest`.

## 9. Что НЕ входит в scope этого этапа

- Цены, финансовые отчёты, реклама, новости/уведомления — эталонный
  xlsx-скрипт их тоже тянет, но они не нужны для аналитики остатков/продаж.
- Приёмки/отгрузки SelSup — домен проекта `priemka` (см. §4.3).
- Само подключение к реальному Postgres на боевом сервере, миграции на
  проде, Windows Task Scheduler — это разворачивается отдельно, после
  ревью кода, не твоя задача. Тебе реальный `DATABASE_URL`/секреты API не
  понадобятся и не будут выданы — весь код должен работать корректно и
  проверяться тестами без них.
- Yandex Market — есть в старом скрипте, в этот проект не входит.

## 10. Готовность

Результат — рабочий, покрытый тестами код в этом репозитории (ветка/PR —
на усмотрение), с `README.md`, обновлённым под факт реализации, и
`.env.example`. Финальную интеграцию с боевой БД и деплой на сервер сделает
человек после ревью — не пытайся сам подключаться к какой-либо внешней
инфраструктуре.

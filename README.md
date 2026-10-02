# marketplace_db_sync

Ежедневный сбор аналитики Ozon/Wildberries/SelSup (остатки, заказы,
продажи) в Postgres — аналог существующего
`MarketplaceGateway/scripts/nightly_export.py` на сервере компании,
но пишет в базу данных вместо xlsx-файлов на Google Drive.

Будет работать на сервере компании (100.104.20.51) по расписанию (Windows
Task Scheduler), рядом с существующей автоматизацией — не заменяет и не
меняет `nightly_export.py` (он остаётся в `reference/`, только для
справки по API-контрактам, см. `reference/README.md`).

Ключевое отличие от xlsx-версии: вместо трёх отдельных срезов
(7д/30д/предыдущие 30д) тянется одно широкое окно заказов/продаж (65
дней) и апсертится по natural key; нужные периоды — это `WHERE date >=
...` при чтении из БД. Остатки хранятся как снэпшот на каждую ночь
(история не перезаписывается) — фундамент для будущего расчёта скорости
продаж и потребности в пополнении.

## Архитектура

Clean Architecture / Ports & Adapters, без фреймворков, ручной DI:

```
domain/                  # dataclasses, без внешних зависимостей
application/
    ports.py              # typing.Protocol: MarketplaceSource, StockRepository, OrderRepository, SyncRunRepository
    use_cases/             # SyncStocksUseCase, SyncOrdersUseCase, SyncSalesUseCase
infrastructure/
    sources/
        wb/                 # WBOrdersSource, WBSalesSource, WBStocksSource
        ozon/               # OzonOrdersSource, OzonStocksSource
        selsup/             # SelsupStocksSource
    persistence/postgres/   # репозитории поверх psycopg, schema.sql
    config/                 # accounts.py — кабинеты, склады SelSup, окно синка
interface/cli/main.py     # точка входа, argparse, композиционный корень
shared/http_retry.py      # retry/backoff-обёртка над httpx (429/5xx)
tests/application/         # unit-тесты use cases на фейках портов
```

`application/` не импортирует ничего из `infrastructure/`. Порты — это
`typing.Protocol` (структурная типизация), поэтому один и тот же порт
репозитория имеет два адаптера — боевой Postgres (`infrastructure/persistence/postgres/`)
и in-memory фейк (`tests/application/fakes.py`) — без изменений в
`use_cases/`. Проект синхронный (без `async`) — это ночной пакетный
прогон раз в сутки.

## Кабинеты

| account key     | Ozon юрлицо/бренд | WB бренд      |
|------------------|--------------------|----------------|
| `skazka`         | ООО «Профтекс»     | Сказка         |
| `milky_garden`   | Milky Garden       | Milky Garden   |
| `timeless`       | Timeless           | Timeless       |

Конфигурация — `infrastructure/config/accounts.py`: словарь хранит
**имена** переменных окружения с учётными данными, не сами значения.

## Установка

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить реальными секретами при деплое
```

## Использование

```
python -m interface.cli.main sync <wb|ozon|selsup|sheets|all> [--account <name|all>]
python -m interface.cli.main status [--limit N]
python -m interface.cli.main backfill [--kinds K ...] [--account A] [--force] [--orders-mode sparse|all] [--max-files N]
python -m interface.cli.main api-backfill <wb_supplies|ozon_supplies|wb_promotions|wb_funnel> [--account A] [--from YYYY-MM-DD] [--to YYYY-MM-DD]
python -m interface.cli.main refresh-dims
python -m interface.cli.main coverage
```

`sync` каждую ночь собирает остатки (WB, Ozon, Selsup), заказы и продажи,
а также **цены** (WB, Ozon), **рекламу WB** (последние 7 дней, апсертом) и
**движения Selsup** (приёмки/отгрузки за последние 3 дня), а также:

- **поставки на маркетплейсы** (`supplies`, `supply_items`): FBW WB и FBO Ozon —
  что, когда и на какой склад отвезли; метки для «куда везти» и товар «в пути»;
- **воронка WB** (`wb_funnel_daily`): открытия карточки, корзина, заказы,
  выкупы, рейтинг по товару и дню (последние 3 дня каждую ночь);
- **акции** (`promotions`, `promotion_items`): календарь WB и акции Ozon с
  участвующими товарами; у Ozon API отдаёт только текущие, история копится с
  первой ночи;
- **остатки Ozon FBO по складам** (`ozon_warehouse_stocks`);
- **таблицы Google, которые ведут вручную** (`sync sheets`, сервисный аккаунт с
  доступом на чтение, нужен `GOOGLE_SERVICE_ACCOUNT_FILE`): производство
  (`production_lines` + журнал изменений `production_line_log`), кратности
  кванта (`quant_multiples`), расход ткани по артикулам (`article_specs`),
  справочник тканей (`dim_fabric`) и наличие ткани (`fabric_stock`, снэпшот на день).
  Производство перечитывается целиком; исчезнувшая строка помечается удалённой,
  а не стирается, а если лист пришёл «обрезанным» (меньше половины известных
  строк), загрузка отклоняется. Лист, у которого переименовали колонку, тоже
  отклоняется: лучше ошибка, чем сдвинутые данные.

`sync` тянет фиксированное окно данных и делает upsert; схема Postgres
(`infrastructure/persistence/postgres/schema.sql`) применяется
автоматически при подключении (все `CREATE TABLE IF NOT EXISTS`, так что
повторный вызов безопасен). Каждый запуск источника/кабинета оборачивается
записью в `sync_runs` — падение одного источника логируется и не прерывает
остальные (обработка ошибок и retry/backoff на 429/5xx — `shared/http_retry.py`).

## Фундамент для ML (этапы 1–2 из `docs/ML_FOUNDATION_PLAN.md`)

Дополнительные таблицы: `wb_prices`, `ozon_prices` (снэпшот на каждую
ночь), `wb_ad_stats` (кампания × товар × день), `selsup_movements`
(приёмки/отгрузки «Склада» и «Склада Квант»), `dim_article` (разбор
артикула: бренд, код дизайна, коды размеров, вариант).

Представления для признаков: `stock_by_place_daily`, `article_stock_daily`
(остатки WB / Ozon / Selsup FBS / Selsup Квант по артикулу и дню с флагами
`wb_out`/`ozon_out`), `out_of_stock_days_30` (дни без остатка за 30 дней),
`stock_days`, `orders_daily`, `selsup_movements_v`.

**Бэкфилл из Drive.** `backfill` читает архив «Выгрузка авто» (сервисный
аккаунт, только чтение; `GOOGLE_SERVICE_ACCOUNT_FILE` и
`DRIVE_ROOT_FOLDER_ID` в `.env`) и грузит его в те же таблицы, что и
ночной синк. Повторный запуск безопасен: загруженные файлы запоминаются в
`backfill_files`. Даты снэпшотов — дата изменения файла по Москве (так же,
как дата наблюдения у ночного синка). Окна заказов/продаж пересекаются, поэтому
по умолчанию (`--orders-mode sparse`) грузятся самый старый и три самых
свежих файла.

`coverage` показывает диапазон дат, число дней и строк по каждой таблице.

**История из API** (`api-backfill`): `wb_supplies` и `ozon_supplies` — вся
история поставок; `wb_promotions` — календарь акций с января (состав — только у ещё не
закончившихся, см. ограничения); `wb_funnel` — воронка по дням от новых к старым (WB отдаёт
3 запроса в минуту, поэтому полугодие занимает часы; прогон возобновляется —
загруженные дни пропускаются). Остатки Ozon по складам за прошлые дни
берутся из архива Drive (`backfill --kinds ozon_warehouse_stocks`).

### Ограничения

- Загрузки проверены на живых API: Selsup (остатки, движения), Ozon (заказы,
  остатки, цены, поставки, акции, остатки по складам) и WB кабинета Milky
  Garden (заказы, продажи, остатки, цены, реклама, поставки, акции, воронка).
  Кабинеты Сказка и Timeless на WB впервые пройдут в ночном прогоне — после него
  смотрите `status`.
- У движений Selsup нет кабинета: он определяется в представлении
  `selsup_movements_v` по остаткам (`sku_id` → account).
- **Трафик Ozon недоступен**: `analytics/data` считает все метрики, кроме заказов
  и выручки, устаревшими (показы и корзина требуют другого доступа). Воронка есть
  только у WB.
- **Состав акций WB доступен только до их окончания**: по автоакциям и по уже
  закончившимся «ручным» API отвечает 422 (проверено: 35 из 38). Поэтому
  сохраняются все акции календаря (с 01.01.2026), а участие товаров копится
  с 01.10.2026 — каждую ночь читаются активные и будущие акции. То же у Ozon.

## Тесты

```
pytest
```

Unit-тесты `application/use_cases/` на фейках портов
(`tests/application/fakes.py`), разбор артикулов и xlsx, оркестрация бэкфилла
на фейковом Drive. Интеграционные тесты на настоящем Postgres
(`tests/integration`) пропускаются, пока не задан `TEST_DATABASE_URL`
(пустая отдельная база — таблицы очищаются):

```
TEST_DATABASE_URL=postgresql://user@localhost/mdb_test pytest
```

## Вне scope этого этапа

Цены, финансовые отчёты, реклама, новости, приёмки/отгрузки SelSup (домен
проекта `priemka`), Yandex Market, деплой на боевой сервер и подключение к
боевому Postgres — см. `TASK.md` §9.

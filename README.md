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
python -m interface.cli.main sync <wb|ozon|selsup|all> [--account <name|all>]
python -m interface.cli.main status [--limit N]
```

`sync` тянет фиксированное окно данных и делает upsert; схема Postgres
(`infrastructure/persistence/postgres/schema.sql`) применяется
автоматически при подключении (все `CREATE TABLE IF NOT EXISTS`, так что
повторный вызов безопасен). Каждый запуск источника/кабинета оборачивается
записью в `sync_runs` — падение одного источника логируется и не прерывает
остальные (обработка ошибок и retry/backoff на 429/5xx — `shared/http_retry.py`).

## Тесты

```
pytest
```

Только unit-тесты `application/use_cases/` на фейковых реализациях портов
(`tests/application/fakes.py`, без моков и без реальных HTTP/Postgres).

## Вне scope этого этапа

Цены, финансовые отчёты, реклама, новости, приёмки/отгрузки SelSup (домен
проекта `priemka`), Yandex Market, деплой на боевой сервер и подключение к
боевому Postgres — см. `TASK.md` §9.

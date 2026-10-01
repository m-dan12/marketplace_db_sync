# Гайд: первый деплой и реальный прогон

Пошагово — как поднять `marketplace_db_sync` на сервере компании и
безопасно проверить его на реальных данных, прежде чем ставить в
расписание.

## 0. Что понадобится заранее

- Доступ на сервер (SSH/RDP).
- Python 3.11+ на сервере.
- Postgres — либо уже поднятый (локально на сервере или во внешнем
  хостинге), либо поднять новый пустой (см. шаг 3).
- Реальные ключи API: `WB_API_KEY*`, `OZON_CLIENT_ID*`/`OZON_API_KEY*`,
  `SELSUP_API_TOKEN` — те же самые, что уже используются в `nightly_export.py`
  на сервере (файл `C:\Users\Proftex1\MarketplaceGateway\.env` — их можно
  просто скопировать оттуда, там уже всё есть).

## 1. Скачать код на сервер

Пока PR не смёржен — тянем ветку с PR напрямую:

```bash
git clone https://github.com/m-dan12/marketplace_db_sync.git
cd marketplace_db_sync
git checkout claude/awesome-ramanujan-gv9cpf
```

После того как PR смёржат в `master` — на сервере достаточно:

```bash
git pull origin master
```

## 2. Настроить окружение Python

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## 3. Подготовить Postgres

Если базы ещё нет — создать пустую базу (например, `marketplace_sync`):

```sql
CREATE DATABASE marketplace_sync;
```

Схему таблиц создавать вручную не нужно — CLI сам применяет
`infrastructure/persistence/postgres/schema.sql` при каждом подключении
(там `CREATE TABLE IF NOT EXISTS`, повторный вызов безопасен).

## 4. Заполнить `.env`

```bash
cp .env.example .env
```

Открыть `.env` и вписать реальные значения (взять из
`C:\Users\Proftex1\MarketplaceGateway\.env` — имена переменных те же
самые, один в один):

```
WB_API_KEY=...              # skazka
WB_API_KEY_MILKY=...
WB_API_KEY_TIMELESS=...

OZON_CLIENT_ID=...          # skazka
OZON_API_KEY=...
OZON_CLIENT_ID_TIMELESS=...
OZON_API_KEY_TIMELESS=...
OZON_CLIENT_ID_MILKY=...
OZON_API_KEY_MILKY=...

SELSUP_API_TOKEN=...

DATABASE_URL=postgres://user:password@host:5432/marketplace_sync
```

`.env` не попадёт в git (он в `.gitignore`) — это ожидаемо, секреты туда
класть безопасно.

## 5. Первый прогон — начать с малого

Не запускайте сразу `sync all` на все три кабинета — лучше проверить
постепенно, чтобы было легче понять, где именно что-то пошло не так.

**Шаг 1 — один источник, один кабинет** (самый быстрый и безопасный тест):

```bash
python -m interface.cli.main sync selsup --account skazka
```

Если всё хорошо — в консоли будет что-то вроде:
```
2026-... INFO === Selsup ===
2026-... INFO   selsup_stocks [skazka]: 123 records
```

**Шаг 2 — проверить, что записалось в базу:**

```bash
python -m interface.cli.main status
```

Покажет последние записи `sync_runs` — источник, кабинет, статус
(`ok`/`error`), сколько записей забрано, текст ошибки если была.

Либо напрямую через `psql`:

```sql
SELECT * FROM selsup_stocks ORDER BY id DESC LIMIT 20;
SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 10;
```

**Шаг 3 — прогнать WB и Ozon по одному кабинету:**

```bash
python -m interface.cli.main sync wb --account skazka
python -m interface.cli.main sync ozon --account skazka
```

Обратите внимание: `wb stocks` (отчёт по остаткам WB) может занять
несколько минут — это асинхронный отчёт на стороне WB, код сам ждёт его
готовности (до ~4 минут), это нормально, не зависание.

**Шаг 4 — если всё ок, прогнать всё сразу:**

```bash
python -m interface.cli.main sync all
```

Это пройдётся по всем трём кабинетам и всем трём источникам. Если один
источник/кабинет упадёт (например, у WB временные проблемы) — остальные
всё равно отработают, ошибка попадёт в `sync_runs.error_message`, процесс
не остановится. Проверить после прогона:

```bash
python -m interface.cli.main status --limit 30
```
и убедиться, что везде `status = ok` (если где-то `error` — читать
`error_message`, разбираться по конкретному источнику).

## 6. Поставить в расписание (Windows Task Scheduler)

По аналогии с уже существующей задачей `NightlyAnalyticsExport`
(запускает `nightly_export.py` в 3:00) — завести отдельную задачу:

- **Программа**: путь к `python.exe` внутри `.venv` (например,
  `C:\...\marketplace_db_sync\.venv\Scripts\python.exe`)
- **Аргументы**: `-m interface.cli.main sync all`
- **Рабочая папка**: корень проекта (`C:\...\marketplace_db_sync`)
- **Время**: любое, отличное от `nightly_export.py` (например, 3:30,
  чтобы не соревноваться за rate limit с уже идущей выгрузкой) — можно и
  то же самое время, оба скрипта независимы и не мешают друг другу, но
  разнести немного безопаснее.
- **Периодичность**: раз в сутки.

Эта новая задача **не трогает и не заменяет** существующую
`NightlyAnalyticsExport` — они работают параллельно, независимо друг от
друга, в одну и ту же Postgres-базу и на тот же Google Drive
соответственно.

## Разовый бэкфилл из Google Drive (после деплоя)

Нужен ключ сервисного аккаунта (`drive_service_account.json`, уже лежит
на сервере у проектов `priemka` / `marketplaces_auto_replies`) — положить
рядом или указать существующий путь. В `.env`:

```
GOOGLE_SERVICE_ACCOUNT_FILE=<путь к drive_service_account.json>
DRIVE_ROOT_FOLDER_ID=1i6pejqBzpThDI8Vxqg6-fqzJQ5ZL2eN8
```

Пробный прогон (по одному свежему файлу каждого типа), затем полный:

```bash
python -m interface.cli.main backfill --max-files 1
python -m interface.cli.main backfill
python -m interface.cli.main refresh-dims
python -m interface.cli.main coverage
```

Полный прогон занимает порядка получаса (около 450 файлов, год движений
Selsup — отдельный файл на 400 тыс. строк). Повторный запуск пропускает уже
загруженные файлы. Бэкфилл надо запускать **один раз, до или сразу после
включения ночного `sync`**: он пишет в те же таблицы, а за один и тот же
день ночной синк и бэкфилл перезаписывают друг друга по ключу, не дублируя.

## История из API: поставки, акции, воронка (после первого деплоя)

Ночной `sync` читает короткие окна, поэтому историю надо загрузить один раз:

```bash
python -m interface.cli.main api-backfill ozon_supplies
python -m interface.cli.main api-backfill wb_supplies
python -m interface.cli.main api-backfill wb_promotions
python -m interface.cli.main backfill --kinds ozon_warehouse_stocks
```

Воронка WB отдаёт 3 запроса в минуту на кабинет, поэтому полугодие занимает
часы. `api-backfill wb_funnel --from 2026-01-01 [--account A]` идёт по дням от
новых к старым и пропускает уже загруженные, так что его можно прерывать и
запускать заново. Три кабинета запускают тремя параллельными процессами (лимит
WB считается на токен) — на сервере это разовая задача планировщика
`MarketplaceDbSyncFunnelBackfill`, логи в `logsunnel_backfill_<кабинет>.log`.

Доставка кода на сервер, если `git pull` на нём не работает: на этой машине
`git bundle create b.bundle <старый_коммит>..master`, `scp` файла на сервер,
затем `git pull --ff-only b.bundle master` в папке проекта.

## Если что-то пошло не так

- `RuntimeError: missing env var ...` — значит забыли вписать какой-то
  ключ в `.env` для этого кабинета/API.
- Ошибка про `DATABASE_URL` / подключение к Postgres — проверить, что
  база доступна с сервера (хост/порт/файрвол) и строка подключения верна.
- 429/5xx от API в логах — это не баг, код сам ждёт и повторяет запрос
  (до 6 попыток с задержкой); если после всех попыток всё равно упало —
  ошибка попадёт в `sync_runs`, стоит просто повторить прогон позже.
- Любая другая ошибка — `status --limit N` и `error_message` в
  `sync_runs` обычно сразу говорят, в каком источнике и кабинете проблема.

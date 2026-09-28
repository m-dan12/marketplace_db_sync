# marketplace_db_sync

Ежедневный сбор аналитики Ozon/Wildberries/SelSup (остатки, заказы,
продажи за 7/30/предыдущие 30 дней) в Postgres — аналог существующего
`MarketplaceGateway/scripts/nightly_export.py` на сервере `ozon-server`,
но пишет в базу данных вместо xlsx-файлов на Google Drive.

Работает на `ozon-server` (100.104.20.51) по расписанию (Windows Task
Scheduler), рядом с существующей автоматизацией — не заменяет и не меняет
`nightly_export.py`.

-- marketplace_db_sync schema. Applied once at first connection (see
-- `priemka`'s convention: a single schema file, not a migration chain).
--
-- Orders/sales: one wide window pulled nightly, UPSERTed by natural key —
-- the 7d/30d/"previous 30d" slices used to be separate xlsx pulls; now
-- they are just `WHERE order_date >= ...` reads against these tables.
--
-- Stocks: a dated snapshot per night, never overwritten — history of daily
-- stock levels is the point (future sell-through-rate calculations need
-- actual in-stock days, not calendar days).

CREATE TABLE IF NOT EXISTS wb_orders (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    srid TEXT NOT NULL,
    order_date DATE NOT NULL,
    last_change_date TIMESTAMPTZ,
    warehouse_name TEXT,
    region_name TEXT,
    supplier_article TEXT,
    nm_id BIGINT,
    barcode TEXT,
    subject TEXT,
    brand TEXT,
    tech_size TEXT,
    total_price NUMERIC,
    discount_percent NUMERIC,
    finished_price NUMERIC,
    price_with_disc NUMERIC,
    is_cancel BOOLEAN NOT NULL DEFAULT FALSE,
    g_number TEXT,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, srid)
);
CREATE INDEX IF NOT EXISTS ix_wb_orders_account_date ON wb_orders (account, order_date);

CREATE TABLE IF NOT EXISTS wb_sales (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    sale_id TEXT NOT NULL,
    sale_date DATE NOT NULL,
    last_change_date TIMESTAMPTZ,
    warehouse_name TEXT,
    region_name TEXT,
    supplier_article TEXT,
    nm_id BIGINT,
    barcode TEXT,
    subject TEXT,
    brand TEXT,
    tech_size TEXT,
    total_price NUMERIC,
    discount_percent NUMERIC,
    spp NUMERIC,
    for_pay NUMERIC,
    finished_price NUMERIC,
    price_with_disc NUMERIC,
    order_type TEXT,
    g_number TEXT,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, sale_id)
);
CREATE INDEX IF NOT EXISTS ix_wb_sales_account_date ON wb_sales (account, sale_date);

CREATE TABLE IF NOT EXISTS wb_stocks (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    nm_id BIGINT NOT NULL,
    vendor_code TEXT,
    barcode TEXT,
    tech_size TEXT,
    volume NUMERIC,
    warehouse_name TEXT NOT NULL,
    quantity NUMERIC NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, snapshot_date, nm_id, barcode, warehouse_name)
);
CREATE INDEX IF NOT EXISTS ix_wb_stocks_account_date ON wb_stocks (account, snapshot_date);

CREATE TABLE IF NOT EXISTS ozon_orders (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    posting_number TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    status TEXT,
    order_date TIMESTAMPTZ,
    source TEXT,
    warehouse_name TEXT,
    city TEXT,
    region TEXT,
    sku BIGINT,
    product_name TEXT,
    quantity INT,
    price NUMERIC,
    currency TEXT,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, posting_number, offer_id)
);
CREATE INDEX IF NOT EXISTS ix_ozon_orders_account_date ON ozon_orders (account, order_date);

CREATE TABLE IF NOT EXISTS ozon_stocks (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    offer_id TEXT NOT NULL,
    product_id BIGINT,
    sku BIGINT,
    stock_type TEXT NOT NULL,
    present INT,
    reserved INT,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, snapshot_date, offer_id, stock_type)
);
CREATE INDEX IF NOT EXISTS ix_ozon_stocks_account_date ON ozon_stocks (account, snapshot_date);

CREATE TABLE IF NOT EXISTS selsup_stocks (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    warehouse_id INT NOT NULL,
    warehouse_name TEXT,
    sku_id BIGINT NOT NULL,
    article TEXT,
    wb_size TEXT,
    ozon_article TEXT,
    cell_name TEXT,
    quantity NUMERIC,
    available_quantity NUMERIC,
    calculated_quantity NUMERIC,
    modify_date TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, snapshot_date, warehouse_id, sku_id)
);
CREATE INDEX IF NOT EXISTS ix_selsup_stocks_account_date ON selsup_stocks (account, snapshot_date);

CREATE TABLE IF NOT EXISTS sync_runs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,  -- 'wb_orders' | 'wb_sales' | 'wb_stocks' | 'ozon_orders' | 'ozon_stocks' | 'selsup_stocks'
    account TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,  -- 'running' | 'ok' | 'error'
    records_fetched INT NOT NULL DEFAULT 0,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS ix_sync_runs_source_account_started ON sync_runs (source, account, started_at DESC);

-- ===========================================================================
-- ML foundation (docs/ML_FOUNDATION_PLAN.md), stages 1-2.
-- ===========================================================================

-- Price snapshots: one dated snapshot per night, never overwritten.
CREATE TABLE IF NOT EXISTS wb_prices (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    nm_id BIGINT NOT NULL,
    vendor_code TEXT,
    tech_size TEXT NOT NULL DEFAULT '',
    size_id BIGINT,
    price NUMERIC,
    discount NUMERIC,
    discounted_price NUMERIC,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, snapshot_date, nm_id, tech_size)
);
CREATE INDEX IF NOT EXISTS ix_wb_prices_account_date ON wb_prices (account, snapshot_date);

CREATE TABLE IF NOT EXISTS ozon_prices (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    offer_id TEXT NOT NULL,
    product_id BIGINT,
    price NUMERIC,
    old_price NUMERIC,
    min_price NUMERIC,
    marketing_seller_price NUMERIC,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, snapshot_date, offer_id)
);
CREATE INDEX IF NOT EXISTS ix_ozon_prices_account_date ON ozon_prices (account, snapshot_date);

-- WB advertising: campaign x product x day, UPSERTed (the last days are
-- re-pulled every night because the statistics settle with a delay).
CREATE TABLE IF NOT EXISTS wb_ad_stats (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    stat_date DATE NOT NULL,
    campaign_id BIGINT NOT NULL,
    nm_id BIGINT NOT NULL,
    campaign_status TEXT,
    views INT,
    clicks INT,
    ctr NUMERIC,
    cpc NUMERIC,
    spend NUMERIC,
    orders INT,
    carts INT,
    avg_position NUMERIC,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, stat_date, campaign_id, nm_id)
);
CREATE INDEX IF NOT EXISTS ix_wb_ad_stats_account_date ON wb_ad_stats (account, stat_date);

-- Selsup warehouse movements (receipts/shipments of "Склад"/"Склад Квант"/FBS).
-- `movement_key` = hash of the row's fields + occurrence index, so the same
-- movement arriving from the API and from an xlsx backfill lands once.
CREATE TABLE IF NOT EXISTS selsup_movements (
    id BIGSERIAL PRIMARY KEY,
    movement_key TEXT NOT NULL UNIQUE,
    movement_type TEXT NOT NULL,
    operation TEXT NOT NULL,
    moved_at TIMESTAMP NOT NULL,
    warehouse_id INT,
    order_id BIGINT,
    order_type TEXT,
    sku_id BIGINT NOT NULL,
    article TEXT,
    product_name TEXT,
    cell_name TEXT,
    quantity NUMERIC NOT NULL,
    user_id BIGINT,
    fetched_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_selsup_movements_moved_at ON selsup_movements (moved_at);
CREATE INDEX IF NOT EXISTS ix_selsup_movements_article ON selsup_movements (article);

-- Article dimension (parsed from the article string, see domain/article.py).
CREATE TABLE IF NOT EXISTS dim_article (
    article TEXT PRIMARY KEY,
    parsed BOOLEAN NOT NULL,
    brand_code TEXT,
    brand_name TEXT,
    design_code TEXT,
    duvet_size_code INT,
    sheet_size_code INT,
    pillow_size_code INT,
    variant TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_dim_article_design ON dim_article (design_code);

-- Bookkeeping of files loaded from the Drive archive by `backfill`.
CREATE TABLE IF NOT EXISTS backfill_files (
    drive_file_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    account TEXT NOT NULL,
    file_name TEXT NOT NULL,
    snapshot_date DATE,
    rows_loaded INT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL
);

-- Stock by place, one row per (account, article, observation date, place).
-- Observation date = the date the snapshot was taken.
--
-- WB's stock report does not list articles that are out of stock at all, so
-- "absent from the stock report" cannot be told apart from "not in the
-- catalogue". The day's catalogue therefore comes from the price snapshots:
-- every catalogued article gets a zero-quantity row, which makes it "listed"
-- (and out of stock when nothing else is found). Ozon's report lists zero
-- rows itself, its price snapshot adds the same kind of catalogue row.
-- Caveat: a catalogue includes archived/inactive cards; filter by articles
-- that ever had stock or orders before reading the out-of-stock flags.
CREATE OR REPLACE VIEW stock_by_place_daily AS
SELECT account, snapshot_date, vendor_code AS article, 'wb'::text AS place,
       SUM(quantity) AS quantity
FROM wb_stocks
WHERE vendor_code IS NOT NULL
  AND warehouse_name NOT LIKE 'В пути%'
  AND warehouse_name <> 'Всего находится на складах'  -- WB's own total row
GROUP BY account, snapshot_date, vendor_code
UNION ALL
SELECT account, snapshot_date, offer_id, 'ozon_' || stock_type, SUM(present)
FROM ozon_stocks
GROUP BY account, snapshot_date, offer_id, stock_type
UNION ALL
SELECT account, snapshot_date, article,
       CASE warehouse_id WHEN 10001 THEN 'selsup_fbs'
                         WHEN 10020 THEN 'selsup_kvant'
                         ELSE 'selsup_other' END,
       SUM(quantity)
FROM selsup_stocks
WHERE article IS NOT NULL
GROUP BY account, snapshot_date, article, warehouse_id
UNION ALL
SELECT DISTINCT account, snapshot_date, vendor_code, 'wb'::text, 0::numeric
FROM wb_prices
WHERE vendor_code IS NOT NULL
UNION ALL
SELECT DISTINCT account, snapshot_date, offer_id, 'ozon_catalog'::text, 0::numeric
FROM ozon_prices;

-- Dates for which a snapshot exists, per source. Prices count: they carry
-- the catalogue that the out-of-stock flags are based on.
CREATE OR REPLACE VIEW stock_days AS
SELECT DISTINCT account, snapshot_date, 'wb'::text AS source FROM wb_stocks
UNION
SELECT DISTINCT account, snapshot_date, 'ozon' FROM ozon_stocks
UNION
SELECT DISTINCT account, snapshot_date, 'selsup' FROM selsup_stocks
UNION
SELECT DISTINCT account, snapshot_date, 'wb_prices' FROM wb_prices
UNION
SELECT DISTINCT account, snapshot_date, 'ozon_prices' FROM ozon_prices;

-- Wide per-article view used for features: stock at WB, Ozon, and the two
-- own warehouses, with out-of-stock flags. `wb_out`/`ozon_out` are true only
-- when the article is listed in that day's snapshot with zero stock.
CREATE OR REPLACE VIEW article_stock_daily AS
SELECT account, article, snapshot_date,
       COALESCE(SUM(quantity) FILTER (WHERE place = 'wb'), 0) AS wb_qty,
       COALESCE(SUM(quantity) FILTER (WHERE place LIKE 'ozon%'), 0) AS ozon_qty,
       COALESCE(SUM(quantity) FILTER (WHERE place = 'selsup_fbs'), 0) AS selsup_fbs_qty,
       COALESCE(SUM(quantity) FILTER (WHERE place = 'selsup_kvant'), 0) AS selsup_kvant_qty,
       BOOL_OR(place = 'wb') AS wb_listed,
       BOOL_OR(place LIKE 'ozon%') AS ozon_listed,
       (BOOL_OR(place = 'wb')
            AND COALESCE(SUM(quantity) FILTER (WHERE place = 'wb'), 0) <= 0) AS wb_out,
       (BOOL_OR(place LIKE 'ozon%')
            AND COALESCE(SUM(quantity) FILTER (WHERE place LIKE 'ozon%'), 0) <= 0) AS ozon_out
FROM stock_by_place_daily
GROUP BY account, article, snapshot_date;

-- Article x day demand (orders, not confirmed sales), both marketplaces.
CREATE OR REPLACE VIEW orders_daily AS
SELECT account, supplier_article AS article, order_date AS day, 'wb'::text AS marketplace,
       COUNT(*) FILTER (WHERE NOT is_cancel) AS units,
       COUNT(*) FILTER (WHERE is_cancel) AS cancelled_units,
       SUM(price_with_disc) FILTER (WHERE NOT is_cancel) AS revenue
FROM wb_orders
WHERE supplier_article IS NOT NULL
GROUP BY account, supplier_article, order_date
UNION ALL
SELECT account, offer_id, (order_date AT TIME ZONE 'Europe/Moscow')::date, 'ozon',
       COALESCE(SUM(quantity) FILTER (WHERE status <> 'cancelled'), 0),
       COALESCE(SUM(quantity) FILTER (WHERE status = 'cancelled'), 0),
       SUM(price * quantity) FILTER (WHERE status <> 'cancelled')
FROM ozon_orders
WHERE order_date IS NOT NULL
GROUP BY account, offer_id, (order_date AT TIME ZONE 'Europe/Moscow')::date;

-- Selsup movements with the account and (when the movement itself has none)
-- the article resolved through the stock snapshots by sku_id.
DROP VIEW IF EXISTS selsup_movements_v;
CREATE VIEW selsup_movements_v AS
SELECT m.id, m.movement_key, m.movement_type, m.operation, m.moved_at, m.warehouse_id,
       m.order_id, m.order_type, m.sku_id, COALESCE(m.article, a.article) AS article,
       m.product_name, m.cell_name, m.quantity, m.user_id, a.account
FROM selsup_movements m
LEFT JOIN (
    SELECT DISTINCT ON (sku_id) sku_id, account, article
    FROM selsup_stocks
    ORDER BY sku_id, snapshot_date DESC
) a ON a.sku_id = m.sku_id;

-- Days without stock over the last 30 days of snapshots (the analyst's
-- "Дни без остатка" columns). Only days that have a snapshot are counted, so
-- a missed sync never turns into a fake out-of-stock day.
CREATE OR REPLACE VIEW out_of_stock_days_30 AS
SELECT account, article,
       COUNT(*) FILTER (WHERE wb_listed) AS wb_listed_days,
       COUNT(*) FILTER (WHERE wb_out) AS wb_out_days,
       COUNT(*) FILTER (WHERE ozon_listed) AS ozon_listed_days,
       COUNT(*) FILTER (WHERE ozon_out) AS ozon_out_days
FROM article_stock_daily
WHERE snapshot_date > (SELECT MAX(snapshot_date) FROM stock_days) - 30
GROUP BY account, article;

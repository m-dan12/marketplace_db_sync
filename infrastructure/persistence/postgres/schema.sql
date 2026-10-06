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
-- wb_qty / ozon_qty are the marketplaces' own warehouses (FBO) only, as in the analyst's sheet;
-- the seller's stock kept on marketplace warehouses is wb_fbs_qty / ozon_fbs_qty.
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
SELECT account, snapshot_date, vendor_code AS article,
       -- 'wb' is the stock in WB's own warehouses (the analyst's "ФБО", the one the sheet counts);
       -- the other warehouses in the report hold the seller's own stock ('wb_fbs').
       CASE WHEN warehouse_name = 'Склад WB РФ' THEN 'wb' ELSE 'wb_fbs' END AS place,
       SUM(quantity) AS quantity
FROM wb_stocks
WHERE vendor_code IS NOT NULL
  AND warehouse_name NOT LIKE 'В пути%'
  AND warehouse_name <> 'Всего находится на складах'  -- WB's own total row
GROUP BY account, snapshot_date, vendor_code, 4
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
       COALESCE(SUM(quantity) FILTER (WHERE place = 'ozon_fbo'), 0) AS ozon_qty,
       COALESCE(SUM(quantity) FILTER (WHERE place = 'selsup_fbs'), 0) AS selsup_fbs_qty,
       COALESCE(SUM(quantity) FILTER (WHERE place = 'selsup_kvant'), 0) AS selsup_kvant_qty,
       BOOL_OR(place = 'wb') AS wb_listed,
       BOOL_OR(place LIKE 'ozon%') AS ozon_listed,
       (BOOL_OR(place = 'wb')
            AND COALESCE(SUM(quantity) FILTER (WHERE place = 'wb'), 0) <= 0) AS wb_out,
       (BOOL_OR(place LIKE 'ozon%')
            AND COALESCE(SUM(quantity) FILTER (WHERE place = 'ozon_fbo'), 0) <= 0) AS ozon_out,
       COALESCE(SUM(quantity) FILTER (WHERE place = 'wb_fbs'), 0) AS wb_fbs_qty,
       COALESCE(SUM(quantity) FILTER (WHERE place = 'ozon_fbs'), 0) AS ozon_fbs_qty
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

-- ===========================================================================
-- Group 1 of the data wishlist: supplies, sales funnel, promotions, Ozon FBO
-- stock by warehouse.
-- ===========================================================================

-- Shipments to marketplace warehouses: the labels for "where do we send the
-- batch" and the stock in transit. One row per supply; its items are replaced
-- wholesale each time the supply is re-read (the content changes until accepted).
CREATE TABLE IF NOT EXISTS supplies (
    id BIGSERIAL PRIMARY KEY,
    marketplace TEXT NOT NULL,
    account TEXT NOT NULL,
    supply_key TEXT NOT NULL,
    order_id TEXT,
    created_at TIMESTAMPTZ,
    planned_date TIMESTAMPTZ,
    fact_date TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    status TEXT,
    warehouse_name TEXT,
    actual_warehouse_name TEXT,
    transit_warehouse_name TEXT,
    is_crossdock BOOLEAN,
    quantity NUMERIC,
    accepted_quantity NUMERIC,
    ready_for_sale_quantity NUMERIC,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (marketplace, account, supply_key)
);
CREATE INDEX IF NOT EXISTS ix_supplies_created ON supplies (marketplace, created_at);

CREATE TABLE IF NOT EXISTS supply_items (
    id BIGSERIAL PRIMARY KEY,
    marketplace TEXT NOT NULL,
    account TEXT NOT NULL,
    supply_key TEXT NOT NULL,
    item_key TEXT NOT NULL,
    article TEXT,
    nm_id BIGINT,
    sku BIGINT,
    barcode TEXT,
    tech_size TEXT,
    quantity NUMERIC,
    accepted_quantity NUMERIC,
    ready_for_sale_quantity NUMERIC,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (marketplace, account, supply_key, item_key)
);
CREATE INDEX IF NOT EXISTS ix_supply_items_article ON supply_items (article);

-- WB sales funnel per product and day (card opens, cart, orders, buyouts,
-- ratings). `wb_funnel_days` remembers which days were loaded (resumable backfill).
CREATE TABLE IF NOT EXISTS wb_funnel_daily (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    day DATE NOT NULL,
    nm_id BIGINT NOT NULL,
    vendor_code TEXT,
    subject_name TEXT,
    open_count INT,
    cart_count INT,
    order_count INT,
    order_sum NUMERIC,
    buyout_count INT,
    buyout_sum NUMERIC,
    cancel_count INT,
    cancel_sum NUMERIC,
    add_to_wishlist INT,
    product_rating NUMERIC,
    feedback_rating NUMERIC,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, day, nm_id)
);
CREATE INDEX IF NOT EXISTS ix_wb_funnel_daily_account_day ON wb_funnel_daily (account, day);

CREATE TABLE IF NOT EXISTS wb_funnel_days (
    account TEXT NOT NULL,
    day DATE NOT NULL,
    rows_loaded INT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account, day)
);

-- Promotions. Ozon's API only lists current/upcoming actions, so rows are
-- never deleted: the history accumulates from the first night on.
CREATE TABLE IF NOT EXISTS promotions (
    id BIGSERIAL PRIMARY KEY,
    marketplace TEXT NOT NULL,
    account TEXT NOT NULL,
    promo_id TEXT NOT NULL,
    name TEXT,
    promo_type TEXT,
    start_at TIMESTAMPTZ,
    end_at TIMESTAMPTZ,
    description TEXT,
    potential_count INT,
    participating_count INT,
    discount_type TEXT,
    discount_value NUMERIC,
    first_seen_date DATE NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (marketplace, account, promo_id)
);

CREATE TABLE IF NOT EXISTS promotion_items (
    id BIGSERIAL PRIMARY KEY,
    marketplace TEXT NOT NULL,
    account TEXT NOT NULL,
    promo_id TEXT NOT NULL,
    item_id BIGINT NOT NULL,  -- WB nm_id / Ozon product_id
    in_action BOOLEAN NOT NULL,
    price NUMERIC,
    plan_price NUMERIC,
    discount NUMERIC,
    plan_discount NUMERIC,
    stock NUMERIC,
    first_seen_date DATE NOT NULL,
    last_seen_date DATE NOT NULL,
    UNIQUE (marketplace, account, promo_id, item_id)
);
CREATE INDEX IF NOT EXISTS ix_promotion_items_item ON promotion_items (marketplace, account, item_id);

-- Ozon FBO stock by warehouse (report "Остатки по складам FBO"), daily snapshot.
CREATE TABLE IF NOT EXISTS ozon_warehouse_stocks (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    offer_id TEXT NOT NULL,
    sku BIGINT,
    product_name TEXT,
    warehouse_name TEXT NOT NULL,
    free_to_sell INT,
    reserved INT,
    promised INT,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account, snapshot_date, offer_id, warehouse_name)
);
CREATE INDEX IF NOT EXISTS ix_ozon_wh_stocks_account_date ON ozon_warehouse_stocks (account, snapshot_date);

-- Supply content together with its header (article = WB vendorCode / Ozon offer_id).
CREATE OR REPLACE VIEW supply_items_v AS
SELECT s.marketplace, s.account, s.supply_key, s.order_id, s.created_at, s.planned_date,
       s.fact_date, s.status, s.warehouse_name, s.actual_warehouse_name, s.is_crossdock,
       i.article, i.nm_id, i.sku, i.barcode, i.tech_size,
       i.quantity, i.accepted_quantity, i.ready_for_sale_quantity
FROM supplies s
JOIN supply_items i USING (marketplace, account, supply_key);

-- Promotion participation with the article resolved (WB: nm_id via the price
-- snapshots; Ozon: product_id via the stock snapshots).
DROP VIEW IF EXISTS promotion_items_v;
CREATE VIEW promotion_items_v AS
SELECT p.marketplace, p.account, p.promo_id, p.name, p.promo_type, p.start_at, p.end_at,
       i.item_id, COALESCE(w.vendor_code, o.offer_id) AS article, i.in_action, i.price,
       i.plan_price, i.discount, i.plan_discount, i.stock, i.first_seen_date, i.last_seen_date
FROM promotions p
JOIN promotion_items i USING (marketplace, account, promo_id)
LEFT JOIN (SELECT DISTINCT ON (account, nm_id) account, nm_id, vendor_code
           FROM wb_prices WHERE vendor_code IS NOT NULL
           ORDER BY account, nm_id, snapshot_date DESC) w
       ON p.marketplace = 'wb' AND w.account = i.account AND w.nm_id = i.item_id
LEFT JOIN (SELECT DISTINCT ON (account, product_id) account, product_id, offer_id
           FROM ozon_stocks WHERE product_id IS NOT NULL
           ORDER BY account, product_id, snapshot_date DESC) o
       ON p.marketplace = 'ozon' AND o.account = i.account AND o.product_id = i.item_id;

-- Daily funnel conversion per product (the demand-side explanation of sales).
CREATE OR REPLACE VIEW wb_funnel_rates_v AS
SELECT account, day, nm_id, vendor_code, open_count, cart_count, order_count, buyout_count,
       order_count::numeric / NULLIF(open_count, 0) AS open_to_order,
       cart_count::numeric / NULLIF(open_count, 0) AS open_to_cart,
       order_count::numeric / NULLIF(cart_count, 0) AS cart_to_order,
       buyout_count::numeric / NULLIF(order_count, 0) AS order_to_buyout
FROM wb_funnel_daily;

-- ---------------------------------------------------------------------------
-- Production and planning sheets (Google Sheets kept by hand).
-- ---------------------------------------------------------------------------

-- The production table, one row per article and region inside a task. Re-read
-- in full every night; a row that disappears from the sheet is soft-deleted
-- (plans that were revised stay visible), a changed status / fact is logged.
CREATE TABLE IF NOT EXISTS production_lines (
    row_key TEXT PRIMARY KEY,
    sheet_row INT,
    article TEXT NOT NULL,
    quantity INT,
    region TEXT,
    order_text TEXT,
    size_text TEXT,
    meters NUMERIC(12, 3),
    meters2 NUMERIC(12, 3),
    week_number INT,
    direction TEXT,
    task_total INT,
    task_key TEXT,
    task_quantity INT,
    fact_quantity INT,
    fact_ship_date DATE,
    fact_ship_raw TEXT,
    fact_accept_date DATE,
    fact_accept_raw TEXT,
    status TEXT,
    status_group TEXT,
    week_start DATE,
    week_end DATE,
    receipt_no TEXT,
    brand TEXT,
    workshop TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_production_lines_article ON production_lines (article);
CREATE INDEX IF NOT EXISTS ix_production_lines_week ON production_lines (week_start);
CREATE INDEX IF NOT EXISTS ix_production_lines_task ON production_lines (task_key);

-- What changed in a tracked field (status, fact quantity/dates, receipt number)
-- or when a row appeared / disappeared. before/after hold the tracked fields.
CREATE TABLE IF NOT EXISTS production_line_log (
    id BIGSERIAL PRIMARY KEY,
    row_key TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    event TEXT NOT NULL,  -- 'changed' | 'deleted' | 'restored'
    before JSONB,
    after JSONB
);
CREATE INDEX IF NOT EXISTS ix_production_line_log_row ON production_line_log (row_key, changed_at);

-- Current lines with the kind of work: sewing, a transfer between our warehouse
-- and a marketplace, or a "подсортировка" (re-sorting stock already made).
-- (the lead-time views further down depend on it)
DROP VIEW IF EXISTS lead_times_by_workshop_v;
DROP VIEW IF EXISTS lead_times_v;
DROP VIEW IF EXISTS production_lines_v;
CREATE VIEW production_lines_v AS
SELECT p.*,
       CASE WHEN p.order_text ILIKE 'перемещение%' THEN 'transfer'
            WHEN p.order_text ILIKE 'подсортировка%' THEN 'resort'
            ELSE 'sewing' END AS kind
FROM production_lines p
WHERE p.deleted_at IS NULL;

-- Packing multiple per size key ('/6-17-17/'): the planning formula rounds the
-- need up to `quant` ("Финальное V5").
CREATE TABLE IF NOT EXISTS quant_multiples (
    size_key TEXT PRIMARY KEY,
    name TEXT,
    volume_liters NUMERIC,
    fits_in_box INT,
    calculated_in_box INT,
    desired_count INT,
    final_count INT,
    final_v4 INT,
    quant INT,
    fetched_at TIMESTAMPTZ NOT NULL
);

-- Fabric consumption per article (planning sheet 'артикулы').
CREATE TABLE IF NOT EXISTS article_specs (
    article TEXT PRIMARY KEY,
    brand_name TEXT,
    fabric_no_1 TEXT,
    fabric_no_2 TEXT,
    meters_per_item_1 NUMERIC,
    meters_per_item_2 NUMERIC,
    purpose TEXT,
    size_text TEXT,
    fetched_at TIMESTAMPTZ NOT NULL
);

-- Sewing lead times per line: task week start -> shipped from the workshop -> accepted at
-- the warehouse. A line is `is_valid` when both dates exist, are in that order and the whole
-- way took at most 120 days (the sheet has typos in dates; ~1% of lines fail this).
CREATE VIEW lead_times_v AS
SELECT p.row_key, p.article, p.workshop, p.brand, p.direction, p.region, s.purpose AS category,
       p.quantity, p.fact_quantity, p.week_start, p.fact_ship_date AS ship_date,
       p.fact_accept_date AS accept_date,
       p.fact_ship_date - p.week_start AS days_to_ship,
       p.fact_accept_date - p.fact_ship_date AS days_ship_to_accept,
       p.fact_accept_date - p.week_start AS days_total,
       COALESCE(p.fact_ship_date >= p.week_start
                AND p.fact_accept_date >= p.fact_ship_date
                AND p.fact_accept_date - p.week_start <= 120, FALSE) AS is_valid
FROM production_lines_v p
LEFT JOIN article_specs s ON s.article = p.article
WHERE p.kind = 'sewing' AND p.week_start IS NOT NULL
  AND p.fact_ship_date IS NOT NULL AND p.fact_accept_date IS NOT NULL;

-- What the planning formula hard-codes as 28 days, measured: per workshop and brand over lines
-- accepted in the last 180 days. Pieces are weighted by nothing — one line, one observation.
CREATE VIEW lead_times_by_workshop_v AS
SELECT workshop, brand, COUNT(*) AS lines, SUM(quantity) AS pieces,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY days_total) AS median_days,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY days_total) AS p90_days,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY days_to_ship) AS median_days_to_ship,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY days_ship_to_accept) AS median_days_ship_to_accept
FROM lead_times_v
WHERE is_valid AND accept_date >= CURRENT_DATE - 180
GROUP BY workshop, brand;


CREATE TABLE IF NOT EXISTS dim_fabric (
    fabric_no TEXT PRIMARY KEY,
    material TEXT,
    price_category TEXT,
    roll_length_m NUMERIC,
    roll_width_cm NUMERIC,
    audience TEXT,
    color TEXT,
    pattern TEXT,
    weave TEXT,
    short_name TEXT,
    supplier_article TEXT,
    supplier_code TEXT,
    supplier TEXT,
    products TEXT,
    fetched_at TIMESTAMPTZ NOT NULL
);

-- Fabric available at suppliers, a snapshot per day (the sheet only shows today).
CREATE TABLE IF NOT EXISTS fabric_stock (
    snapshot_date DATE NOT NULL,
    fabric_no TEXT NOT NULL,
    supplier TEXT NOT NULL DEFAULT '',
    quantity_m NUMERIC,
    name TEXT,
    brand TEXT,
    fetched_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (snapshot_date, fabric_no, supplier)
);

-- Nightly output of the analyst's formula (domain/baseline.py): the baseline
-- any model has to beat, and later a source of labels. `inputs` keeps every
-- number the calculation saw, so a row can be re-checked at any time.
CREATE TABLE IF NOT EXISTS baseline_recommendation (
    as_of DATE NOT NULL,
    article TEXT NOT NULL,
    category TEXT,
    quant NUMERIC,
    wb_speed NUMERIC,
    ozon_speed NUMERIC,
    wb_need NUMERIC,
    ozon_need NUMERIC,
    need NUMERIC,
    need_quants NUMERIC,
    inputs JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (as_of, article)
);

ALTER TABLE ozon_prices ADD COLUMN IF NOT EXISTS net_price NUMERIC;

-- Margin model of the pricing workbook: cost and price limits per product model,
-- one snapshot per day (the fabric prices behind the cost change over time).
CREATE TABLE IF NOT EXISTS cost_models (
    snapshot_date DATE NOT NULL,
    model_key TEXT NOT NULL,
    fabric_price NUMERIC,
    price_type TEXT,
    base_price NUMERIC,
    cost_total NUMERIC,
    ozon_limit_discount NUMERIC,
    wb_max_discount NUMERIC,
    min_price NUMERIC,
    fetched_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (snapshot_date, model_key)
);

-- Every Wildberries card of a cabinet with brand, category, cost of its model and the price
-- range, as the pricing workbook has them now (the latest state; history is in cost_models).
CREATE TABLE IF NOT EXISTS wb_article_pricing (
    account TEXT NOT NULL,
    article TEXT NOT NULL,
    nm_id BIGINT,
    brand TEXT,
    category TEXT,
    model_key TEXT,
    cost NUMERIC,
    base_price NUMERIC,
    range_start NUMERIC,
    range_end NUMERIC,
    limit_price NUMERIC,
    limit_discount NUMERIC,
    launch_discount NUMERIC,
    fetched_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account, article)
);

-- Selsup product data next to the stock: category (e.g. 'Ткани для рукоделия'), brand, name,
-- the model's article (set even when the sku has none, as with fabric and fittings) and cost.
ALTER TABLE selsup_stocks ADD COLUMN IF NOT EXISTS product_name TEXT;
ALTER TABLE selsup_stocks ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE selsup_stocks ADD COLUMN IF NOT EXISTS brand TEXT;
ALTER TABLE selsup_stocks ADD COLUMN IF NOT EXISTS model_article TEXT;
ALTER TABLE selsup_stocks ADD COLUMN IF NOT EXISTS purchase_price NUMERIC;
ALTER TABLE selsup_stocks ADD COLUMN IF NOT EXISTS organization_id BIGINT;

-- Marketplace cards with category, brand and title (the stock and order reports have none of them).
-- The latest state per card; first_seen_at tells when a card appeared.
CREATE TABLE IF NOT EXISTS product_cards (
    marketplace TEXT NOT NULL,
    account TEXT NOT NULL,
    article TEXT NOT NULL,
    external_id BIGINT,
    title TEXT,
    brand TEXT,
    category TEXT,
    category_id BIGINT,
    first_seen_at TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (marketplace, account, article)
);

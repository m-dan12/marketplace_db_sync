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

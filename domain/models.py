"""Domain dataclasses. No external dependencies — plain data, no behavior."""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass(frozen=True)
class WbOrderLine:
    srid: str
    order_date: date
    last_change_date: Optional[datetime]
    warehouse_name: Optional[str]
    region_name: Optional[str]
    supplier_article: Optional[str]
    nm_id: Optional[int]
    barcode: Optional[str]
    subject: Optional[str]
    brand: Optional[str]
    tech_size: Optional[str]
    total_price: Optional[float]
    discount_percent: Optional[float]
    finished_price: Optional[float]
    price_with_disc: Optional[float]
    is_cancel: bool
    g_number: Optional[str]


@dataclass(frozen=True)
class WbSaleLine:
    sale_id: str
    sale_date: date
    last_change_date: Optional[datetime]
    warehouse_name: Optional[str]
    region_name: Optional[str]
    supplier_article: Optional[str]
    nm_id: Optional[int]
    barcode: Optional[str]
    subject: Optional[str]
    brand: Optional[str]
    tech_size: Optional[str]
    total_price: Optional[float]
    discount_percent: Optional[float]
    spp: Optional[float]
    for_pay: Optional[float]
    finished_price: Optional[float]
    price_with_disc: Optional[float]
    order_type: Optional[str]
    g_number: Optional[str]


@dataclass(frozen=True)
class WbStockLine:
    nm_id: int
    vendor_code: Optional[str]
    barcode: Optional[str]
    tech_size: Optional[str]
    volume: Optional[float]
    warehouse_name: str
    quantity: float


@dataclass(frozen=True)
class OzonOrderLine:
    posting_number: str
    offer_id: str
    status: Optional[str]
    order_date: Optional[datetime]
    source: Optional[str]
    warehouse_name: Optional[str]
    city: Optional[str]
    region: Optional[str]
    sku: Optional[int]
    product_name: Optional[str]
    quantity: Optional[int]
    price: Optional[float]
    currency: Optional[str]


@dataclass(frozen=True)
class OzonStockLine:
    offer_id: str
    product_id: Optional[int]
    sku: Optional[int]
    stock_type: str
    present: Optional[int]
    reserved: Optional[int]


@dataclass(frozen=True)
class SelsupStockLine:
    warehouse_id: int
    warehouse_name: Optional[str]
    sku_id: int
    article: Optional[str]
    wb_size: Optional[str]
    ozon_article: Optional[str]
    cell_name: Optional[str]
    quantity: float
    available_quantity: Optional[float]
    calculated_quantity: Optional[float]
    modify_date: Optional[datetime]


@dataclass(frozen=True)
class SyncRunSummary:
    source: str
    account: str
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    records_fetched: int
    error_message: Optional[str]

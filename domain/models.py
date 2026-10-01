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


@dataclass(frozen=True)
class WbPriceLine:
    nm_id: int
    vendor_code: Optional[str]
    tech_size: Optional[str]
    size_id: Optional[int]
    price: Optional[float]
    discount: Optional[float]
    discounted_price: Optional[float]


@dataclass(frozen=True)
class OzonPriceLine:
    offer_id: str
    product_id: Optional[int]
    price: Optional[float]
    old_price: Optional[float]
    min_price: Optional[float]
    marketing_seller_price: Optional[float]


@dataclass(frozen=True)
class WbAdStatLine:
    """One WB advertising campaign x product x day."""
    stat_date: date
    campaign_id: int
    nm_id: int
    campaign_status: Optional[str]
    views: Optional[int]
    clicks: Optional[int]
    ctr: Optional[float]
    cpc: Optional[float]
    spend: Optional[float]
    orders: Optional[int]
    carts: Optional[int]
    avg_position: Optional[float]


@dataclass(frozen=True)
class SelsupMovementLine:
    """One Selsup warehouse movement (receipt or shipment). The account is
    not part of the line: Selsup's history rows carry no organization, it is
    resolved later through `selsup_stocks` (sku_id -> account)."""
    movement_type: str  # 'Приёмка' | 'Отгрузка'
    operation: str
    moved_at: datetime
    warehouse_id: Optional[int]
    order_id: Optional[int]
    order_type: Optional[str]
    sku_id: int
    article: Optional[str]
    product_name: Optional[str]
    cell_name: Optional[str]
    quantity: float
    user_id: Optional[int]


@dataclass(frozen=True)
class SupplyLine:
    """One shipment to a marketplace warehouse (WB FBW supply / Ozon FBO supply)."""
    marketplace: str  # 'wb' | 'ozon'
    supply_key: str
    order_id: Optional[str]  # WB preorderID / Ozon order id
    created_at: Optional[datetime]
    planned_date: Optional[datetime]
    fact_date: Optional[datetime]
    updated_at: Optional[datetime]
    status: Optional[str]
    warehouse_name: Optional[str]
    actual_warehouse_name: Optional[str]
    transit_warehouse_name: Optional[str]
    is_crossdock: Optional[bool]
    quantity: Optional[float]
    accepted_quantity: Optional[float]
    ready_for_sale_quantity: Optional[float]


@dataclass(frozen=True)
class SupplyItemLine:
    item_key: str  # barcode, else sku/article — unique inside one supply
    article: Optional[str]
    nm_id: Optional[int]
    sku: Optional[int]
    barcode: Optional[str]
    tech_size: Optional[str]
    quantity: Optional[float]
    accepted_quantity: Optional[float]
    ready_for_sale_quantity: Optional[float]


@dataclass(frozen=True)
class SupplyBundle:
    supply: SupplyLine
    items: tuple[SupplyItemLine, ...]


@dataclass(frozen=True)
class WbFunnelLine:
    """WB sales funnel for one product on one day (card opens -> cart -> order -> buyout)."""
    day: date
    nm_id: int
    vendor_code: Optional[str]
    subject_name: Optional[str]
    open_count: Optional[int]
    cart_count: Optional[int]
    order_count: Optional[int]
    order_sum: Optional[float]
    buyout_count: Optional[int]
    buyout_sum: Optional[float]
    cancel_count: Optional[int]
    cancel_sum: Optional[float]
    add_to_wishlist: Optional[int]
    product_rating: Optional[float]
    feedback_rating: Optional[float]


@dataclass(frozen=True)
class PromotionLine:
    marketplace: str
    promo_id: str
    name: Optional[str]
    promo_type: Optional[str]
    start_at: Optional[datetime]
    end_at: Optional[datetime]
    description: Optional[str]
    potential_count: Optional[int]
    participating_count: Optional[int]
    discount_type: Optional[str]
    discount_value: Optional[float]


@dataclass(frozen=True)
class PromotionItemLine:
    item_id: int  # WB nm_id / Ozon product_id
    in_action: bool
    price: Optional[float]
    plan_price: Optional[float]
    discount: Optional[float]
    plan_discount: Optional[float]
    stock: Optional[float]


@dataclass(frozen=True)
class PromotionBundle:
    promotion: PromotionLine
    items: tuple[PromotionItemLine, ...]


@dataclass(frozen=True)
class OzonWarehouseStockLine:
    offer_id: str
    sku: Optional[int]
    product_name: Optional[str]
    warehouse_name: str
    free_to_sell: Optional[int]
    reserved: Optional[int]
    promised: Optional[int]

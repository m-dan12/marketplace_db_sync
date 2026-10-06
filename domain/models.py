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
    net_price: Optional[float] = None  # the seller's cost price, as set in the Ozon cabinet


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


@dataclass(frozen=True)
class ProductionLine:
    """One row of the hand-kept production table (an article within a task and
    a distribution region). `fact_*`, `status` and `receipt_no` describe the
    task as a whole and repeat on every row of it. All cleaning is done by
    `domain.production`; the raw date text is kept next to the parsed date."""
    row_key: str
    sheet_row: int
    article: str
    quantity: Optional[int]
    region: Optional[str]
    order_text: str
    size_text: Optional[str]
    meters: Optional[float]
    meters2: Optional[float]
    week_number: Optional[int]
    direction: Optional[str]  # 'wb' | 'ozon' | 'sklad' | 'sklad_kvant'
    task_total: Optional[int]
    task_key: str
    task_quantity: Optional[int]
    fact_quantity: Optional[int]
    fact_ship_date: Optional[date]
    fact_ship_raw: Optional[str]
    fact_accept_date: Optional[date]
    fact_accept_raw: Optional[str]
    status: Optional[str]
    status_group: str
    week_start: Optional[date]
    week_end: Optional[date]
    receipt_no: Optional[str]
    brand: Optional[str]
    workshop: Optional[str]


@dataclass(frozen=True)
class QuantMultipleLine:
    """Packing multiple per size key ('/6-17-17/'): what the planning formula
    rounds the need up to. `quant` is the "Финальное V5" column."""
    size_key: str
    name: Optional[str]
    volume_liters: Optional[float]
    fits_in_box: Optional[int]
    calculated_in_box: Optional[int]
    desired_count: Optional[int]
    final_count: Optional[int]
    final_v4: Optional[int]
    quant: Optional[int]


@dataclass(frozen=True)
class ArticleSpecLine:
    """Fabric consumption of an article from the planning sheet 'артикулы'."""
    article: str
    brand_name: Optional[str]
    fabric_no_1: Optional[str]
    fabric_no_2: Optional[str]
    meters_per_item_1: Optional[float]
    meters_per_item_2: Optional[float]
    purpose: Optional[str]
    size_text: Optional[str]


@dataclass(frozen=True)
class FabricLine:
    fabric_no: str
    material: Optional[str]
    price_category: Optional[str]
    roll_length_m: Optional[float]
    roll_width_cm: Optional[float]
    audience: Optional[str]
    color: Optional[str]
    pattern: Optional[str]
    weave: Optional[str]
    short_name: Optional[str]
    supplier_article: Optional[str]
    supplier_code: Optional[str]
    supplier: Optional[str]
    products: Optional[str]


@dataclass(frozen=True)
class FabricStockLine:
    """Fabric available at a supplier (sheet 'наличие ткани'), one dated snapshot."""
    fabric_no: str
    quantity_m: Optional[float]
    supplier: Optional[str]
    name: Optional[str]
    brand: Optional[str]


@dataclass(frozen=True)
class CostModelLine:
    """One row of the margin model: cost and price limits of a product model
    (size key + fabric type, e.g. '/4-18-26/1 - перкаль 220 с рисунком')."""
    model_key: str
    fabric_price: Optional[float]
    price_type: Optional[str]
    base_price: Optional[float]
    cost_total: Optional[float]  # СЕБЕСТОИМОСТЬ ИТОГО
    ozon_limit_discount: Optional[float]
    wb_max_discount: Optional[float]
    min_price: Optional[float]


@dataclass(frozen=True)
class WbArticlePricingLine:
    """One Wildberries card in a cabinet's price-range sheet: brand and category
    of the card, the cost of its model, base price and the working price range."""
    nm_id: Optional[int]
    article: str
    brand: Optional[str]
    category: Optional[str]
    model_key: Optional[str]
    cost: Optional[float]  # Себестоимость по модели
    base_price: Optional[float]
    range_start: Optional[float]  # working range of the discount
    range_end: Optional[float]
    limit_price: Optional[float]
    limit_discount: Optional[float]
    launch_discount: Optional[float]  # старт новинки

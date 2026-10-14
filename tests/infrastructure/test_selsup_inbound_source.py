"""Selsup inbound orders (receipts at our own warehouse) against a faked HTTP
transport. The payload shapes are the live ones (priemka project, 2026-09-30:
`invoiceNumber` 619 <-> "неделя 37 цех Солях задание 46")."""
import httpx

from infrastructure.sources.selsup import inbound
from infrastructure.sources.selsup.inbound import SelsupInboundSource, parse_inbound_bundle

ORDER = {
    "id": 1325198, "invoiceNumber": 638, "name": "неделя 35 цех Химки 44 , 225,  35 , 314",
    "supplier": {"id": 4, "name": "Химки"}, "status": "FINISHED", "warehouseId": 10020,
    "createdDate": "2026-10-04T09:00:00", "closeDate": "2026-10-04T18:30:00",
}
ITEMS = [
    {"id": 71, "productId": 900, "quantity": 10, "introducedQuantity": 8},
    {"id": 72, "productId": 901, "quantity": 5, "introducedQuantity": 0},
    {"id": 73, "productId": 902, "quantity": 2, "introducedQuantity": 2},  # product without a card
]
PRODUCTS = {900: ("6432/5-0-0/1", "2040000000011"), 901: ("134/4-13-19/1", None)}


def test_inbound_bundle_maps_header_and_lines():
    bundle = parse_inbound_bundle(ORDER, ITEMS, PRODUCTS)
    order = bundle.order
    assert (order.order_id, order.receipt_number, order.status) == (1325198, "638", "FINISHED")
    assert (order.supplier_id, order.supplier_name, order.warehouse_id) == ("4", "Химки", "10020")
    assert order.order_name.startswith("неделя 35 цех Химки")
    assert order.created_at.date().isoformat() == "2026-10-04"
    assert order.closed_at.hour == 18
    assert [(i.line_id, i.article, i.barcode, i.quantity, i.introduced_quantity) for i in bundle.items] == [
        ("71", "6432/5-0-0/1", "2040000000011", 10.0, 8.0),
        ("72", "134/4-13-19/1", None, 5.0, 0.0),
        ("73", None, None, 2.0, 2.0),
    ]


def test_inbound_order_without_supplier_or_invoice_number_keeps_nones():
    bundle = parse_inbound_bundle({"id": 5, "status": "CREATED"}, [], {})
    order = bundle.order
    assert (order.receipt_number, order.supplier_id, order.supplier_name, order.warehouse_id) == (None,) * 4
    assert order.closed_at is None and bundle.items == ()


def _handler(orders_pages, lines_pages, state):
    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        state["calls"].append((path, params))
        if path == "/api/inbound/find":
            return httpx.Response(200, json={"rows": orders_pages[int(params["page"]) - 1], "hasMorePages": False})
        if path == "/api/inbound/fetchOrderProduct":
            return httpx.Response(200, json={"rows": lines_pages[int(params["page"]) - 1]})
        assert path == "/api/product/find"
        ids = [int(v) for v in request.url.params.get_list("ids")]
        rows = [
            {"id": pid, "view": {"wbArticle": PRODUCTS[pid][0]}, "barcodes": [{"barcode": PRODUCTS[pid][1]}]}
            for pid in ids if pid in PRODUCTS
        ]
        return httpx.Response(200, json={"rows": rows})

    return handler


def test_inbound_source_pages_orders_and_lines_and_scopes_by_organization(fake_http, monkeypatch):
    monkeypatch.setattr(inbound, "_PAGE_SIZE", 2)  # a short page ends the paging, not `hasMorePages`
    state = {"calls": []}
    second_order = {**ORDER, "id": 1325199, "invoiceNumber": 639}
    third_order = {**ORDER, "id": 1325200, "invoiceNumber": 640}
    fake_http(inbound, _handler(
        orders_pages=[[ORDER, second_order], [third_order]],
        lines_pages=[ITEMS[:2], ITEMS[2:]],
        state=state,
    ))
    source = SelsupInboundSource(
        "token", {100980: "skazka", 100943: "timeless"}, window_days=60,
        today=__import__("datetime").date(2026, 10, 14), pause_seconds=0,
    )

    bundles = source.fetch("timeless")

    assert [b.order.receipt_number for b in bundles] == ["638", "639", "640"]
    # every order read its lines over two pages (2 + 1 rows)
    assert all(len(b.items) == 3 for b in bundles)
    first_find = next(p for path, p in state["calls"] if path == "/api/inbound/find")
    assert first_find["organizations"] == "100943"  # timeless, not skazka
    assert first_find["type"] == "INCOME"
    assert first_find["dateFrom"] == "2026-08-15T00:00:00Z"  # 60 days back, with the Z suffix


def test_inbound_source_unknown_account_reads_nothing(fake_http):
    state = {"calls": []}
    fake_http(inbound, _handler([[]], [[]], state))
    source = SelsupInboundSource("token", {100980: "skazka"}, pause_seconds=0)

    assert source.fetch("other") == []
    assert state["calls"] == []

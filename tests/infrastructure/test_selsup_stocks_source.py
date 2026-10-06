from infrastructure.sources.selsup import stocks
from infrastructure.sources.selsup.stocks import OTHER_ACCOUNT, SelsupStocksSource


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_rows_of_other_organizations_and_unknown_skus_are_kept_under_other(monkeypatch):
    stock = [
        {"skuId": 1, "quantity": 5, "sku": {"product": {"anyArticle": "A/1-0-0/1"}}},
        {"skuId": 2, "quantity": 80, "sku": {"product": {}}},  # fabric roll of another organization
        {"skuId": 3, "quantity": 7, "sku": {"product": {}}},  # sku unknown to product/find
        {"skuId": 4, "quantity": 0, "sku": {"product": {}}},  # phantom zero stays out
    ]
    products = {"rows": [
        {"id": 1, "organizationId": 100943, "name": "Комплект"},
        {"id": 2, "organizationId": 555, "name": "Ткань PT5926"},
    ]}

    def fake_request(client, method, url, **kwargs):
        return _Response(stock if url.endswith("/stock/all") else products)

    monkeypatch.setattr(stocks, "request_with_retry", fake_request)
    source = SelsupStocksSource("token", [(10001, "Склад")], {100943: "timeless"})

    assert [r.sku_id for r in source.fetch("timeless")] == [1]
    other = source.fetch(OTHER_ACCOUNT)
    assert [(r.sku_id, r.organization_id, r.quantity) for r in other] == [(2, 555, 80), (3, None, 7)]

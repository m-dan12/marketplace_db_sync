STATISTICS_BASE = "https://statistics-api.wildberries.ru"
ANALYTICS_BASE = "https://seller-analytics-api.wildberries.ru"
CONTENT_BASE = "https://content-api.wildberries.ru"


def wb_headers(api_key: str) -> dict[str, str]:
    """WB auth: `Authorization: <api_key>` — no `Bearer` prefix."""
    return {"Authorization": api_key}

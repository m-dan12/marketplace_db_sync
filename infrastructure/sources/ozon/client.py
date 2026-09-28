OZON_BASE = "https://api-seller.ozon.ru"


def ozon_headers(client_id: str, api_key: str) -> dict[str, str]:
    return {"Client-Id": client_id, "Api-Key": api_key}

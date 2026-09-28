"""Cabinet configuration: account key -> names of the environment variables
holding its credentials (never the secret values themselves — those are
only read from `os.environ` at call time, in the resolver functions below).
"""
from __future__ import annotations

import os

ACCOUNTS: dict[str, dict[str, str]] = {
    "skazka": {
        "wb_api_key_env": "WB_API_KEY",
        "ozon_client_id_env": "OZON_CLIENT_ID",
        "ozon_api_key_env": "OZON_API_KEY",
    },
    "milky_garden": {
        "wb_api_key_env": "WB_API_KEY_MILKY",
        "ozon_client_id_env": "OZON_CLIENT_ID_MILKY",
        "ozon_api_key_env": "OZON_API_KEY_MILKY",
    },
    "timeless": {
        "wb_api_key_env": "WB_API_KEY_TIMELESS",
        "ozon_client_id_env": "OZON_CLIENT_ID_TIMELESS",
        "ozon_api_key_env": "OZON_API_KEY_TIMELESS",
    },
}

# SelSup: one token for all cabinets — the cabinet is determined by
# organizationId in the response data, not by the token.
SELSUP_API_TOKEN_ENV = "SELSUP_API_TOKEN"

SELSUP_WAREHOUSES: list[tuple[int, str]] = [
    (10001, "FBS"),
    (10020, "Kvant"),
    (10023, "Технический"),
    (10016, "Фурнитура Профтекс"),
    (10018, "Фурнитура ИП"),
    (10019, "Возвраты"),
    (10017, "Временное хранение"),
]

SELSUP_ORGANIZATION_IDS: dict[int, str] = {
    100980: "skazka",
    100984: "milky_garden",
    100943: "timeless",
}


def resolve_wb_api_key(account: str) -> str:
    env_name = ACCOUNTS[account]["wb_api_key_env"]
    value = os.environ.get(env_name)
    if not value:
        raise RuntimeError(f"missing env var {env_name} for WB account {account!r}")
    return value


def resolve_ozon_credentials(account: str) -> tuple[str, str]:
    cfg = ACCOUNTS[account]
    client_id_env = cfg["ozon_client_id_env"]
    api_key_env = cfg["ozon_api_key_env"]
    client_id = os.environ.get(client_id_env)
    api_key = os.environ.get(api_key_env)
    if not client_id or not api_key:
        raise RuntimeError(
            f"missing env var {client_id_env}/{api_key_env} for Ozon account {account!r}"
        )
    return client_id, api_key


def resolve_selsup_token() -> str:
    value = os.environ.get(SELSUP_API_TOKEN_ENV)
    if not value:
        raise RuntimeError(f"missing env var {SELSUP_API_TOKEN_ENV}")
    return value

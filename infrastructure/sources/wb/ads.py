from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import httpx

from domain.models import WbAdStatLine
from infrastructure.config.settings import AD_WINDOW_DAYS
from infrastructure.sources.parsing import parse_date
from infrastructure.sources.wb.client import wb_headers
from shared.http_retry import request_with_retry

ADV_BASE = "https://advert-api.wildberries.ru"
_FULLSTATS_CHUNK = 50  # campaigns per request
_FULLSTATS_PAUSE_SECONDS = 65  # the endpoint allows ~1 request/minute

ADV_STATUS_LABELS = {
    4: "готова к запуску",
    7: "завершена",
    8: "отклонена",
    9: "активна",
    11: "приостановлена",
}


class WBAdsSource:
    """Campaign list (`/adv/v1/promotion/count`) then per-day, per-product
    statistics (`/adv/v3/fullstats`) for the last `window_days` days."""

    def __init__(
        self,
        api_key: str,
        window_days: int = AD_WINDOW_DAYS,
        chunk_pause_seconds: float = _FULLSTATS_PAUSE_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._window_days = window_days
        self._chunk_pause_seconds = chunk_pause_seconds

    def fetch(self, account: str) -> list[WbAdStatLine]:
        headers = wb_headers(self._api_key)
        date_to = date.today()
        date_from = date_to - timedelta(days=self._window_days)
        with httpx.Client(timeout=60.0) as client:
            response = request_with_retry(
                client, "GET", f"{ADV_BASE}/adv/v1/promotion/count", headers=headers,
            )
            campaign_ids, status_map = parse_campaigns(response.json())
            stats: list[dict[str, Any]] = []
            for i in range(0, len(campaign_ids), _FULLSTATS_CHUNK):
                chunk = campaign_ids[i : i + _FULLSTATS_CHUNK]
                response = request_with_retry(
                    client, "GET", f"{ADV_BASE}/adv/v3/fullstats", headers=headers,
                    params={
                        "ids": ",".join(map(str, chunk)),
                        "beginDate": date_from.isoformat(),
                        "endDate": date_to.isoformat(),
                    },
                )
                stats.extend(response.json() or [])
                if i + _FULLSTATS_CHUNK < len(campaign_ids):
                    time.sleep(self._chunk_pause_seconds)
        return parse_fullstats(stats, status_map)


def parse_campaigns(payload: dict[str, Any]) -> tuple[list[int], dict[int, str]]:
    ids: list[int] = []
    status_map: dict[int, str] = {}
    for group in payload.get("adverts") or []:
        label = ADV_STATUS_LABELS.get(group.get("status"), str(group.get("status")))
        for item in group.get("advert_list") or []:
            ids.append(item["advertId"])
            status_map[item["advertId"]] = label
    return list(dict.fromkeys(ids)), status_map


def parse_fullstats(stats: list[dict[str, Any]], status_map: dict[int, str]) -> list[WbAdStatLine]:
    lines: list[WbAdStatLine] = []
    for campaign in stats:
        campaign_id = campaign["advertId"]
        positions = {b.get("date"): b.get("avg_position") for b in campaign.get("boosterStats") or []}
        for day in campaign.get("days") or []:
            stat_date = parse_date(day["date"])
            for app in day.get("apps") or []:
                for nm in app.get("nms") or []:
                    lines.append(
                        WbAdStatLine(
                            stat_date=stat_date,
                            campaign_id=campaign_id,
                            nm_id=nm["nmId"],
                            campaign_status=status_map.get(campaign_id),
                            views=nm.get("views"),
                            clicks=nm.get("clicks"),
                            ctr=nm.get("ctr"),
                            cpc=nm.get("cpc"),
                            spend=nm.get("sum"),
                            orders=nm.get("orders"),
                            carts=nm.get("atbs"),
                            avg_position=positions.get(day["date"]),
                        )
                    )
    return lines

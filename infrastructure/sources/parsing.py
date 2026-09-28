"""Small parsing helpers shared by the marketplace-source adapters — API
timestamps come back as ISO-ish strings with varying precision/suffixes."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None

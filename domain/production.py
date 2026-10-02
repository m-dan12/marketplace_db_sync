"""Cleaning rules for the hand-kept production tables: dates typed in a dozen
formats, the same brand / workshop / direction spelled several ways, decimal
commas. Pure functions on plain values — no I/O — so every rule has a test
on a real row."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

# Anything outside this range is a typo (the table has years like "0206").
_DATE_MIN = date(2024, 1, 1)
_DATE_MAX = date(2028, 12, 31)
_GOOGLE_EPOCH = date(1899, 12, 30)

_DATE_TOKEN = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?!\d)")


@dataclass(frozen=True)
class ParsedDates:
    dates: tuple[date, ...]
    invalid: int  # tokens that looked like a date but are not one (typo, out of range)

    @property
    def last(self) -> Optional[date]:
        return max(self.dates) if self.dates else None


def parse_number(value: Any) -> Optional[float]:
    """'7,28' / ' 5 710 ' / 12 -> number; empty or free text -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: Any) -> Optional[int]:
    number = parse_number(value)
    return None if number is None else int(round(number))


def parse_dates(value: Any, reference: Optional[date] = None) -> ParsedDates:
    """All dates found in a cell. A cell may hold several ('12.08, 14.08' —
    partial shipments); a day and month without a year take the year of
    `reference` (the start of the task's week)."""
    if value is None or value == "":
        return ParsedDates((), 0)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            serial = _GOOGLE_EPOCH + timedelta(days=int(value))
        except OverflowError:
            return ParsedDates((), 1)
        return ParsedDates((serial,), 0) if _in_range(serial) else ParsedDates((), 1)

    found: list[date] = []
    invalid = 0
    for match in _DATE_TOKEN.finditer(str(value)):
        day, month, year_text = int(match[1]), int(match[2]), match[3]
        if year_text is None:
            if reference is None:
                invalid += 1
                continue
            year = reference.year
        else:
            year = int(year_text) + (2000 if len(year_text) == 2 else 0)
        try:
            parsed = date(year, month, day)
        except ValueError:
            invalid += 1
            continue
        if year_text is None and reference is not None and parsed < reference - timedelta(days=180):
            try:
                parsed = date(year + 1, month, day)  # '05.01' typed in a December week
            except ValueError:
                invalid += 1
                continue
        if not _in_range(parsed):
            invalid += 1
            continue
        found.append(parsed)
    return ParsedDates(tuple(found), invalid)


def _in_range(value: date) -> bool:
    return _DATE_MIN <= value <= _DATE_MAX


def _squash(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


_BRANDS = {
    "сказка": "Сказка",
    "сказка сатин": "Сказка Сатин",
    "timeless": "Timeless",
    "milky garden": "Milky Garden",
    "анна мария": "Анна Мария",
    "мечта": "Мечта",
    "котикикотики": "КотикиКотики",
}


def normalize_brand(value: Any) -> Optional[str]:
    text = _squash(value)
    if not text:
        return None
    key = text.casefold().rstrip(". ")
    return _BRANDS.get(key, text)


_WORKSHOPS = {"наш цех": "Наш цех", "гав ям": "Гав ЯМ"}


def normalize_workshop(value: Any) -> Optional[str]:
    text = _squash(value)
    if not text:
        return None
    return _WORKSHOPS.get(text.casefold(), text)


_DIRECTIONS = {"вб": "wb", "озон": "ozon", "склад": "sklad", "складквант": "sklad_kvant"}


def normalize_direction(value: Any) -> Optional[str]:
    return _DIRECTIONS.get(_squash(value).casefold())


_REGIONS = {"вб": "ВБ", "озон": "Озон", "склад": "Склад", "складквант": "СкладКвант"}


def normalize_region(value: Any) -> Optional[str]:
    text = _squash(value)
    if not text:
        return None
    return _REGIONS.get(text.casefold(), text)


def normalize_status(value: Any) -> Optional[str]:
    text = _squash(value).casefold()
    return text or None


def status_group(status: Optional[str]) -> str:
    """Coarse stage of a task. `released` and `closed` are the finished ones
    (an assumption of ours, to be confirmed with the analyst)."""
    if not status:
        return "unknown"
    if status == "в производстве":
        return "in_production"
    if status.startswith("в приемке"):
        return "in_acceptance"
    if status == "готово,не вывезено":
        return "ready"
    if status == "выпущено":
        return "released"
    if status == "закрыт":
        return "closed"
    if status == "не определено":
        return "unknown"
    return "other"

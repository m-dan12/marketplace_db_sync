"""Parsing of the company's article string, e.g. `PT5930/6-17-17/1`.

    PT        brand code (PT, MT, AM, ...); no prefix means the Timeless brand
    5930      design code
    6-17-17   size codes: duvet cover - sheet - pillowcase (0 = absent)
    1         variant code at the end (with/without ruffles, zipper/
              buttons/...) — kept as a raw string, the meaning of the
              digits is not (yet) decoded

Articles that do not fit the pattern (`PT6026/сарафан`, ...) are kept as
`parsed=False` with the raw string only — nothing is guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_ARTICLE_RE = re.compile(
    r"^(?P<brand>[A-Za-zА-Яа-я]*)(?P<design>\d+)/(?P<duvet>\d+)-(?P<sheet>\d+)-(?P<pillow>\d+)/(?P<variant>.+)$"
)

# Only what the business stated is encoded: an article without a prefix is
# the Timeless brand. Other prefixes (PT, MY, MT, PC, AM, ...) are kept as
# `brand_code` but NOT mapped to a cabinet — several of them are sub-brands
# sold in more than one cabinet, so the cabinet is taken from where the
# article actually appears in the data, never guessed from the prefix.
BRAND_CODE_TO_NAME = {"": "Timeless"}


@dataclass(frozen=True)
class ParsedArticle:
    article: str
    parsed: bool
    brand_code: Optional[str] = None
    brand_name: Optional[str] = None
    design_code: Optional[str] = None
    duvet_size_code: Optional[int] = None
    sheet_size_code: Optional[int] = None
    pillow_size_code: Optional[int] = None
    variant: Optional[str] = None


def parse_article(article: str) -> ParsedArticle:
    text = (article or "").strip()
    match = _ARTICLE_RE.match(text)
    if not match:
        return ParsedArticle(article=text, parsed=False)
    brand = match["brand"].upper()
    return ParsedArticle(
        article=text,
        parsed=True,
        brand_code=brand,
        brand_name=BRAND_CODE_TO_NAME.get(brand),
        design_code=match["design"],
        duvet_size_code=int(match["duvet"]),
        sheet_size_code=int(match["sheet"]),
        pillow_size_code=int(match["pillow"]),
        variant=match["variant"],
    )

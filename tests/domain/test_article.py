import pytest

from domain.article import parse_article


def test_brand_prefixed_article():
    parsed = parse_article("PT5930/6-17-17/1")
    assert parsed.parsed
    assert (parsed.brand_code, parsed.brand_name) == ("PT", None)
    assert parsed.design_code == "5930"
    assert (parsed.duvet_size_code, parsed.sheet_size_code, parsed.pillow_size_code) == (6, 17, 17)
    assert parsed.variant == "1"


def test_article_without_prefix_is_timeless():
    parsed = parse_article("151/0-0-25/1")
    assert parsed.parsed
    assert parsed.brand_code == ""
    assert parsed.brand_name == "Timeless"
    assert parsed.design_code == "151"
    assert (parsed.duvet_size_code, parsed.sheet_size_code, parsed.pillow_size_code) == (0, 0, 25)


def test_other_prefixes_are_kept_but_not_mapped_to_a_cabinet():
    parsed = parse_article("MY64246425/4-13-26/1")
    assert parsed.brand_code == "MY" and parsed.brand_name is None
    assert parsed.design_code == "64246425"
    assert parsed.sheet_size_code == 13


def test_variant_is_kept_raw():
    assert parse_article("PT1669/50-0-0/0ИП10").variant == "0ИП10"


def test_unknown_brand_prefix_parses_without_brand_name():
    parsed = parse_article("XX12/1-2-3/0")
    assert parsed.parsed
    assert parsed.brand_code == "XX"
    assert parsed.brand_name is None


@pytest.mark.parametrize("raw", ["PT6026/сарафан", "", "garbage", "PT5930/6-17/1"])
def test_non_matching_articles_are_not_guessed(raw):
    parsed = parse_article(raw)
    assert not parsed.parsed
    assert parsed.design_code is None and parsed.brand_name is None

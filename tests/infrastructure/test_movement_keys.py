from datetime import datetime

from domain.models import SelsupMovementLine
from infrastructure.persistence.postgres.repositories_ml import movement_keys


def make_line(**overrides) -> SelsupMovementLine:
    base = dict(
        movement_type="Приёмка", operation="PUT", moved_at=datetime(2026, 9, 29, 14, 41, 25),
        warehouse_id=10001, order_id=5, order_type="INCOME", sku_id=1, article="A", product_name="n",
        cell_name="c", quantity=2.0, user_id=7,
    )
    base.update(overrides)
    return SelsupMovementLine(**base)


def test_identical_rows_get_distinct_keys_but_stable_across_batches():
    batch = [make_line(), make_line(), make_line(sku_id=2)]
    keys = movement_keys(batch)
    assert len(set(keys)) == 3
    assert keys == movement_keys(batch)


def test_key_ignores_article_and_name_so_api_and_xlsx_rows_match():
    assert movement_keys([make_line(article="A")]) == movement_keys(
        [make_line(article=None, product_name=None)]
    )


def test_key_is_insensitive_to_int_vs_float_quantity():
    assert movement_keys([make_line(quantity=2)]) == movement_keys([make_line(quantity=2.0)])


def test_key_changes_with_any_identifying_field():
    assert movement_keys([make_line()]) != movement_keys([make_line(quantity=3.0)])
    assert movement_keys([make_line()]) != movement_keys(
        [make_line(moved_at=datetime(2026, 9, 29, 14, 41, 26))]
    )

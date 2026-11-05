"""规则引擎单测：价格计算只走规则，口径不一致不排序，少于 3 家/3 份不进入比价。"""
from app.rules import compute_price_rules, compute_price_metrics


def test_unit_price_with_tax_and_total():
    items = [{"material": "螺纹钢", "spec": "HRB400 12mm", "unit": "吨", "unit_price": 4000,
              "quantity": 10, "tax_rate": 0.13, "freight": 0, "supplier": "A"}]
    snap = compute_price_metrics(items)
    row = snap["items"][0]
    assert row["price_with_tax"] == round(4000 * 1.13, 4)
    assert row["total"] == round(4000 * 1.13 * 10, 4)
    assert snap["comparable"] is True


def test_inconsistent_units_do_not_rank():
    items = [
        {"material": "水泥", "spec": "P.O 42.5", "unit": "吨", "unit_price": 500, "quantity": 1,
         "tax_rate": 0.13, "freight": 0, "supplier": "A"},
        {"material": "水泥", "spec": "P.O 42.5", "unit": "公斤", "unit_price": 0.55, "quantity": 1,
         "tax_rate": 0.13, "freight": 0, "supplier": "B"},
        {"material": "水泥", "spec": "P.O 42.5", "unit": "吨", "unit_price": 520, "quantity": 1,
         "tax_rate": 0.13, "freight": 0, "supplier": "C"},
    ]
    snap = compute_price_metrics(items)
    assert snap["comparable"] is False
    assert snap["ranking"] == []
    assert any("单位" in r for r in snap["inconsistency_reasons"])


def test_diff_against_reference():
    items = [{"material": "镀锌管", "spec": "DN50", "unit": "米", "unit_price": 20,
              "quantity": 100, "tax_rate": 0.13, "freight": 0, "supplier": "A"}]
    snap = compute_price_metrics(items, reference={"unit_price": 18})
    row = snap["items"][0]
    assert row["reference_price_with_tax"] == round(18 * 1.13, 4)
    assert row["diff"] == round(20 * 1.13 - 18 * 1.13, 4)


def test_compute_price_rules_requires_3_suppliers():
    items = [{"material": "水泥", "spec": "P.O 42.5", "unit": "吨", "unit_price": 500,
              "quantity": 1, "tax_rate": 0.13, "freight": 0, "supplier": "A"},
             {"material": "水泥", "spec": "P.O 42.5", "unit": "吨", "unit_price": 510,
              "quantity": 1, "tax_rate": 0.13, "freight": 0, "supplier": "B"}]
    snap = compute_price_rules(items)
    assert snap["comparable"] is False
    assert snap["ranking"] == []
    assert any("不足" in r for r in snap["inconsistency_reasons"])


def test_compute_price_rules_with_3_suppliers_ok():
    items = [
        {"material": "水泥", "spec": "P.O 42.5", "unit": "吨", "unit_price": 500,
         "quantity": 1, "tax_rate": 0.13, "freight": 0, "supplier": "A"},
        {"material": "水泥", "spec": "P.O 42.5", "unit": "吨", "unit_price": 510,
         "quantity": 1, "tax_rate": 0.13, "freight": 0, "supplier": "B"},
        {"material": "水泥", "spec": "P.O 42.5", "unit": "吨", "unit_price": 505,
         "quantity": 1, "tax_rate": 0.13, "freight": 0, "supplier": "C"},
    ]
    snap = compute_price_rules(items)
    assert snap["comparable"] is True
    assert len(snap["ranking"]) == 3


def test_missing_unit_not_ranked():
    items = [
        {"material": "钢材", "spec": "x", "unit": "吨", "unit_price": 4000, "quantity": 1,
         "tax_rate": 0.13, "freight": 0, "supplier": "A"},
        {"material": "钢材", "spec": "x", "unit": "吨", "unit_price": 4100, "quantity": 1,
         "tax_rate": 0.13, "freight": 0, "supplier": "B"},
        {"material": "钢材", "spec": "x", "unit": "", "unit_price": 4200, "quantity": 1,
         "tax_rate": 0.13, "freight": 0, "supplier": "C"},
    ]
    snap = compute_price_metrics(items)
    missing = [r for r in snap["items"] if r.get("missing_unit")]
    assert len(missing) == 1

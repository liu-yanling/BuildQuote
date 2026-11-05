"""规则引擎：价格计算（单位换算、含税价、总价、差价、比例、排序）。

铁律：所有价格计算只能在这里完成，纯函数、可复现，绝不经过语言模型。
"""
from __future__ import annotations

from typing import Any

RULE_VERSION = "口径规则/v1"

# 基准单位换算系数（到「千克」；体积类按常见密度给占位系数，正式口径由口径规则维护）
UNIT_TO_KG = {
    "kg": 1.0, "公斤": 1.0,
    "t": 1000.0, "吨": 1000.0,
    "g": 0.001, "克": 0.001,
    "m": 1.0, "米": 1.0,        # 管材按「米」计价时不做重量换算，仅同单位可比
    "根": 1.0,                  # 管材按「根」计价时保留原单位，口径不一致时提示
    "m³": 1.0, "立方米": 1.0,
}

_FORMULA_NOTES = {
    "含税单价": "unit_price * (1 + tax_rate)",
    "含税总价": "含税单价 * quantity + freight",
    "差价": "当前含税单价 - 参考含税单价",
    "差价比例": "(当前含税单价 - 参考含税单价) / 参考含税单价",
    "排序": "按可比基准单位下的含税单价升序；口径不一致时不排序，返回差异原因",
}


def _comparable(items: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """判断同一材料下的报价口径是否可比（单位、含税、运费口径一致才可比）。"""
    reasons: list[str] = []
    units = {i.get("unit") for i in items}
    if len(units) > 1:
        reasons.append(f"单位不一致：{sorted(units)}，需先统一口径")
    tax_rates = {round(float(i.get("tax_rate", 0)), 4) for i in items}
    if len(tax_rates) > 1:
        reasons.append(f"税率口径不一致：{sorted(tax_rates)}")
    has_freight = {bool(i.get("freight")) for i in items}
    if len(has_freight) > 1:
        reasons.append("运费口径不一致：部分报价含运费、部分不含")
    return (not reasons), reasons


def compute_price_metrics(items: list[dict[str, Any]], config: dict[str, Any] | None = None,
                          reference: dict[str, Any] | None = None) -> dict[str, Any]:
    """按已确认字段与口径配置计算，返回带溯源的快照。

    items：经人工确认的报价明细（材料/规格/单位/单价/数量/税率/运费/供应商）。
    reference：可选，参考价（历史价/信息价），用于差价与比例。
    """
    config = config or {}
    ref_unit_price = (reference or {}).get("unit_price")
    rows: list[dict[str, Any]] = []
    ok, reasons = _comparable(items)

    for it in items:
        unit = it.get("unit") or ""
        base_coef = UNIT_TO_KG.get(unit)
        unit_price = float(it.get("unit_price", 0) or 0)
        quantity = float(it.get("quantity", 0) or 0)
        tax_rate = float(it.get("tax_rate", 0) or 0)
        freight = float(it.get("freight", 0) or 0)

        # 缺单位：该行不参与比价，标注待补充
        if not unit or base_coef is None:
            rows.append({"supplier": it.get("supplier", ""), "material": it.get("material", ""),
                         "spec": it.get("spec", ""), "unit": unit, "unit_price": unit_price,
                         "quantity": quantity, "tax_rate": tax_rate, "freight": freight,
                         "total": None, "price_with_tax": None, "base_unit_coefficient": None,
                         "comparable": False, "missing_unit": True})
            continue

        price_with_tax = round(unit_price * (1 + tax_rate), 4)
        total = round(price_with_tax * quantity + freight, 4)
        row = {"supplier": it.get("supplier", ""), "material": it.get("material", ""),
               "spec": it.get("spec", ""), "unit": unit, "unit_price": unit_price,
               "quantity": quantity, "tax_rate": tax_rate, "freight": freight,
               "price_with_tax": price_with_tax, "total": total,
               "base_unit_coefficient": base_coef, "comparable": ok}
        if ok and ref_unit_price is not None:
            ref_with_tax = round(float(ref_unit_price) * (1 + tax_rate), 4)
            diff = round(price_with_tax - ref_with_tax, 4)
            ratio = round(diff / ref_with_tax, 4) if ref_with_tax else None
            row["reference_price_with_tax"] = ref_with_tax
            row["diff"] = diff
            row["diff_ratio"] = ratio
        rows.append(row)

    ranking: list[dict[str, Any]] = []
    if ok:
        ranking = sorted(rows, key=lambda r: (r.get("price_with_tax") or 0, r["supplier"]))
        for idx, r in enumerate(ranking, 1):
            r["rank"] = idx

    return {
        "snapshot_id": f"calc-{sum(abs(hash(str(r))) for r in rows) % 10_000_000:07d}",
        "rule_version": config.get("rule_version", RULE_VERSION),
        "comparable": ok,
        "inconsistency_reasons": reasons,
        "items": rows,
        "ranking": ranking,
        "formulas": dict(_FORMULA_NOTES),
    }


def compute_price_rules(items: list[dict[str, Any]], config: dict[str, Any] | None = None,
                        reference: dict[str, Any] | None = None) -> dict[str, Any]:
    """V3 工具入口（compute_price_rules）：前置校验单次询价至少 3 家供应商、3 份及以上报价，
    不满足时不进入比价；满足后走与 compute_price_metrics 相同的规则引擎。"""
    suppliers = {it.get("supplier") for it in items if it.get("supplier")}
    if len(items) < 3 or len(suppliers) < 3:
        return {
            "snapshot_id": None,
            "rule_version": (config or {}).get("rule_version", RULE_VERSION),
            "comparable": False,
            "inconsistency_reasons": [
                f"供应商或报价不足：当前 {len(suppliers)} 家供应商 / {len(items)} 份报价，"
                "少于 3 家/3 份，不进入比价"],
            "items": [], "ranking": [], "formulas": dict(_FORMULA_NOTES),
        }
    return compute_price_metrics(items, config=config, reference=reference)

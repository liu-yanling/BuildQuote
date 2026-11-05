"""成本规则（V4）：科目归一 + 归集对齐 + 黄/红警两级阈值黄金样例断言。"""
from app.cost_rules import (CostSource, build_ledger, compute_cost_monitor,
                            detect_alerts, load_subject_config, normalize_subject)


def test_normalize_subject_alias():
    assert normalize_subject("劳务") == "人工费"
    assert normalize_subject("钢材") == "材料费"
    assert normalize_subject("人工费") == "人工费"
    assert normalize_subject("") == "待确认"


def test_build_ledger_and_yellow_red_alerts():
    budget = [{"subject": "人工费", "budget": 1000.0},
              {"subject": "材料费", "budget": 2000.0}]
    sources = [
        CostSource("进度款", "劳务", 1060.0, "S1"),   # 人工费 6% → 黄警（≥5% 且 <10%）
        CostSource("进度款", "钢材", 2280.0, "S2"),   # 材料费 14% → 红警（≥10%）
    ]
    ledger = build_ledger(budget, sources)
    by = {r.subject: r for r in ledger}
    assert by["人工费"].deviation_ratio == 0.06
    assert by["材料费"].deviation_ratio == 0.14

    alerts = {a.subject: a for a in detect_alerts(ledger, 0.05, 0.10)}
    assert alerts["人工费"].level == "黄警"
    assert alerts["材料费"].level == "红警"


def test_no_alert_below_yellow():
    budget = [{"subject": "人工费", "budget": 1000.0}]
    sources = [CostSource("合同", "人工费", 1040.0, "S1")]  # 4% < 5% 不预警
    out = compute_cost_monitor(budget, sources, 0.05, 0.10)
    assert out["alerts"] == []
    assert out["ledger"][0]["deviation_ratio"] == 0.04


def test_load_subject_config_file():
    cfg = load_subject_config()
    assert cfg["subjects"][0] == "人工费"
    assert "劳务" in cfg["aliases"]["人工费"]

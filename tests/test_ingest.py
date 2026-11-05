"""成本数据接入（V4）：CSV/Excel → CostSource → 归集 + 黄/红警。"""
from pathlib import Path

from app.ingest import (budget_rows_from_excel, make_template, run_monitor,
                        sources_from_csv, sources_from_excel)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_sources_from_csv():
    srcs = sources_from_csv(EXAMPLES / "sample_cost_sources.csv")
    assert len(srcs) == 3
    assert srcs[0].source_type == "进度款"
    assert srcs[0].amount == 800.0


def test_run_monitor_csv():
    out = run_monitor(EXAMPLES / "sample_cost_sources.csv", EXAMPLES / "sample_budget.json")
    assert [a["subject"] for a in out["alerts"]] == ["人工费"]
    assert out["alerts"][0]["level"] == "黄警"
    by = {r["subject"]: r for r in out["ledger"]}
    assert by["人工费"]["deviation"] == 60.0
    assert by["材料费"]["deviation"] == -10.0


def test_make_template_and_read_excel(tmp_path):
    p = tmp_path / "t.xlsx"
    make_template(p)
    assert p.is_file()
    srcs = sources_from_excel(p)
    assert srcs == []          # 模板只含表头，无数据行
    budget = budget_rows_from_excel(p)
    assert len(budget) == 9    # 9 个默认科目

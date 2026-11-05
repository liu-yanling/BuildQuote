"""成本数据接入（V4）：CSV/Excel/JSON 记录 → CostSource 列表 → 归集 + 预警。

- CSV 列：source_type,subject,amount,source_id,note
- Excel：由 --make-template 生成空白模板（成本来源 + 预算 + 填写说明 三张表），也可按同列结构手工填写
- JSON：[{"source_type","subject","amount","source_id","note"}] 或 {"sources":[...]}

用法：
  python -m app.ingest examples/sample_cost_sources.csv --budget examples/sample_budget.json
  python -m app.ingest examples/成本台账模板.xlsx
  python -m app.ingest --make-template examples/成本台账模板.xlsx
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from app import cost_rules
from app.cost_rules import CostSource, compute_cost_monitor, load_subject_config

SRC_COLUMNS = ("source_type", "subject", "amount", "source_id", "note")
BUDGET_COLUMNS = ("subject", "budget")


def sources_from_records(records: list[dict[str, Any]]) -> list[CostSource]:
    return [CostSource(source_type=str(r.get("source_type", "")), subject=str(r.get("subject", "")),
                       amount=float(r.get("amount", 0) or 0), source_id=str(r.get("source_id", "")),
                       note=str(r.get("note", ""))) for r in records]


def sources_from_json(path: str | Path) -> list[CostSource]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("sources", data.get("records", []))
    return sources_from_records(data)


def sources_from_csv(path: str | Path) -> list[CostSource]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return sources_from_records(rows)


def _excel_rows(path: Path, sheet: str, header_skip: bool = True) -> list[tuple[Any, ...]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet in wb.sheetnames else wb[wb.sheetnames[0]]
    out: list[tuple[Any, ...]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if header_skip and i == 0:
            continue
        if not any(v is not None and str(v).strip() != "" for v in row):
            continue
        out.append(row)
    return out


def sources_from_excel(path: str | Path, sheet: str = "成本来源") -> list[CostSource]:
    rows = _excel_rows(Path(path), sheet)
    out: list[dict[str, Any]] = []
    for row in rows:
        src_type, subject, amount, source_id, note = (list(row) + [None] * 5)[:5]
        if src_type or subject or amount is not None:
            out.append({"source_type": src_type, "subject": subject, "amount": amount,
                        "source_id": source_id, "note": note})
    return sources_from_records(out)


def budget_rows_from_json(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("budget_rows", []))
    return list(data)


def budget_rows_from_excel(path: str | Path, sheet: str = "预算") -> list[dict[str, Any]]:
    rows = _excel_rows(Path(path), sheet)
    out: list[dict[str, Any]] = []
    for row in rows:
        subject, budget = (list(row) + [None, None])[:2]
        if subject and budget is not None:
            out.append({"subject": str(subject).strip(), "budget": float(budget)})
    return out


def load_sources(path: str | Path) -> list[CostSource]:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return sources_from_csv(p)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        return sources_from_excel(p)
    return sources_from_json(p)


def load_budget_rows(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        return budget_rows_from_excel(p)
    return budget_rows_from_json(p)


def run_monitor(sources_path: str | Path, budget_path: str | Path | None = None,
                yellow_ratio: float | None = None, red_ratio: float | None = None) -> dict[str, Any]:
    sources = load_sources(sources_path)
    budget_rows = load_budget_rows(budget_path) if budget_path else []
    return compute_cost_monitor(budget_rows, sources,
                                yellow_ratio=yellow_ratio or cost_rules.DEFAULT_YELLOW_RATIO,
                                red_ratio=red_ratio or cost_rules.DEFAULT_RED_RATIO)


def make_template(path: str | Path, subjects: list[str] | None = None) -> Path:
    """生成空白 Excel 模板：成本来源（下拉 source_type/subject）+ 预算 + 填写说明。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.worksheet.datavalidation import DataValidation

    cfg = load_subject_config()
    subjects = list(subjects or cfg["subjects"])

    wb = Workbook()
    ws = wb.active
    ws.title = "成本来源"
    ws.append(list(SRC_COLUMNS))
    for c in ws[1]:
        c.font = Font(bold=True)

    dv_type = DataValidation(type="list", formula1='"合同,进度款,签证"', allow_blank=True)
    dv_subj = DataValidation(type="list", formula1='"' + ",".join(subjects) + '"', allow_blank=True)
    ws.add_data_validation(dv_type)
    ws.add_data_validation(dv_subj)
    dv_type.add("A2:A1000")
    dv_subj.add("B2:B1000")

    ws2 = wb.create_sheet("预算")
    ws2.append(list(BUDGET_COLUMNS))
    for c in ws2[1]:
        c.font = Font(bold=True)
    for s in subjects:
        ws2.append([s, 0])

    ws3 = wb.create_sheet("填写说明")
    ws3.append(["填写说明"])
    ws3.append(["1. 「成本来源」表：source_type 填 合同/进度款/签证；subject 从下拉选成本科目；amount 填金额；source_id 填单据号（脱敏）；note 填备注。"])
    ws3.append(["2. 「预算」表：每行一个科目的预算金额。"])
    ws3.append(["3. 预警规则：偏差率 ≥ 5% 黄警，≥ 10% 红警（可在 .env 调 ALERT_YELLOW_RATIO / ALERT_RED_RATIO）。"])
    ws3.append(["4. 填完后用：python -m app.ingest 你的文件.xlsx"])

    wb.save(path)
    return Path(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sources", nargs="?", help="CSV/XLSX/JSON 成本来源文件")
    ap.add_argument("--budget", help="预算 JSON/XLSX 文件")
    ap.add_argument("--yellow", type=float, default=None)
    ap.add_argument("--red", type=float, default=None)
    ap.add_argument("--make-template", help="生成空白 Excel 模板到指定路径")
    args = ap.parse_args(argv)
    if args.make_template:
        p = make_template(args.make_template)
        print("已生成模板：", p)
        return 0
    if not args.sources:
        ap.print_help()
        return 2
    out = run_monitor(args.sources, args.budget, args.yellow, args.red)
    print(json.dumps({"yellow_ratio": out["yellow_ratio"], "red_ratio": out["red_ratio"],
                      "alerts": out["alerts"], "ledger": out["ledger"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

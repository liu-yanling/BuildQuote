"""江山云墅「合同付款跟踪-月度资金计划」台账解析（真实数据适配）。

把真实 Excel（1/2/3 级口径）解析成合同级 + 科目级成本监控：
- 预算 = 合同预估结算金额（元）
- 实际 = 合同累计已完成产值 / 合同累计已付款[财务实付]
- 超支 = 实际 - 预算，按偏差率黄警(≥5%)/红警(≥10%)

总承包合同（JA-2025-010，1.11 亿）按 2 级总包口径的下游合同拆到科目（+ 管理费、税差）。
只读解析，不改动原始 Excel；数字全部规则计算。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.cost_rules import DEFAULT_RED_RATIO, DEFAULT_YELLOW_RATIO

# 关键列（1 基，与 1/2/3 级表共用同一列结构）
COL = {
    "seq": 1, "stage": 2, "contract_no": 3, "contract_name": 4, "vendor": 5,
    "nature": 8,
    "budget": 9,          # 合同预估结算金额（元）
    "actual_output": 10,  # 合同累计已完成产值
    "paid": 13,           # 合同累计已付款[财务实付]
    "remaining": 44,      # 合同剩余待付款（含质保金）
    "retention": 45,      # 其中质保金（单列）
}

SUBJECT_KEYWORDS = [
    ("人工费", ["劳务", "人工"]),
    ("机械使用费", ["租赁", "塔吊", "机械", "电梯", "升降机"]),
    ("材料费", ["混凝土", "钢材", "材料采购", "商品", "涂料", "砌块", "门窗", "砂浆", "页岩砖", "砖", "保温", "铝合金"]),
    ("分包费", ["分包", "专业施工", "装修", "景观", "防水", "土方", "围挡", "边坡", "桩基", "临电", "用电", "空调", "售楼部", "弱电", "夜景", "人防", "外装饰", "家具", "烟道", "栏杆", "百叶", "线条", "配电"]),
    ("其他直接费", ["监理", "造价咨询", "检测", "勘察", "设计", "测绘", "资质", "资料", "保险", "咨询", "招标", "售电", "智慧工地", "实名制", "扬尘", "施工图审查"]),
    ("现场管理费", ["管理费"]),
    ("公司管理费", ["公司管理"]),
    ("税金", ["税差", "税金"]),
    ("预备费", ["预备", "风险"]),
]

# 人工确认的科目归属（覆盖关键词推断）
MANUAL_SUBJECT = {
    "JA-2025-007": "分包费",       # 临时用电工程
    "JA-2025-049": "分包费",       # EPS 线条工程
    "JA-2025-054": "分包费",       # 设备平台栏杆、百叶
    "JA-2025-058": "机械使用费",   # 施工升降机监控
    "JA-2025-060": "分包费",       # 机制烟道制作安装
    "JA-2025-062": "分包费",       # 红线外配电工程
    "JA-2025-010-07": "材料费",    # 壹术商贸建材采购及租赁（主材为主）
}

SPECIAL_SUBJECTS = {"管理费": "现场管理费", "税差": "税金"}


@dataclass
class ContractRow:
    seq: str
    contract_no: str
    contract_name: str
    vendor: str
    nature: str
    budget: float
    actual_output: float
    paid: float
    remaining: float
    retention: float
    output_deviation: float
    output_deviation_ratio: float | None
    paid_deviation_ratio: float | None
    subject: str


def _f(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def classify_subject(name: str, contract_no: str = "") -> str:
    if contract_no in MANUAL_SUBJECT:
        return MANUAL_SUBJECT[contract_no]
    for subject, kws in SUBJECT_KEYWORDS:
        for kw in kws:
            if kw in name:
                return subject
    return "待确认"


def parse_sheet(ws) -> list[ContractRow]:
    rows: list[ContractRow] = []
    for r in ws.iter_rows(min_row=5, values_only=True):
        seq = r[COL["seq"] - 1]
        if seq is None:
            continue
        try:
            int(str(seq).strip())
        except (ValueError, AttributeError):
            continue
        cno = str(r[COL["contract_no"] - 1] or "").strip()
        name = str(r[COL["contract_name"] - 1] or "").strip()
        if not cno or not name:
            continue
        budget = _f(r[COL["budget"] - 1])
        actual = _f(r[COL["actual_output"] - 1])
        paid = _f(r[COL["paid"] - 1])
        out_dev = round(actual - budget, 2)
        out_ratio = round(out_dev / budget, 4) if budget else None
        paid_ratio = round((paid - budget) / budget, 4) if budget else None
        rows.append(ContractRow(
            seq=str(seq).strip(), contract_no=cno, contract_name=name,
            vendor=str(r[COL["vendor"] - 1] or "").strip(),
            nature=str(r[COL["nature"] - 1] or "").strip(),
            budget=round(budget, 2), actual_output=round(actual, 2), paid=round(paid, 2),
            remaining=round(_f(r[COL["remaining"] - 1]), 2),
            retention=round(_f(r[COL["retention"] - 1]), 2),
            output_deviation=out_dev, output_deviation_ratio=out_ratio,
            paid_deviation_ratio=paid_ratio, subject=classify_subject(name, cno),
        ))
    return rows


def extract_special_rows(ws, labels: set[str]) -> list[dict]:
    """抓无序号的特殊行（管理费/税差）。"""
    out: list[dict] = []
    for row in ws.iter_rows(min_row=5, values_only=True):
        name = str(row[COL["contract_name"] - 1] or "").strip()
        if name in labels:
            out.append({"contract_name": name, "subject": SPECIAL_SUBJECTS[name],
                        "budget": _f(row[COL["budget"] - 1]),
                        "actual_output": _f(row[COL["actual_output"] - 1]),
                        "paid": _f(row[COL["paid"] - 1])})
    return out


def build_subject_ledger(wb) -> tuple[dict[str, dict], list[ContractRow], list[dict]]:
    """完整科目级台账：1 级非总包合同 + 总承包按 2 级下游拆分 + 管理费/税差。"""
    primary = parse_sheet(wb["1级-资金计划（碧晟口径）"])
    non_general = [r for r in primary if r.contract_no != "JA-2025-010"]

    downstream: list[ContractRow] = []
    seen: set[str] = set()
    for r in parse_sheet(wb["2级-资金计划 (总包口径)"]):
        if r.contract_no.startswith("JA-2025-010-") and r.contract_no not in seen:
            seen.add(r.contract_no)
            downstream.append(r)

    special = extract_special_rows(wb["2级-资金计划 (总包口径)"], {"管理费"})
    special += extract_special_rows(wb["3级-资金计划 (壹术商贸口径) "], {"税差"})

    rollup: dict[str, dict] = {}

    def add(subject: str, budget: float, output: float, paid: float, contracts: int = 1) -> None:
        d = rollup.setdefault(subject, {"budget": 0.0, "actual_output": 0.0, "paid": 0.0, "contracts": 0})
        d["budget"] += budget
        d["actual_output"] += output
        d["paid"] += paid
        d["contracts"] += contracts

    for r in non_general:
        add(r.subject, r.budget, r.actual_output, r.paid)
    for r in downstream:
        add(r.subject, r.budget, r.actual_output, r.paid)
    for s in special:
        add(s["subject"], s["budget"], s["actual_output"], s["paid"])

    for s, d in rollup.items():
        d["budget"] = round(d["budget"], 2)
        d["actual_output"] = round(d["actual_output"], 2)
        d["paid"] = round(d["paid"], 2)
        d["deviation_ratio"] = round((d["actual_output"] - d["budget"]) / d["budget"], 4) if d["budget"] else None
    return {s: rollup[s] for s in sorted(rollup)}, downstream, special


def analyze(path: str | Path, yellow: float = DEFAULT_YELLOW_RATIO,
            red: float = DEFAULT_RED_RATIO) -> dict[str, Any]:
    wb = load_workbook(path, read_only=True, data_only=True)
    sheets: dict[str, list[dict]] = {}
    for sn in wb.sheetnames:
        rows = parse_sheet(wb[sn])
        if rows:
            sheets[sn] = [asdict(r) for r in rows]

    primary = sheets.get("1级-资金计划（碧晟口径）", [])
    alerts: list[dict] = []
    for r in primary:
        base = {k: r[k] for k in ("seq", "contract_no", "contract_name", "vendor", "subject",
                                  "budget", "actual_output", "paid", "output_deviation_ratio",
                                  "paid_deviation_ratio")}
        if r["output_deviation"] > 0 and r["output_deviation_ratio"] is not None:
            if r["output_deviation_ratio"] >= red:
                alerts.append({**base, "level": "红警", "metric": "产值超支"})
            elif r["output_deviation_ratio"] >= yellow:
                alerts.append({**base, "level": "黄警", "metric": "产值超支"})
        if r["paid_deviation_ratio"] is not None and r["paid_deviation_ratio"] > 0:
            if r["paid_deviation_ratio"] >= red:
                alerts.append({**base, "level": "红警", "metric": "付款超支"})
            elif r["paid_deviation_ratio"] >= yellow:
                alerts.append({**base, "level": "黄警", "metric": "付款超支"})

    rollup: dict[str, dict] = {}
    for r in primary:
        s = r["subject"]
        d = rollup.setdefault(s, {"budget": 0.0, "actual_output": 0.0, "paid": 0.0, "contracts": 0})
        d["budget"] += r["budget"]
        d["actual_output"] += r["actual_output"]
        d["paid"] += r["paid"]
        d["contracts"] += 1
    for s, d in rollup.items():
        d["budget"] = round(d["budget"], 2)
        d["actual_output"] = round(d["actual_output"], 2)
        d["paid"] = round(d["paid"], 2)
        d["deviation_ratio"] = round((d["actual_output"] - d["budget"]) / d["budget"], 4) if d["budget"] else None
    rollup = {s: rollup[s] for s in sorted(rollup)}

    total_budget = round(sum(r["budget"] for r in primary), 2)
    total_output = round(sum(r["actual_output"] for r in primary), 2)
    total_paid = round(sum(r["paid"] for r in primary), 2)

    ledger, downstream, special = build_subject_ledger(wb)
    return {"sheets": list(sheets.keys()), "primary": primary, "alerts": alerts,
            "rollup": rollup, "subject_ledger": ledger,
            "downstream": [asdict(r) for r in downstream], "special": special,
            "total": {"budget": total_budget, "actual_output": total_output, "paid": total_paid,
                      "output_deviation_ratio": round((total_output - total_budget) / total_budget, 4) if total_budget else None},
            "yellow_ratio": yellow, "red_ratio": red}


def _money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"


def _pct(v) -> str:
    return f"{v:.2%}" if isinstance(v, (int, float)) else "—"


def render_markdown(out: dict) -> str:
    lines = [f"# 江山云墅成本监控复验（{' / '.join(out['sheets'])}）", "",
             f"阈值：黄警 ≥ {out['yellow_ratio']:.0%}、红警 ≥ {out['red_ratio']:.0%}（偏差率 = (实际-预算)/预算）", "",
             "## 一、总量（1 级碧晟口径）", "",
             f"- 合同预估结算金额（预算）：**{_money(out['total']['budget'])} 元**",
             f"- 合同累计已完成产值（实际）：**{_money(out['total']['actual_output'])} 元**",
             f"- 合同累计已付款：**{_money(out['total']['paid'])} 元**",
             f"- 产值偏差率：**{_pct(out['total']['output_deviation_ratio'])}**（负=未超预算，项目早期正常）", "",
             "## 二、完整科目级台账（总承包已拆分到下游科目）", "",
             "| 科目 | 合同数 | 预算(元) | 实际产值(元) | 已付(元) | 偏差率 |",
             "|---|---|---|---|---|---|"]
    for s, d in out["subject_ledger"].items():
        lines.append(f"| {s} | {d['contracts']} | {_money(d['budget'])} | {_money(d['actual_output'])} | {_money(d['paid'])} | {_pct(d['deviation_ratio'])} |")
    lines += ["", "## 三、预警清单", ""]
    if not out["alerts"]:
        lines.append("（无超支/超付预警）")
    else:
        lines.append("| 级别 | 指标 | 科目 | 合同编号 | 合同简称 | 预算(元) | 实际(元) | 偏差率 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for a in out["alerts"]:
            dr = a["output_deviation_ratio"] if a["metric"] == "产值超支" else a["paid_deviation_ratio"]
            actual = a["actual_output"] if a["metric"] == "产值超支" else a["paid"]
            lines.append(f"| {a['level']} | {a['metric']} | {a['subject']} | {a['contract_no']} | {a['contract_name'][:20]} | {_money(a['budget'])} | {_money(actual)} | {_pct(dr)} |")
    lines += ["", "## 四、总承包拆分明细（2 级总包口径 + 管理费/税差）", "",
              "| 合同编号 | 合同全称 | 科目 | 预算(元) | 产值(元) | 已付(元) |",
              "|---|---|---|---|---|---|"]
    for r in out["downstream"]:
        lines.append(f"| {r['contract_no']} | {r['contract_name']} | {r['subject']} | {_money(r['budget'])} | {_money(r['actual_output'])} | {_money(r['paid'])} |")
    for s in out["special"]:
        lines.append(f"| （特殊行） | {s['contract_name']} | {s['subject']} | {_money(s['budget'])} | {_money(s['actual_output'])} | {_money(s['paid'])} |")
    lines += ["", "## 五、合同明细（1 级碧晟口径）", "",
              "| 序号 | 合同编号 | 合同全称 | 施工单位 | 科目 | 预算(元) | 产值(元) | 产值偏差率 | 已付(元) | 付款偏差率 | 剩余待付(元) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["primary"]:
        lines.append(f"| {r['seq']} | {r['contract_no']} | {r['contract_name']} | {r['vendor']} | {r['subject']} | {_money(r['budget'])} | {_money(r['actual_output'])} | {_pct(r['output_deviation_ratio'])} | {_money(r['paid'])} | {_pct(r['paid_deviation_ratio'])} | {_money(r['remaining'])} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="资金计划 Excel 路径")
    ap.add_argument("--yellow", type=float, default=None)
    ap.add_argument("--red", type=float, default=None)
    ap.add_argument("--out", help="输出 Markdown 报告路径")
    args = ap.parse_args(argv)
    out = analyze(args.path, args.yellow or DEFAULT_YELLOW_RATIO, args.red or DEFAULT_RED_RATIO)
    md = render_markdown(out)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print("报告已写入：", args.out)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

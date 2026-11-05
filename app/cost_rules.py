"""成本科目体系 + 归集对齐 + 预警规则（纯函数，绝不经过语言模型）。

红线：预算-实际偏差、偏差率的数字计算只能在这里完成；模型只做科目识别/归集对齐/解释引用。

可配置：
- 科目口径：skills/成本科目/subjects.json（可用 COST_SUBJECTS_FILE 覆盖；缺失则内置默认）。
- 预警阈值：偏差率 ≥ ALERT_YELLOW_RATIO（默认 0.05）黄警；≥ ALERT_RED_RATIO（默认 0.10）红警。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

RULE_VERSION = "成本科目/v2"

# 内置默认科目（小企业会计准则 · 建筑业常用成本科目；subjects.json 缺失时使用）
COST_SUBJECTS = ["人工费", "材料费", "机械使用费", "分包费", "其他直接费", "现场管理费", "公司管理费", "税金", "预备费"]

DEFAULT_YELLOW_RATIO = float(os.getenv("ALERT_YELLOW_RATIO", "0.05") or "0.05")
DEFAULT_RED_RATIO = float(os.getenv("ALERT_RED_RATIO", "0.10") or "0.10")

_ALIASES: dict[str, list[str]] = {
    "人工费": ["人工", "劳务", "劳务费", "工资"],
    "材料费": ["材料", "主材", "辅材", "钢材", "水泥", "管材", "商品混凝土"],
    "机械使用费": ["机械", "机械费", "设备租赁", "租赁费"],
    "分包费": ["分包", "劳务分包", "专业分包", "分包工程"],
    "其他直接费": ["其他直接", "直接费", "措施费", "临设"],
    "现场管理费": ["现场", "间接费", "现场经费", "项目管理费"],
    "公司管理费": ["公司管理", "管理费", "总部", "摊销"],
    "税金": ["税金", "税费", "增值税", "税金及附加"],
    "预备费": ["预备", "风险费", "不可预见费"],
}

_FORMULAS = {
    "偏差": "actual - budget",
    "偏差率": "(actual - budget) / budget",
    "黄警": "偏差率 >= yellow_ratio 且 偏差 > 0",
    "红警": "偏差率 >= red_ratio 且 偏差 > 0",
}

SUBJECTS_FILE = Path(os.getenv("COST_SUBJECTS_FILE") or
                     (Path(__file__).resolve().parent.parent / "skills" / "成本科目" / "subjects.json"))


def load_subject_config(path: str | Path | None = None) -> dict[str, Any]:
    """读取科目口径（subjects / aliases）；缺失回退内置默认。阈值走全局 .env。"""
    p = Path(path) if path else SUBJECTS_FILE
    if not p.is_file():
        return {"subjects": list(COST_SUBJECTS),
                "aliases": {k: list(v) for k, v in _ALIASES.items()}}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {"subjects": list(data.get("subjects", COST_SUBJECTS)),
            "aliases": {k: list(v) for k, v in data.get("aliases", _ALIASES).items()}}


SUBJECT_CONFIG = load_subject_config()


@dataclass
class CostSource:
    """一条归集来源：合同/进度款/签证，带来源定位。"""
    source_type: str            # 合同 / 进度款 / 签证
    subject: str                # 成本科目（可为空，待科目识别）
    amount: float
    source_id: str = ""
    note: str = ""


@dataclass
class LedgerRow:
    """一条成本台账：科目预算 vs 实际（规则归集）。"""
    subject: str
    budget: float
    actual: float
    deviation: float = 0.0
    deviation_ratio: float | None = None
    sources: list[dict[str, Any]] | None = None


@dataclass
class Alert:
    """一条超支预警（数字全部规则计算）。level = 黄警 / 红警。"""
    subject: str
    budget: float
    actual: float
    deviation: float
    deviation_ratio: float
    level: str
    threshold_ratio: float
    sources: list[dict[str, Any]]
    rule_version: str = RULE_VERSION


def normalize_subject(subject: str, config: dict[str, Any] | None = None) -> str:
    """科目归一：别名/空白 → 标准科目；无法识别返回「待确认」。"""
    cfg = config or SUBJECT_CONFIG
    s = (subject or "").strip()
    if s in cfg["subjects"]:
        return s
    for std, aliases in cfg["aliases"].items():
        if s and (s in aliases or any(a in s for a in aliases)):
            return std
    return s or "待确认"


def aggregate_actual(sources: list[CostSource], config: dict[str, Any] | None = None) -> dict[str, list[CostSource]]:
    """按科目归集来源（对齐：同一科目聚合，保留来源明细）。"""
    grouped: dict[str, list[CostSource]] = {}
    for src in sources:
        grouped.setdefault(normalize_subject(src.subject, config), []).append(src)
    return grouped


def build_ledger(budget_rows: list[dict[str, Any]], sources: list[CostSource],
                 config: dict[str, Any] | None = None) -> list[LedgerRow]:
    """预算行 + 归集后的实际成本 → 台账行。缺预算但实际有发生的科目 → 预算记 0。"""
    budget = {normalize_subject(r.get("subject", ""), config): float(r.get("budget", 0) or 0)
              for r in budget_rows}
    grouped = aggregate_actual(sources, config)
    rows: list[LedgerRow] = []
    for s in sorted(set(budget) | set(grouped)):
        b = budget.get(s, 0.0)
        srcs = grouped.get(s, [])
        a = round(sum(x.amount for x in srcs), 2)
        dev = round(a - b, 2)
        ratio = round(dev / b, 4) if b else None
        rows.append(LedgerRow(subject=s, budget=round(b, 2), actual=a, deviation=dev,
                              deviation_ratio=ratio, sources=[asdict(x) for x in srcs]))
    return rows


def detect_alerts(ledger: list[LedgerRow], yellow_ratio: float = DEFAULT_YELLOW_RATIO,
                  red_ratio: float = DEFAULT_RED_RATIO) -> list[Alert]:
    """按科目比对预算 vs 实际：偏差率 ≥ 红警阈值 → 红警；≥ 黄警阈值 → 黄警。"""
    alerts: list[Alert] = []
    for row in ledger:
        ratio = row.deviation_ratio
        if ratio is not None and row.deviation > 0:
            if ratio >= red_ratio:
                level, thr = "红警", red_ratio
            elif ratio >= yellow_ratio:
                level, thr = "黄警", yellow_ratio
            else:
                continue
            alerts.append(Alert(subject=row.subject, budget=row.budget, actual=row.actual,
                                deviation=row.deviation, deviation_ratio=ratio, level=level,
                                threshold_ratio=thr, sources=row.sources or []))
    return alerts


def compute_cost_monitor(budget_rows: list[dict[str, Any]], sources: list[CostSource],
                         yellow_ratio: float = DEFAULT_YELLOW_RATIO,
                         red_ratio: float = DEFAULT_RED_RATIO,
                         subject_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """归集 + 预警一条龙（纯函数，可离线测试）。"""
    ledger = build_ledger(budget_rows, sources, subject_config)
    alerts = detect_alerts(ledger, yellow_ratio, red_ratio)
    return {
        "rule_version": RULE_VERSION,
        "yellow_ratio": yellow_ratio,
        "red_ratio": red_ratio,
        "ledger": [asdict(r) for r in ledger],
        "alerts": [asdict(a) for a in alerts],
        "formulas": dict(_FORMULAS),
    }

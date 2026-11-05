"""领域数据模型：询价单、报价记录、候选字段、计算快照、报告。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class User:
    """内部成员 / 供应商的最小身份（不自建账号体系：成员名单 + 邀请链接）。"""
    user_id: str
    name: str
    role: str                      # 成本经理 / 采购 / 项目经理 / 供应商
    enterprise_id: str
    project_ids: list[str] = field(default_factory=list)


@dataclass
class QuoteItem:
    """一条报价明细（原始内容与标准化结果并存）。"""
    material: str = ""             # 材料名称
    spec: str = ""                 # 规格
    unit: str = ""                 # 单位
    unit_price: float = 0.0        # 单价
    quantity: float = 0.0          # 数量
    tax_rate: float = 0.0          # 税率（小数，0.13 = 13%）
    freight: float = 0.0           # 运费
    supplier: str = ""             # 供应商
    brand: str = ""                # 品牌
    delivery_terms: str = ""       # 交付条件
    source_id: str = ""            # 来源文件/记录 ID
    raw: dict[str, Any] = field(default_factory=dict)   # 原始内容
    confidence: float = 1.0        # 可信度 0~1


@dataclass
class FieldCandidate:
    """parse_quote_document 产出的候选字段（带可信度，需人工确认）。"""
    field: str
    value: Any
    raw_value: str
    source_span: str               # 原文定位
    confidence: float


@dataclass
class CalcSnapshot:
    """compute_price_metrics 产出的计算快照（每个数字带输入与公式溯源）。"""
    snapshot_id: str
    rule_version: str
    items: list[dict[str, Any]]
    ranking: list[dict[str, Any]]  # 可比排序结果
    formulas: dict[str, str]       # 指标名 -> 公式

    def to_dict(self) -> dict:
        return asdict(self)

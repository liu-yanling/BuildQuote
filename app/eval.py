"""评测体系（V4）：预警覆盖率/准确率、召回准确率、回答准确率、引用可追溯率、报告一次可用率。

金标准由人标注；本模块只做指标计算与离线评测（不调用模型）。
"""
from __future__ import annotations

from typing import Any


def _set(xs) -> set:
    return {str(x) for x in xs}


def alert_metrics(predicted: list[str], gold: list[str]) -> dict[str, Any]:
    """预警覆盖率 = 命中/应预警；预警准确率 = 命中/系统预警。"""
    p, g = _set(predicted), _set(gold)
    tp = len(p & g)
    coverage = round(tp / len(g), 4) if g else None
    precision = round(tp / len(p), 4) if p else None
    return {"系统预警数": len(p), "应预警数": len(g), "命中": tp,
            "预警覆盖率": coverage, "预警准确率": precision}


def retrieval_metrics(predicted_ids: list[str], gold_ids: list[str], k: int | None = None) -> dict[str, Any]:
    """召回准确率（precision@k）与召回率（recall@k）。"""
    p = list(predicted_ids)[:k] if k else list(predicted_ids)
    ps, gs = _set(p), _set(gold_ids)
    tp = len(ps & gs)
    precision = round(tp / len(ps), 4) if ps else None
    recall = round(tp / len(gs), 4) if gs else None
    return {"命中": tp, "召回准确率": precision, "召回率": recall}


def citation_rate(answers: list[dict]) -> float:
    """引用可追溯率：带 citation 的答案比例。"""
    if not answers:
        return 0.0
    cited = sum(1 for a in answers if (a or {}).get("citation"))
    return round(cited / len(answers), 4)


def answer_accuracy(predicted: list[str], gold: list[str]) -> float | None:
    ps, gs = _set(predicted), _set(gold)
    return round(len(ps & gs) / len(gs), 4) if gs else None


def run_eval(samples: dict[str, Any]) -> dict[str, Any]:
    """离线评测样例：
    {"alerts": {"predicted": [], "gold": []},
     "retrieval": {"predicted": [], "gold": []},
     "answers": [{"citation": "..."}],
     "answer_accuracy": {"predicted": [], "gold": []}}
    """
    out: dict[str, Any] = {}
    if "alerts" in samples:
        out["预警"] = alert_metrics(samples["alerts"].get("predicted", []),
                                    samples["alerts"].get("gold", []))
    if "retrieval" in samples:
        out["检索"] = retrieval_metrics(samples["retrieval"].get("predicted", []),
                                        samples["retrieval"].get("gold", []))
    if "answers" in samples:
        out["引用可追溯率"] = citation_rate(samples["answers"])
    if "answer_accuracy" in samples:
        out["回答准确率"] = answer_accuracy(samples["answer_accuracy"].get("predicted", []),
                                            samples["answer_accuracy"].get("gold", []))
    return out

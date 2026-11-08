"""评测金标准集测试：扩展集规模/字段完整，供批量检索评测使用。"""
import json
from pathlib import Path

GOLD = Path(__file__).resolve().parent.parent / "evals" / "qa_gold_large.json"


def test_large_gold_set_has_enough_samples():
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    samples = data.get("samples") or []
    # 需求：评测题目扩展到 100–200 道
    assert 100 <= len(samples) <= 200
    assert all(s.get("question") and s.get("contract") for s in samples)


def test_core_gold_set_still_has_12_questions():
    data = json.loads((GOLD.parent / "qa_gold.json").read_text(encoding="utf-8"))
    assert len(data["samples"]) == 12

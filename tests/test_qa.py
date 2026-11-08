"""问答输出格式测试：回答洗成自然语言（去 Markdown），prompt 明确要求自然语言。"""
from app import qa


class _FakeRag:
    def __init__(self, hits=None):
        self.hits = hits if hits is not None else [
            {"source_id": "JA-2025-010-04", "text": "第八条 付款方式：以银行转账或现金方式支付。",
             "meta": {"span": "第八条 付款方式"}, "score": 1.2},
        ]

    def retrieve(self, question, top_k=5):
        return self.hits


class _FakeModel:
    def __init__(self, text):
        self.text = text
        self.seen = None

    def complete(self, system, messages, tools=None, max_tokens=None):
        self.seen = (system, messages)
        return {"text": self.text}


def test_prompt_requires_natural_language():
    assert "自然语言" in qa.QA_SYSTEM
    assert "Markdown" in qa.QA_SYSTEM
    assert "[1][2]" in qa.QA_SYSTEM          # 明确禁止编号式引用
    assert "不要" in qa.QA_SYSTEM and "分点" in qa.QA_SYSTEM


def test_naturalize_strips_markdown_symbols():
    raw = (
        "## 付款方式\n\n"
        "**1. 付款**\n"
        "- 以银行转账或现金方式支付 [3]。\n"
        "- 发票不作为付款依据 [3]。\n\n"
        "---\n\n"
        "### 付款期限\n\n"
        "按《JA-2025-010-04》第八条，具体节点原文未提及。"
    )
    out = qa.naturalize_answer(raw)
    for token in ["##", "**", "- 以银行", "---", "###", "[3]"]:
        assert token not in out
    assert "付款方式" in out
    assert "以银行转账或现金方式支付。" in out
    assert "第八条" in out


def test_naturalize_keeps_plain_text_and_paragraphs():
    raw = "第一段结论。\n\n第二段依据，含《JA-2025-010-04》第八条。"
    out = qa.naturalize_answer(raw)
    assert out == "第一段结论。\n\n第二段依据，含《JA-2025-010-04》第八条。"


def test_ask_returns_naturalized_answer():
    model = _FakeModel(
        "## 结论\n\n"
        "- 按《JA-2025-010-04》第八条，付款方式为银行转账或现金 [1]。\n"
        "- 质保金原文未提及。")
    out = qa.ask("钢材采购合同怎么付款", _FakeRag(), model)
    assert "##" not in out["answer"]
    assert "- " not in out["answer"]
    assert "[1]" not in out["answer"]
    assert "银行转账" in out["answer"]
    assert "JA-2025-010-04" in out["citations"][0]
    # 调用模型时用的 user prompt 也要求自然语言
    system, messages = model.seen
    assert "自然语言" in messages[0]["content"]


def test_ask_without_evidence_is_honest():
    out = qa.ask("无关问题", _FakeRag(hits=[]), _FakeModel("不应被调用"))
    assert "未在已入库的合同文本中检索到相关条款" in out["answer"]
    assert out["citations"] == []

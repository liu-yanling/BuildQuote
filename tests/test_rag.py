"""RAG（V4）：切块 + 混合召回 + 引用治理 + 评测指标。"""
from app.embeddings import Embedder
from app.eval import alert_metrics, citation_rate, retrieval_metrics
from app.rag import RagEngine, build_citation, format_evidence
from app.vectorstore import VectorStore


def _rag(tmp_path):
    return RagEngine(Embedder(None), VectorStore(tmp_path / "v.db"))


def test_chunk_and_ingest_retrieve(tmp_path):
    rag = _rag(tmp_path)
    doc = "建筑工程费由土建和主体结构构成。安装工程费含机电水暖电。设备购置费另计。"
    n = rag.ingest(doc, "src-001")
    assert n >= 1
    hits = rag.retrieve("建筑工程费", top_k=3)
    assert hits
    assert hits[0]["source_id"] == "src-001"
    assert build_citation(hits[0]).startswith("[src-001#")


def test_hybrid_retrieval_citation(tmp_path):
    rag = _rag(tmp_path)
    rag.ingest("钢材含税单价 4200 元每吨，供应商甲。", "quote-1")
    rag.ingest("水泥到厂价 480 元每吨。", "quote-2")
    hits = rag.retrieve("钢材价格", top_k=3)
    assert hits
    ev = format_evidence(hits)
    assert all(e["citation"] for e in ev)


def test_eval_metrics():
    m = alert_metrics(["A", "B", "C"], ["A", "C", "D"])
    assert m["预警覆盖率"] == round(2 / 3, 4)
    assert m["预警准确率"] == round(2 / 3, 4)
    r = retrieval_metrics(["1", "2", "3"], ["2", "3", "9"])
    assert r["命中"] == 2
    assert citation_rate([{"citation": "[a#1]"}, {}]) == 0.5

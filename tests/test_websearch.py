"""联网检索（建筑知识）测试：内置知识兜底 + 合并排序（不依赖外网）。"""
from app import websearch


def test_builtin_results_match():
    out = websearch.builtin_results("混凝土强度等级", top_k=3)
    assert out
    assert all(r["source"] == "内置知识库" for r in out)
    assert any("强度等级" in r["title"] or "强度等级" in r["snippet"] for r in out)


def test_search_falls_back_to_builtin(monkeypatch):
    monkeypatch.setattr(websearch, "search_wiki", lambda q, top_k=3: [])
    monkeypatch.setattr(websearch, "search_baidu", lambda q, top_k=5: [])
    out = websearch.search("钢筋理论重量", top_k=5, model=None, with_answer=False)
    assert out["ok"] is True
    assert out["results"]
    assert any(r["source"] == "内置知识库" for r in out["results"])


def test_search_merges_wiki_first(monkeypatch):
    monkeypatch.setattr(websearch, "search_wiki", lambda q, top_k=3: [
        {"title": "混凝土", "url": "https://zh.wikipedia.org/wiki/混凝土",
         "snippet": "混凝土是由凝胶材料、骨料和水配置成的复合材料。", "source": "维基百科"}])
    monkeypatch.setattr(websearch, "search_baidu", lambda q, top_k=5: [])
    out = websearch.search("混凝土是什么", top_k=5, model=None, with_answer=False)
    assert out["results"]
    assert any(r["source"] == "维基百科" for r in out["results"])


def test_web_system_prompt_requires_sources():
    assert "不得编造" in websearch.WEB_SYSTEM
    assert "[1][2]" in websearch.WEB_SYSTEM

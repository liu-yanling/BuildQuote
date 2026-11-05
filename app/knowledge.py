"""技能加载与知识检索（s07）：skills/ 目录先行、按需展开、BM25 式打分；联网检索公开造价参考价。

知识来源：标准物料表、500 条历史报价、企业合同/制度语料、5~10 个四川公开造价站点（白名单内，每月更新）。
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

# 公开造价参考站点白名单（MVP 联网检索仅限白名单内；未核验许可的站点拒绝接入）
REFERENCE_SITES: dict[str, str] = {
    # 站点名 -> 说明。硬化阶段填入真实已核验站点，并在接入前完成许可核验。
    # "四川工程造价信息网": "https://example.gov.cn/sichuan（示例，待核验许可后启用）",
}


def list_skills() -> list[str]:
    """目录先行：只列技能名，不加载正文。"""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p.parent.name for p in SKILLS_DIR.rglob("SKILL.md"))


def load_skill(name: str) -> str:
    """按需展开：读取某个技能的 SKILL.md 正文。"""
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.is_file():
        raise FileNotFoundError(f"技能不存在：{name}")
    return path.read_text(encoding="utf-8")


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]|\d+(?:\.\d+)?", text.lower())


def _bm25_score(query: str, doc: str, doc_len: float, avg_len: float,
                k1: float = 1.5, b: float = 0.75) -> float:
    q_terms = _tokenize(query)
    doc_terms = _tokenize(doc)
    if not q_terms or not doc_terms:
        return 0.0
    tf = {t: doc_terms.count(t) for t in set(q_terms)}
    score = 0.0
    for t in q_terms:
        f = tf.get(t, 0)
        if f:
            score += (f * (k1 + 1)) / (f + k1 * (1 - b + b * doc_len / avg_len))
    return score


def retrieve_knowledge(query: str, scope: dict[str, Any] | None = None,
                       top_k: int = 5) -> list[dict[str, Any]]:
    """按需检索本地知识片段，返回 {text, source_id, score}。"""
    docs: list[dict[str, Any]] = []
    for skill_name in list_skills():
        path = SKILLS_DIR / skill_name / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?m)^##\s+(.+)\n((?:.|\n)*?)(?=^##\s|\Z)", text):
            title, body = m.group(1).strip(), m.group(2).strip()
            if body:
                docs.append({"text": body, "source_id": f"{skill_name}#{title}",
                             "title": title, "doc": body})
    if not docs:
        return []
    avg_len = sum(len(d["doc"]) for d in docs) / len(docs)
    scored = [{"text": d["text"][:400], "source_id": d["source_id"],
               "score": round(_bm25_score(query, d["doc"], len(d["doc"]), avg_len), 4)}
              for d in docs]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return [s for s in scored if s["score"] > 0][:top_k]


def search_reference_price(query: str, scope: dict[str, Any] | None = None,
                           top_k: int = 5) -> dict[str, Any]:
    """联网检索公开造价参考价（V3）：白名单内站点允许，白名单外拒绝；命中标「参考价，非成交价」。"""
    # 白名单门禁：MVP 白名单为空/未核验时，不发起真实抓取，返回「未接入」说明
    hits: list[dict] = []
    for site, url in REFERENCE_SITES.items():
        if query and query.lower() in site.lower() or not query:
            hits.append({"source": site, "url": url, "price": None,
                         "note": "参考价，非成交价（联网抓取硬化阶段接入）"})
    local = retrieve_knowledge(query, scope=scope, top_k=top_k)
    return {
        "hits": hits,
        "local_hits": local,
        "count": len(hits) + len(local),
        "note": "仅白名单内站点；未核验许可的站点拒绝接入。命中标参考价，非成交价。",
    }

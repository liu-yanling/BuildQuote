"""RAG：条款级切块 + 混合召回（向量 + BM25）+ 合同过滤 + 引用治理。

V4.1 召回增强：
- 按「第X条 / 一、二、 / 付款方式 / 质保金 / 税率」等标题切块，条款成为独立检索单元，引用=条款名。
- 问句里带合同编号或合同名时，先定位到该合同，仅在该合同内召回。
- 融合：向量 0.5 + BM25 0.5，关键词问句由 BM25 主导。
"""
from __future__ import annotations

import re
from typing import Any

from app.embeddings import Embedder
from app.vectorstore import VectorStore, cosine

COLLECTION = "cost_knowledge"

_CLAUSE_HEAD = re.compile(
    r"^(第[一二三四五六七八九十百零0-9]+[条章节款])"
    r"|^[一二三四五六七八九十]+、"
    r"|^(付款方式|支付方式|质保金|保修金|质量保修|税率|增值税|合同价款|合同金额|结算方式|结算|进度款|工程款|工期|违约责任|质量标准|验收|交付|安全生产|安全|保险|税金|发票|争议解决|争议|合同解除|解除|补充协议|其他约定|双方权利义务|履约担保)[：:]"
)

_CONTRACT_NO = re.compile(r"(JA-\d{4}-\d{3}(?:-\d{2})?|GL-\d{4}-\d{3})")

_COMMON_PREFIXES = ["绵阳市盐亭县江山云墅项目", "绵阳市盐亭县云溪地块项目", "绵阳市盐亭县云溪项目",
                    "盐亭县江山云墅项目", "盐亭县云溪地块项目", "盐亭县云溪项目",
                    "绵阳市盐亭县项目", "江山云墅项目", "云溪项目"]


def _tokens(text: str) -> list[str]:
    """中文用 jieba 分词（词级 BM25）；无 jieba 时退回单字切分。"""
    text = (text or "").lower()
    try:
        import jieba  # noqa: F401
        return [w for w in jieba.cut(text) if w.strip() and re.search(r"[A-Za-z0-9\u4e00-\u9fff]", w)]
    except ImportError:
        return re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]|\d+(?:\.\d+)?", text)


def _heading_hit(query: str, span: str) -> bool:
    """查询里的连续 2~4 字是否出现在条款标题里（命中标题的块加权）。"""
    q = re.sub(r"[\s，。？?！!：:、,.]", "", query or "")
    s = re.sub(r"[\s，。？?！!：:、,.]", "", span or "")
    for n in (4, 3, 2):
        for i in range(len(q) - n + 1):
            if q[i:i + n] in s:
                return True
    return False


def _is_heading(line: str) -> bool:
    return bool(_CLAUSE_HEAD.match(line))


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts = re.split(r"(?<=[。；;])", text)
    out, buf = [], ""
    for p in parts:
        if len(buf) + len(p) <= max_chars:
            buf += p
        else:
            if buf:
                out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out


def chunk_text(text: str, source_id: str, max_chars: int = 700) -> list[dict[str, Any]]:
    """条款级切块：以条款标题为边界，超长再按句号切。span=条款标题（供引用）。"""
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[dict[str, Any]] = []
    heading = ""
    body: list[str] = []

    def flush() -> None:
        nonlocal body, heading
        if not body:
            return
        body_text = "\n".join(body).strip()
        if body_text:
            span = heading if heading else f"正文"
            for piece in _split_long((heading + "\n" + body_text).strip() if heading else body_text, max_chars):
                chunks.append({"text": piece, "source_id": source_id, "span": span})
        body = []

    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if _is_heading(s):
            flush()
            heading = s
        else:
            body.append(s)
    flush()
    return chunks


def _bm25(query: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    q = _tokens(query)
    if not q or not docs:
        return []
    doc_tokens = [_tokens(d["text"]) for d in docs]
    avg = (sum(len(t) for t in doc_tokens) / len(doc_tokens)) if doc_tokens else 1.0
    k1, b = 1.5, 0.75
    scored = []
    for d, dt in zip(docs, doc_tokens):
        score = 0.0
        for t in q:
            f = dt.count(t)
            if f:
                score += (f * (k1 + 1)) / (f + k1 * (1 - b + b * len(dt) / avg))
        if score > 0:
            scored.append({**d, "bm25": round(score, 4)})
    scored.sort(key=lambda x: x["bm25"], reverse=True)
    return scored


_GENERIC_BIGRAMS = {"项目", "工程", "合同", "协议", "施工", "服务", "采购", "购销", "分包", "专业",
                  "供应", "安装", "买卖", "咨询", "设备", "材料", "有限", "限公", "公司", "乙方", "甲方"}


def _bigrams(text: str) -> set[str]:
    clean = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
    return {clean[i:i + 2] for i in range(len(clean) - 1)}


def _core_name(fname: str) -> str:
    """从 OCR 文件名提取合同核心名（去掉编号、通用项目前缀、.txt/.pdf、合同/协议尾）。"""
    name = _CONTRACT_NO.sub("", fname).replace(".txt", "").replace(".pdf", "").strip()
    for pre in _COMMON_PREFIXES:
        if name.startswith(pre):
            name = name[len(pre):]
    name = re.sub(r"(合同|协议|书)?$", "", name).strip()
    return name


class RagEngine:
    def __init__(self, embedder: Embedder, store: VectorStore):
        self.embedder = embedder
        self.store = store
        self._catalog: dict[str, str] | None = None

    def ingest(self, text: str, source_id: str, meta: dict[str, Any] | None = None) -> int:
        chunks = chunk_text(text, source_id)
        if not chunks:
            return 0
        vectors = self.embedder.embed([c["text"] for c in chunks])
        for c, v in zip(chunks, vectors):
            self.store.upsert(COLLECTION, c["text"], c["source_id"], v,
                              meta={**(meta or {}), "span": c["span"]})
        return len(chunks)

    def _build_catalog(self) -> dict[str, str]:
        if self._catalog is None:
            cat: dict[str, str] = {}
            for c in self.store.list_chunks(COLLECTION):
                sid = c["source_id"]
                if sid not in cat:
                    cat[sid] = (c.get("meta") or {}).get("file", "")
            self._catalog = cat
        return self._catalog

    def _match_contract(self, query: str) -> str | None:
        m = _CONTRACT_NO.search(query)
        if m:
            return m.group(1)
        q_bg = _bigrams(query)
        best: tuple[str, int] | None = None
        for sid, fname in self._build_catalog().items():
            core = _core_name(fname)
            if not core:
                continue
            bg = _bigrams(core) - _GENERIC_BIGRAMS
            overlap = len(bg & q_bg)
            if overlap and (best is None or overlap > best[1]):
                best = (sid, overlap)
        return best[0] if best else None

    def retrieve(self, query: str, top_k: int = 5, contract: str | None = None) -> list[dict[str, Any]]:
        """混合召回：向量 0.5 + BM25 0.5；命中合同编号/合同名时先过滤到该合同。"""
        vec = self.embedder.embed([query])[0]
        chunks = self.store.list_chunks(COLLECTION)
        cid = contract or self._match_contract(query)
        if cid:
            filtered = [c for c in chunks if c["source_id"] == cid]
            if filtered:
                chunks = filtered

        for c in chunks:
            c["score"] = round(cosine(vec, c["vector"]), 4)
        vec_ranked = sorted(chunks, key=lambda x: x["score"], reverse=True)[:50]
        bm25_map = {h["id"]: h["bm25"] for h in _bm25(query, chunks)}

        merged: dict[str, dict[str, Any]] = {}
        for h in vec_ranked:
            merged[h["id"]] = {**h, "bm25": bm25_map.get(h["id"], 0.0)}
        for h in _bm25(query, chunks)[:50]:
            if h["id"] not in merged:
                merged[h["id"]] = {**h, "score": 0.0, "bm25": h["bm25"]}

        for hid, h in merged.items():
            span = (h.get("meta") or {}).get("span", "")
            heading_bonus = 0.35 if _heading_hit(query, span) else 0.0
            h["fusion"] = round(0.5 * (h.get("score") or 0.0) + 0.5 * (h.get("bm25") or 0.0) + heading_bonus, 4)
        ranked = sorted(merged.values(), key=lambda x: x["fusion"], reverse=True)
        return ranked[:top_k]


def build_citation(chunk: dict[str, Any]) -> str:
    span = (chunk.get("meta") or {}).get("span", "")
    return f"[{chunk.get('source_id')}#{span}]"


def format_evidence(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"source_id": h["source_id"], "span": (h.get("meta") or {}).get("span", ""),
             "score": h.get("fusion", h.get("score", 0.0)), "text": h["text"][:220],
             "citation": build_citation(h)} for h in hits]

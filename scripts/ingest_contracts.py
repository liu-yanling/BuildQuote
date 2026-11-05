"""把 OCR 出的合同文本灌入 RAG 知识库（本地 bge-small-zh 向量）。

source_id 取合同编号（JA-XXXX-XXX / GL-XXXX-XXX），便于引用可追溯。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from app.embeddings import Embedder, LocalModelEmbedding
from app.rag import RagEngine
from app.vectorstore import VectorStore

ROOT = Path(__file__).resolve().parent.parent
OCR_DIR = ROOT / "inbox" / "合同OCR文本"
VECTOR_DB = ROOT / "data" / "buildquote_vectors.db"


def source_id_from_name(name: str) -> str:
    m = re.search(r"(JA-\d{4}-\d{3}(?:-\d{2})?|GL-\d{4}-\d{3})", name)
    return m.group(1) if m else name.replace(".txt", "")


def main() -> int:
    embedder = Embedder(LocalModelEmbedding("BAAI/bge-small-zh-v1.5"))
    store = VectorStore(VECTOR_DB)
    from app.rag import COLLECTION

    store.clear(COLLECTION)  # 重新灌入（切块策略变更，避免新旧块混在一起）
    rag = RagEngine(embedder, store)
    t0 = time.time()
    total_chunks = 0
    files = 0
    for p in sorted(OCR_DIR.glob("*.txt")):
        text = p.read_text(encoding="utf-8")
        if len(text.strip()) < 50:
            continue
        sid = source_id_from_name(p.name)
        n = rag.ingest(text, source_id=sid, meta={"file": p.name})
        total_chunks += n
        files += 1
        print(f"ingest {sid:<16} {n:>4} 块  {p.name[:38]}", flush=True)
    print(f"\n完成：{files} 份合同、{total_chunks} 块，用时 {time.time()-t0:.0f}s，"
          f"embedding degraded={embedder.degraded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

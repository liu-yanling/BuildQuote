"""SQLite 向量存储：chunk + embedding 余弦相似度检索（不依赖 numpy）。"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from pathlib import Path
from typing import Any


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class VectorStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init(self) -> None:
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS chunks(
                id TEXT PRIMARY KEY, collection TEXT NOT NULL, text TEXT NOT NULL,
                source_id TEXT NOT NULL, meta TEXT NOT NULL, vector TEXT NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_col ON chunks(collection)")

    def upsert(self, collection: str, text: str, source_id: str, vector: list[float],
               meta: dict[str, Any] | None = None, chunk_id: str | None = None) -> str:
        chunk_id = chunk_id or uuid.uuid4().hex
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO chunks(id, collection, text, source_id, meta, vector) "
                      "VALUES(?,?,?,?,?,?)",
                      (chunk_id, collection, text, source_id,
                       json.dumps(meta or {}, ensure_ascii=False), json.dumps(vector)))
        return chunk_id

    def list_chunks(self, collection: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT id, text, source_id, meta, vector FROM chunks WHERE collection=?",
                             (collection,)).fetchall()
        out: list[dict[str, Any]] = []
        for cid, text, source_id, meta, vec in rows:
            out.append({"id": cid, "text": text, "source_id": source_id,
                        "meta": json.loads(meta), "vector": json.loads(vec)})
        return out

    def search(self, collection: str, vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        scored = []
        for h in self.list_chunks(collection):
            scored.append({**h, "score": round(cosine(vector, h["vector"]), 4)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def count(self, collection: str) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM chunks WHERE collection=?", (collection,)).fetchone()[0]

    def clear(self, collection: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM chunks WHERE collection=?", (collection,))

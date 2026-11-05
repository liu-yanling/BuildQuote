"""SQLite 存储：按「企业/项目/集合」组织记录，敏感字段（报价、合同、企业价格）用 DATA_KEY 加密落盘。

约束（V3 E4）：不采购商业数据库服务；MVP 用开源 SQLite（轻量起步，可平滑切 PostgreSQL），自行建表，
不做自研存储引擎。单用户 MVP，SQLite WAL 模式足够。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from app.crypto import encrypt, decrypt

# 需要加密落盘的敏感字段（报价、合同、企业价格相关）
SENSITIVE_KEYS = {"unit_price", "total", "price_with_tax", "reference_price_with_tax",
                  "quote_content", "contract_text", "enterprise_price", "quote",
                  # 真实报告导出新增：报告快照内含报价明细与比价结果，同样加密落盘
                  "items", "comparison", "suppliers"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Store:
    """SQLite 单文件库。记录以 JSON 存于 records 表，敏感字段加密。"""

    def __init__(self, db_path: Path | str, data_key: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_key = data_key
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS records(
                id TEXT PRIMARY KEY,
                enterprise_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                collection TEXT NOT NULL,
                ts TEXT NOT NULL,
                data TEXT NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_records_scope "
                      "ON records(enterprise_id, project_id, collection)")

    def _encrypt_sensitive(self, record: dict) -> dict:
        out = dict(record)
        for key in SENSITIVE_KEYS:
            if key in out and out[key] is not None:
                out[key] = encrypt(json.dumps(out[key], ensure_ascii=False), self.data_key)
        return out

    def _decrypt_sensitive(self, record: dict) -> dict:
        out = dict(record)
        for key in SENSITIVE_KEYS:
            if key in out and isinstance(out[key], str):
                try:
                    out[key] = json.loads(decrypt(out[key], self.data_key))
                except Exception:
                    out[key] = None
        return out

    # ---- 写 ----
    def append(self, enterprise_id: str, project_id: str, collection: str, record: dict) -> str:
        record = {"id": uuid.uuid4().hex, "ts": _now(), **self._encrypt_sensitive(record)}
        with self._conn() as c:
            c.execute("INSERT INTO records(id, enterprise_id, project_id, collection, ts, data) "
                      "VALUES(?,?,?,?,?,?)",
                      (record["id"], enterprise_id, project_id, collection, record["ts"],
                       json.dumps(record, ensure_ascii=False)))
        return record["id"]

    # ---- 读 ----
    def query(self, enterprise_id: str, project_id: str, collection: str,
              predicate: Callable[[dict], bool] | None = None) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT data FROM records WHERE enterprise_id=? AND project_id=? "
                             "AND collection=? ORDER BY ts", (enterprise_id, project_id, collection)).fetchall()
        out: list[dict] = []
        for (data,) in rows:
            try:
                rec = json.loads(data)
            except json.JSONDecodeError:
                continue
            rec = self._decrypt_sensitive(rec)
            if predicate is None or predicate(rec):
                out.append(rec)
        return out

    def write_log(self, enterprise_id: str, project_id: str, action: str, detail: dict) -> None:
        self.append(enterprise_id, project_id, "audit_log",
                    {"action": action, "detail": detail, "actor": detail.get("actor", "")})

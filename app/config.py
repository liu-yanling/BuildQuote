"""配置加载与密钥校验：所有配置走 .env / 环境变量，缺失关键键启动即失败。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

_REQUIRED = ("MODEL_API_KEY", "DB_PATH", "DATA_KEY", "SESSION_SECRET")


@dataclass(frozen=True)
class Settings:
    model_api_key: str
    model_base_url: str
    model_name: str
    model_timeout: float
    db_path: Path
    data_key: str
    log_level: str
    session_secret: str
    embedding_api_key: str = ""
    embedding_backend: str = "local_model"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_timeout: float = 30.0


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必需配置 {name}（请在 .env 或环境变量中提供，模板见 .env.example）")
    return value


def load_settings() -> Settings:
    data_key = _require("DATA_KEY")
    if len(data_key) < 32:
        raise RuntimeError("DATA_KEY 长度不足（AES-256-GCM 需要 >=32 字符）")
    session_secret = _require("SESSION_SECRET")
    if len(session_secret) < 32:
        raise RuntimeError("SESSION_SECRET 长度不足（需要 >=32 字符）")
    db_path = Path(os.getenv("DB_PATH", str(ROOT / "data" / "buildquote.db"))).expanduser()
    return Settings(
        model_api_key=_require("MODEL_API_KEY"),
        model_base_url=os.getenv("MODEL_BASE_URL", "https://api.deepseek.com").strip(),
        model_name=os.getenv("MODEL_NAME", "deepseek-chat").strip(),
        model_timeout=float(os.getenv("MODEL_TIMEOUT", "60")),
        db_path=db_path,
        data_key=data_key,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip(),
        session_secret=session_secret,
        embedding_api_key=os.getenv("EMBEDDING_API_KEY", "").strip(),
        embedding_backend=os.getenv("EMBEDDING_BACKEND", "local_model").strip(),
        embedding_base_url=os.getenv("EMBEDDING_BASE_URL", "https://api.openai.com/v1").strip(),
        embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5").strip(),
        embedding_timeout=float(os.getenv("EMBEDDING_TIMEOUT", "30")),
    )

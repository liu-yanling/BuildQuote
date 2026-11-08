"""生产启动入口：带定时催收调度 + 反向代理头支持（单进程，适配 SQLite）。"""
from __future__ import annotations

import os

import uvicorn

from app.main import app, start_scheduler


def main() -> None:
    start_scheduler()
    port = int(os.getenv("PORT", "8010"))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()

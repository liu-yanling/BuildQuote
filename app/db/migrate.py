"""`python -m app.db.migrate up`：初始化 SQLite 数据库与 .tasks/ 目录。"""
from __future__ import annotations

import sys
from pathlib import Path

from app.config import load_settings
from app.storage import Store
from app.tasks import TASKS_DIR


def up() -> None:
    settings = load_settings()
    Store(settings.db_path, settings.data_key)   # 建表
    TASKS_DIR.mkdir(parents=True, exist_ok=True)  # 任务持久化目录
    print(f"✅ 数据库已就绪：{settings.db_path}")
    print(f"✅ 任务目录已就绪：{TASKS_DIR}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "up":
        up()
    else:
        print("用法：python -m app.db.migrate up")

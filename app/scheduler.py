"""定时调度（s12）：定时扫描超期未回的报价，自动向成本经理发送催收提醒。

MVP 用进程内 daemon 线程按固定间隔扫描；通知渠道为站内消息（写 reminders 集合 + 审计日志）。
周期询价、批量导入历史询价单放 V2，不做。
"""
from __future__ import annotations

import threading
import time
from typing import Callable

DEFAULT_INTERVAL = 3600.0   # 默认每小时扫一次


def check_overdue(store, inquiries: list[dict], now_ts: str | None = None) -> list[dict]:
    """找出超期未回的报价：截止时间已过且供应商无已完成回复。返回 [超期项...]。"""
    now = now_ts or time.strftime("%Y-%m-%dT%H:%M:%S")
    overdue: list[dict] = []
    for inquiry in inquiries:
        deadline = inquiry.get("deadline", "")
        if not deadline or deadline > now:
            continue
        inquiry_id = inquiry.get("inquiry_id", "")
        for supplier in inquiry.get("suppliers", []):
            replied = inquiry.get("replied_suppliers", [])
            if supplier not in replied:
                overdue.append({"inquiry_id": inquiry_id, "supplier": supplier,
                                "deadline": deadline, "overdue_days": _days(deadline, now)})
    return overdue


def _days(deadline: str, now: str) -> int:
    try:
        d = time.mktime(time.strptime(deadline[:10], "%Y-%m-%d"))
        n = time.mktime(time.strptime(now[:10], "%Y-%m-%d"))
        return max(0, int((n - d) / 86400))
    except ValueError:
        return 0


def send_reminder(store, enterprise_id: str, project_id: str,
                  inquiry_id: str, supplier: str, overdue_days: int) -> str:
    """向成本经理站内发送催收提醒（写 reminders 集合 + 审计日志）。按询价单+供应商幂等。"""
    key = f"{inquiry_id}:{supplier}"
    existing = store.query(enterprise_id, project_id, "reminders",
                           predicate=lambda r: r.get("key") == key)
    if existing:
        return f"已提醒过 {key}，不重复发送"
    record_id = store.append(enterprise_id, project_id, "reminders", {
        "key": key, "inquiry_id": inquiry_id, "supplier": supplier,
        "overdue_days": overdue_days, "channel": "站内消息",
        "delivered": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    store.write_log(enterprise_id, project_id, "send_reminder",
                    {"actor": "scheduler", "inquiry_id": inquiry_id,
                     "supplier": supplier, "record_id": record_id})
    return f"已催收 {supplier}（超期 {overdue_days} 天）"


def scan_and_remind(store, enterprise_id: str, project_id: str,
                    inquiries: list[dict]) -> list[str]:
    """一次扫描：查超期 → 逐条催收。返回本次动作清单。"""
    out: list[str] = []
    for item in check_overdue(store, inquiries):
        out.append(send_reminder(store, enterprise_id, project_id,
                                 item["inquiry_id"], item["supplier"], item["overdue_days"]))
    return out


def start_scheduler(interval: float = DEFAULT_INTERVAL,
                    task_fn: Callable[[], None] | None = None) -> threading.Thread:
    """启动后台扫描线程。task_fn 每次 tick 执行一次（默认空，由 main 注入 scan_and_remind）。"""

    def _loop() -> None:
        while True:
            try:
                if task_fn:
                    task_fn()
            except Exception:  # noqa: BLE001 —— 定时任务异常不退出
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="buildquote-scheduler")
    t.start()
    return t

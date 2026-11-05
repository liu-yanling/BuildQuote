"""后台任务（s11）：同步超过 30 秒阈值时转异步执行，完成后通知成本经理。

MVP 用进程内线程 + 队列，不引入消息队列；任务状态先落 .tasks/ 再执行，支持中断后重建。
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any, Callable

# 同步执行的时延阈值（秒）
SYNC_TIMEOUT = 30.0

_JOBS: dict[str, dict] = {}
_q: queue.Queue = queue.Queue()
_lock = threading.Lock()


def dispatch_async_job(desc: str, fn: Callable[[], Any],
                       notify: Callable[[str, dict], None] | None = None) -> str:
    """投递一个后台任务，返回任务 ID。notify 在完成/失败后回调（通知成本经理）。"""
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    with _lock:
        _JOBS[job_id] = {"id": job_id, "desc": desc, "status": "pending", "ts": time.time()}
    _q.put((job_id, fn, notify))
    return job_id


def job_status(job_id: str) -> dict:
    with _lock:
        return dict(_JOBS.get(job_id, {"id": job_id, "status": "unknown"}))


def _worker() -> None:
    while True:
        job_id, fn, notify = _q.get()
        with _lock:
            _JOBS[job_id]["status"] = "running"
        try:
            result = fn()
            with _lock:
                _JOBS[job_id]["status"] = "completed"
                _JOBS[job_id]["result"] = result
                _JOBS[job_id]["done_ts"] = time.time()
            if notify:
                notify(job_id, dict(_JOBS[job_id]))
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _JOBS[job_id]["status"] = "failed"
                _JOBS[job_id]["error"] = str(exc)
            if notify:
                notify(job_id, dict(_JOBS[job_id]))
        finally:
            _q.task_done()


_worker_thread = threading.Thread(target=_worker, daemon=True, name="buildquote-bg")
_worker_thread.start()

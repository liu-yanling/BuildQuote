"""工作流运行时（s16）测试：registry、五步编排、journal 断点续跑、结构化输出校验。"""
import asyncio
import tempfile
from pathlib import Path

from app import workflow as wf
from app.llm import MockRunner


def _tool_runner(name, args):
    if name == "compute_price_rules":
        return {"snapshot_id": "calc-1", "comparable": True, "items": [], "ranking": []}
    raise ValueError(f"未知工具 {name}")


def test_registry_has_quote_comparison():
    assert "quote_comparison" in wf.WORKFLOWS
    assert wf.list_workflows()[0]["name"] == "quote_comparison"


def test_meta_validation():
    try:
        wf.validate_meta({"name": "bad name!", "description": "x"})
        assert False
    except wf.WorkflowInputError:
        pass
    ok = wf.validate_meta({"name": "ok-1", "description": "x", "phases": ["a", "b"]})
    assert ok["name"] == "ok-1"


def test_run_quote_comparison_offline(monkeypatch):
    monkeypatch.setattr(wf, "RUNTIME_DIR", Path(tempfile.mkdtemp()))
    quotes = [
        {"supplier": "A", "material": "螺纹钢", "unit_price": 4000},
        {"supplier": "B", "material": "螺纹钢", "unit_price": 4100},
        {"supplier": "C", "material": "螺纹钢", "unit_price": 4050},
    ]
    out = wf.run_workflow_sync("quote_comparison", args={"inquiry_id": "INQ-1", "quotes": quotes},
                               runner=MockRunner(), tool_runner=_tool_runner)
    assert out["status"] == "completed"
    assert out["result"]["supplier_count"] == 3
    # 事件流应有 task_started / phase / task_notification
    kinds = {e["type"] for e in out["events"]}
    assert "task_started" in kinds and "task_notification" in kinds and "phase" in kinds


def test_resume_reuses_journal(monkeypatch):
    monkeypatch.setattr(wf, "RUNTIME_DIR", Path(tempfile.mkdtemp()))
    quotes = [{"supplier": "A", "material": "水泥", "unit_price": 500},
              {"supplier": "B", "material": "水泥", "unit_price": 520},
              {"supplier": "C", "material": "水泥", "unit_price": 510}]
    args = {"inquiry_id": "INQ-2", "quotes": quotes}
    first = wf.run_workflow_sync("quote_comparison", args=args, runner=MockRunner(), tool_runner=_tool_runner)
    # 用同一 run_id 续跑，agent() 应命中 journal 缓存（事件里出现 cached）
    second = wf.run_workflow_sync("quote_comparison", args=args, resume_from_run_id=first["run_id"],
                                  runner=MockRunner(), tool_runner=_tool_runner)
    assert second["status"] == "completed"
    assert any(e.get("status") == "cached" for e in second["events"] if e["type"] == "workflow_agent")


def test_parallel_pipeline_primitives():
    async def _run():
        journal = wf.WorkflowJournal(Path(tempfile.mkdtemp()) / "j.jsonl")
        journal.open()
        task = wf.LocalWorkflowTask("run-x", "t")
        ctx = wf.ExecutionState(task, journal, MockRunner())
        r = await ctx.parallel([lambda: asyncio.sleep(0, result=1), lambda: asyncio.sleep(0, result=2)])
        assert r == [1, 2]
        journal.close()

    asyncio.run(_run())

"""任务系统（s10）测试：持久化、blockedBy 依赖、认领/完成、询价单五步进度。"""
import tempfile
from pathlib import Path

from app.tasks import InquiryBoard, TaskStore, STATUS_COMPLETED, STATUS_IN_PROGRESS


def _store(tmp: Path) -> TaskStore:
    return TaskStore(tmp / ".tasks")


def test_create_and_list():
    with tempfile.TemporaryDirectory() as d:
        st = _store(Path(d))
        t = st.create("setup schema")
        assert t.status == "pending"
        assert any(x.id == t.id for x in st.list())


def test_claim_blocked_by_dependency():
    with tempfile.TemporaryDirectory() as d:
        st = _store(Path(d))
        schema = st.create("setup database schema")
        endpoints = st.create("create API endpoints", blockedBy=[schema.id])
        # 依赖未完成时不能认领
        msg = st.claim(endpoints.id)
        assert "阻塞" in msg or "无法" in msg
        # 完成 schema 后解锁 endpoints
        st.claim(schema.id)
        st.complete(schema.id)
        assert st.can_start(endpoints.id) is True
        msg = st.claim(endpoints.id)
        assert "已认领" in msg
        assert st.get(endpoints.id).status == STATUS_IN_PROGRESS


def test_complete_unlocks_downstream():
    with tempfile.TemporaryDirectory() as d:
        st = _store(Path(d))
        a = st.create("A")
        b = st.create("B", blockedBy=[a.id])
        st.claim(a.id)
        out = st.complete(a.id)
        assert "解锁" in out and "B" in out
        assert st.can_start(b.id) is True


def test_cannot_complete_unclaimed():
    with tempfile.TemporaryDirectory() as d:
        st = _store(Path(d))
        t = st.create("x")
        msg = st.complete(t.id)
        assert "无法" in msg


def test_missing_dependency_rejected():
    with tempfile.TemporaryDirectory() as d:
        st = _store(Path(d))
        try:
            st.create("x", blockedBy=["task_nonexistent"])
            assert False, "应当拒绝不存在的依赖"
        except ValueError:
            pass


def test_inquiry_board_five_steps():
    with tempfile.TemporaryDirectory() as d:
        st = _store(Path(d))
        board = InquiryBoard(st)
        steps = board.init_inquiry("INQ-1", ["A", "B", "C"])
        assert [s.subject.split(" ")[1] for s in steps] == ["建需求", "收报价", "核口径", "做比价", "出报告"]
        status = board.step_status("INQ-1")
        assert status["建需求"] == "pending"
        # 完成建需求
        first = [s for s in steps if s.subject.endswith("建需求")][0]
        st.claim(first.id)
        st.complete(first.id)
        # 供应商回复
        board.record_supplier_reply("INQ-1", "A")
        board.record_supplier_reply("INQ-1", "B")
        board.record_supplier_reply("INQ-1", "C")
        # 3 家全部回复后，「收报价」被解锁（can_start），认领并完成后进入下一步
        receive = [s for s in st.list() if s.subject == "[INQ-1] 收报价"][0]
        assert st.can_start(receive.id) is True
        st.claim(receive.id)
        st.complete(receive.id)
        assert board.step_status("INQ-1")["收报价"] == STATUS_COMPLETED

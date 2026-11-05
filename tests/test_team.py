"""团队与子 Agent（V4）：s06/s13 离线走通分派与文件邮箱。"""
from app.models import User
from app.tasks import TaskStore
from app.team import Team


class _EchoModel:
    """最小模型：不调工具，直接回文本，用于离线验证团队分派。"""

    def complete(self, system, messages, tools):
        return {"text": "mock-result", "tool_uses": [], "stop_reason": "stop"}


def test_team_spawn_dispatch_collect(tmp_path):
    team = Team(_EchoModel(), {"user": User("u1", "成本经理", "成本经理", "e1")},
                store=TaskStore(tmp_path / ".tasks"))
    team.spawn("collect", "collect", "sys", [], {})
    team.spawn("alert", "alert", "sys", [], {})
    team.spawn("qa", "qa", "sys", [], {})

    out = team.run_parallel({"collect": "归集", "alert": "预警", "qa": "问答"})
    assert out["collect"] == "mock-result"
    assert out["alert"] == "mock-result"
    assert out["qa"] == "mock-result"

    inbox = team.collect("lead")
    assert len(inbox) == 3
    assert all(m["type"] == "result" for m in inbox)

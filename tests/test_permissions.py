"""权限边界测试：模型 deny、角色门槛、企业/项目隔离、审核导出门槛。"""
from app.models import User
from app.permissions import assert_report_reviewed, check_permission, filter_by_scope


def _model_user():
    return User("model-1", "模型", "采购", "ent-1", ["proj-1"])


def _manager():
    return User("u1", "成本经理", "成本经理", "ent-1", ["proj-1"])


def _buyer():
    return User("u2", "采购", "采购", "ent-1", ["proj-1"])


def test_model_cannot_export():
    ok, reason = check_permission(_model_user(), "export_report")
    assert ok is False
    assert "模型不得调用" in reason


def test_export_requires_manager_role():
    ok, reason = check_permission(_buyer(), "export_report")
    assert ok is False
    ok, _ = check_permission(_manager(), "export_report")
    assert ok is True


def test_approve_requires_manager_role():
    ok, _ = check_permission(_buyer(), "approve_report")
    assert ok is False
    ok, _ = check_permission(_manager(), "approve_report")
    assert ok is True


def test_cross_project_access_denied():
    user = User("u2", "采购", "采购", "ent-1", ["proj-1"])
    ok, reason = check_permission(user, "search_reference_price",
                                  {"enterprise_id": "ent-1", "project_id": "proj-2"})
    assert ok is False
    assert "跨项目" in reason


def test_scope_filter_drops_other_enterprise():
    user = User("u2", "采购", "采购", "ent-1", ["proj-1"])
    records = [
        {"enterprise_id": "ent-1", "project_id": "proj-1", "material": "钢材"},
        {"enterprise_id": "ent-2", "project_id": "proj-1", "material": "钢材"},
        {"enterprise_id": "ent-1", "project_id": "proj-9", "material": "钢材"},
    ]
    out = filter_by_scope(user, records)
    assert len(out) == 1
    assert out[0]["enterprise_id"] == "ent-1" and out[0]["project_id"] == "proj-1"


def test_report_must_be_reviewed_by_manager():
    assert assert_report_reviewed({"review_status": "审核通过", "reviewer_role": "成本经理"}) is True
    assert assert_report_reviewed({"review_status": "草稿", "reviewer_role": "成本经理"}) is False
    assert assert_report_reviewed({"review_status": "审核通过", "reviewer_role": "采购"}) is False


def test_manual_entry_allowed_for_buyer():
    ok, _ = check_permission(_buyer(), "record_manual_entry")
    assert ok is True

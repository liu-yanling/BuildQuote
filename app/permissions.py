"""权限系统（s03）：角色门槛 + 模型 deny 名单 + 门禁（未审核禁导出、算价禁模型、白名单外禁检索）。"""
from __future__ import annotations

from typing import Any

from app.models import User

# 模型不得调用的工具：导出报告由工作台后端在审核通过后触发
MODEL_DENY_TOOLS = {"export_report"}
DENY_LIST = MODEL_DENY_TOOLS

ROLE_RANK = {"供应商": 1, "采购": 2, "项目经理": 3, "成本经理": 4}

# 各动作的最低角色门槛（MVP 单人为成本经理，门槛主要拦截模型与越权）
ACTION_MIN_ROLE = {
    "parse_quote_file": "采购",
    "record_manual_entry": "采购",
    "search_reference_price": "采购",
    "compute_price_rules": "采购",
    "save_task_state": "采购",
    "dispatch_async_job": "采购",
    "send_reminder": "采购",
    "approve_report": "成本经理",
    "export_report": "成本经理",
    "Workflow": "采购",
}


def _role_ok(user: User, action: str) -> bool:
    need = ACTION_MIN_ROLE.get(action)
    return need is None or ROLE_RANK.get(user.role, 0) >= ROLE_RANK[need]


def check_permission(user: User, action: str, resource: dict[str, Any] | None = None) -> tuple[bool, str]:
    """权限检查：返回 (是否允许, 拒绝原因)。"""
    # 模型（user_id 前缀 'model'）对 deny 名单一律拒绝
    if user.user_id.startswith("model") and action in DENY_LIST:
        return False, f"模型不得调用 {action}（由人工在界面触发）"
    if not _role_ok(user, action):
        return False, f"角色 {user.role} 无权限执行 {action}"

    # 企业/项目归属过滤
    if resource is not None:
        res_enterprise = resource.get("enterprise_id")
        res_project = resource.get("project_id")
        if res_enterprise and res_enterprise != user.enterprise_id:
            return False, "越权：跨企业访问被拒绝"
        if res_project and res_project not in user.project_ids:
            return False, "越权：跨项目访问被拒绝"
    return True, ""


def filter_by_scope(user: User, records: list[dict], project_field: str = "project_id") -> list[dict]:
    """按当前用户企业/项目/角色强制注入过滤条件，越权数据不返回。"""
    return [r for r in records
            if r.get("enterprise_id") == user.enterprise_id
            and r.get(project_field) in user.project_ids]


def assert_report_reviewed(report: dict) -> bool:
    """报告导出前必须已由成本经理审核通过。"""
    return report.get("review_status") == "审核通过" and report.get("reviewer_role") == "成本经理"

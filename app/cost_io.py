"""成本台账 Excel 读写（硬化阶段）：模板生成 / 完整台账导出 / 台账导入解析。

- 模板：`模板` 三张表（预算 / 成本来源 / 填写说明），列结构与前端成本监控表格一致。
- 导出：预算 / 成本来源 / 台账（规则引擎归集结果）/ 说明 四张表，带合计行。
- 导入：按表头名解析（兼容旧版 5 列模板：来源类型/科目/金额/来源ID/备注）。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from app import cost_rules

SHEET_BUDGET = "预算"
SHEET_SOURCES = "成本来源"
SHEET_LEDGER = "台账"
SHEET_NOTES = "填写说明"

BUDGET_HEADERS = ("科目", "预算金额（万元）", "说明")
SOURCE_HEADERS = ("来源类型", "科目/别名", "金额（万元）", "来源ID", "日期", "供应商/对方", "备注")

_BUDGET_ALIASES = {
    "subject": ("科目", "成本科目", "科目/别名", "科目名称", "subject"),
    "budget": ("预算金额（万元）", "预算金额", "预算(万元)", "金额", "budget", "budget_amount"),
    "note": ("说明", "备注", "note"),
}
_SOURCE_ALIASES = {
    "source_type": ("来源类型", "类型", "source_type"),
    "subject": ("科目/别名", "科目", "成本科目", "subject"),
    "amount": ("金额（万元）", "金额", "amount"),
    "source_id": ("来源ID", "来源id", "来源编号", "source_id"),
    "date": ("日期", "发生日期", "date"),
    "supplier": ("供应商/对方", "供应商", "对方", "supplier"),
    "note": ("备注", "说明", "note"),
}
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _norm(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _num(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _date_str(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()[:10]


def _find_col(header: list[str], names: tuple[str, ...]) -> int | None:
    normalized = [h.replace(" ", "").replace("（", "(").replace("）", ")") for h in header]
    for i, h in enumerate(normalized):
        for name in names:
            if name.replace(" ", "").replace("（", "(").replace("）", ")") == h:
                return i
    return None


def _read_sheet_rows(wb, name: str) -> list[list[Any]]:
    ws = wb[name] if name in wb.sheetnames else wb[wb.sheetnames[0]]
    return [list(row) for row in ws.iter_rows(values_only=True)]


# ---------------------------------------------------------------------------
# 模板
# ---------------------------------------------------------------------------
def build_template_bytes(subjects: list[str] | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    config = cost_rules.load_subject_config()
    subjects = subjects or list(config.get("subjects") or []) or [
        "人工费", "材料费", "机械使用费", "分包费", "其他直接费",
        "现场管理费", "公司管理费", "税金", "预备费"]
    aliases = config.get("aliases") or {}

    wb = Workbook()
    bold = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="2F5597")
    wrap = Alignment(wrap_text=True, vertical="top")

    def _header(ws, headers, widths):
        ws.append(list(headers))
        for cell in ws[1]:
            cell.font = bold
            cell.fill = fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[chr(64 + i)].width = w
        ws.freeze_panes = "A2"

    ws1 = wb.active
    ws1.title = SHEET_BUDGET
    _header(ws1, BUDGET_HEADERS, (22, 16, 34))
    for s in subjects:
        ws1.append([s, None, ""])
    ws1.append(["合计", "=SUM(B2:B{0})".format(len(subjects) + 1), ""])

    ws2 = wb.create_sheet(SHEET_SOURCES)
    _header(ws2, SOURCE_HEADERS, (14, 18, 14, 16, 14, 26, 28))
    ws2.append(["合同", "", None, "", "", "", "示例：合同累计"])
    ws2.append(["进度款", "", None, "", "", "", "示例：月进度款"])
    ws2.append(["签证", "", None, "", "", "", "示例：签证/变更"])
    ws2.append(["合计", "", "=SUM(C2:C4)", "", "", "", ""])

    # 科目下拉
    dv = DataValidation(type="list", formula1='"' + ",".join(subjects) + '"', allow_blank=True)
    ws2.add_data_validation(dv)
    dv.add("B2:B200")

    ws3 = wb.create_sheet(SHEET_NOTES)
    ws3.column_dimensions["A"].width = 110
    lines = [
        "填写说明（BuildQuote 成本台账模板）",
        "",
        "1. 「预算」表：每行一个成本科目 + 预算金额（万元），科目需与「成本科目」口径一致。",
        "2. 「成本来源」表：实际发生的合同/进度款/签证等记录，金额单位为万元。",
        "3. 科目列可用标准科目名或别名：" + "；".join(f"{a}→{s}" for a, s in list(aliases.items())[:20]),
        "4. 日期格式 YYYY-MM-DD；供应商/对方填合同相对方名称；备注可写说明。",
        "5. 导入后可在网页端继续编辑，再点「运行监控」出归集台账与预警。",
        "6. 所有数字（归集、偏差、预警）仍由规则引擎计算，Excel 只做数据承载。",
    ]
    for line in lines:
        c = ws3.cell(row=ws3.max_row + 1 if ws3["A1"].value else 1, column=1, value=line)
        c.alignment = wrap

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def build_export_bytes(budget_rows: list[dict[str, Any]], sources: list[dict[str, Any]],
                       ledger: list[dict[str, Any]] | None = None,
                       yellow_ratio: float = cost_rules.DEFAULT_YELLOW_RATIO,
                       red_ratio: float = cost_rules.DEFAULT_RED_RATIO) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    bold = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="2F5597")
    money = "#,##0.00"

    def _head(ws, headers, widths):
        ws.append(headers)
        for cell in ws[1]:
            cell.font = bold
            cell.fill = fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[chr(64 + i)].width = w
        ws.freeze_panes = "A2"

    ws1 = wb.active
    ws1.title = SHEET_BUDGET
    _head(ws1, ("科目", "预算金额（万元）", "说明"), (22, 16, 36))
    total_budget = 0.0
    for r in budget_rows:
        amount = _num(r.get("budget"))
        total_budget += amount
        ws1.append([_norm(r.get("subject")), amount, _norm(r.get("note"))])
        ws1.cell(row=ws1.max_row, column=2).number_format = money
    ws1.append(["合计", total_budget, f"共 {len(budget_rows)} 个科目"])
    ws1.cell(row=ws1.max_row, column=2).number_format = money
    for cell in ws1[ws1.max_row]:
        cell.font = Font(bold=True)

    ws2 = wb.create_sheet(SHEET_SOURCES)
    _head(ws2, SOURCE_HEADERS, (12, 18, 14, 16, 14, 24, 30))
    total_actual = 0.0
    by_type: dict[str, float] = {}
    for s in sources:
        amount = _num(s.get("amount"))
        total_actual += amount
        src_type = _norm(s.get("source_type")) or "未分类"
        by_type[src_type] = by_type.get(src_type, 0.0) + amount
        ws2.append([src_type, _norm(s.get("subject")), amount, _norm(s.get("source_id")),
                    _date_str(s.get("date")), _norm(s.get("supplier")), _norm(s.get("note"))])
        ws2.cell(row=ws2.max_row, column=3).number_format = money
    ws2.append(["合计", "", total_actual, "", "", "", f"共 {len(sources)} 笔"])
    ws2.cell(row=ws2.max_row, column=3).number_format = money
    for cell in ws2[ws2.max_row]:
        cell.font = Font(bold=True)
    row = ws2.max_row + 2
    ws2.cell(row=row, column=1, value="按来源类型小计").font = Font(bold=True)
    for i, (t, amt) in enumerate(sorted(by_type.items(), key=lambda kv: -kv[1]), 1):
        ws2.cell(row=row + i, column=1, value=t)
        ws2.cell(row=row + i, column=3, value=amt).number_format = money

    if ledger:
        ws3 = wb.create_sheet(SHEET_LEDGER)
        _head(ws3, ("科目", "预算（万元）", "实际（万元）", "偏差", "偏差率", "状态"),
              (20, 16, 16, 14, 12, 10))
        tot_b = tot_a = 0.0
        for r in ledger:
            budget = _num(r.get("budget"))
            actual = _num(r.get("actual"))
            tot_b += budget
            tot_a += actual
            dev = _num(r.get("deviation"))
            ratio = r.get("deviation_ratio")
            lvl = r.get("level") or ("红警" if (ratio or 0) >= red_ratio
                                     else "黄警" if (ratio or 0) >= yellow_ratio else "正常")
            ws3.append([_norm(r.get("subject")), budget, actual, dev,
                        ratio, lvl])
            for col in (2, 3, 4):
                ws3.cell(row=ws3.max_row, column=col).number_format = money
            ws3.cell(row=ws3.max_row, column=5).number_format = "0.0%"
        ws3.append(["合计", tot_b, tot_a, tot_a - tot_b,
                    (tot_a - tot_b) / tot_b if tot_b else None,
                    "红警" if tot_b and (tot_a - tot_b) / tot_b >= red_ratio
                    else "黄警" if tot_b and (tot_a - tot_b) / tot_b >= yellow_ratio else "正常"])
        for col in (2, 3, 4):
            ws3.cell(row=ws3.max_row, column=col).number_format = money
        ws3.cell(row=ws3.max_row, column=5).number_format = "0.0%"
        for cell in ws3[ws3.max_row]:
            cell.font = Font(bold=True)

    ws4 = wb.create_sheet(SHEET_NOTES)
    ws4.column_dimensions["A"].width = 110
    notes = [
        "BuildQuote 成本台账导出",
        "",
        f"预算合计：{total_budget:,.2f} 万元；实际合计：{total_actual:,.2f} 万元；差额：{total_actual - total_budget:+,.2f} 万元。",
        f"预警阈值：黄警 ≥{yellow_ratio:.1%} / 红警 ≥{red_ratio:.1%}（规则引擎计算）。",
        "说明：本表由成本监控工作台导出；台账行的偏差/预警为规则引擎结果，可用于复核。",
    ]
    for line in notes:
        c = ws4.cell(row=ws4.max_row + 1 if ws4["A1"].value else 1, column=1, value=line)
        c.alignment = Alignment(wrap_text=True, vertical="top")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
def parse_ledger_bytes(data: bytes) -> dict[str, list[dict[str, Any]]]:
    from openpyxl import load_workbook

    if not data:
        raise ValueError("空文件")
    wb = load_workbook(io.BytesIO(data), data_only=True)

    # 预算
    budget_rows: list[dict[str, Any]] = []
    rows = _read_sheet_rows(wb, SHEET_BUDGET)
    header = [_norm(c) for c in rows[0]] if rows else []
    subj_i = _find_col(header, _BUDGET_ALIASES["subject"])
    bud_i = _find_col(header, _BUDGET_ALIASES["budget"])
    note_i = _find_col(header, _BUDGET_ALIASES["note"])
    for row in rows[1:]:
        subject = _norm(row[subj_i]) if subj_i is not None and subj_i < len(row) else ""
        if not subject or subject == "合计":
            continue
        budget_rows.append({
            "subject": subject,
            "budget": _num(row[bud_i]) if bud_i is not None and bud_i < len(row) else 0.0,
            "note": _norm(row[note_i]) if note_i is not None and note_i < len(row) else "",
        })

    # 成本来源
    sources: list[dict[str, Any]] = []
    rows2 = _read_sheet_rows(wb, SHEET_SOURCES)
    header2 = [_norm(c) for c in rows2[0]] if rows2 else []
    idx = {k: _find_col(header2, names) for k, names in _SOURCE_ALIASES.items()}
    legacy = all(idx[k] is None for k in ("date", "supplier")) and idx["source_type"] is not None
    for row in rows2[1:]:
        def get(key: str, fallback: int | None = None) -> Any:
            i = idx.get(key)
            if i is None:
                i = fallback
            return row[i] if i is not None and i < len(row) else None

        src_type = _norm(get("source_type", 0))
        subject = _norm(get("subject", 1))
        if not subject or subject == "合计" or src_type == "合计":
            continue
        amount = _num(get("amount", 2))
        if legacy:
            sources.append({"source_type": src_type or "进度款", "subject": subject, "amount": amount,
                            "source_id": _norm(get("source_id", 3)),
                            "date": "", "supplier": "",
                            "note": _norm(get("note", 4))})
        else:
            sources.append({"source_type": src_type or "进度款", "subject": subject, "amount": amount,
                            "source_id": _norm(get("source_id")),
                            "date": _date_str(get("date")),
                            "supplier": _norm(get("supplier")),
                            "note": _norm(get("note"))})
    return {"budget_rows": budget_rows, "sources": sources}

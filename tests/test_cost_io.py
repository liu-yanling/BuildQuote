"""成本台账 Excel 读写测试：模板生成 / 导出 / 导入解析（含旧版 5 列模板兼容）。"""
import io

from openpyxl import Workbook, load_workbook

from app import cost_io

BUDGET = [{"subject": "人工费", "budget": 1000, "note": "劳务合同"},
          {"subject": "材料费", "budget": 2000, "note": "钢材/商砼"}]
SOURCES = [
    {"source_type": "合同", "subject": "劳务", "amount": 620, "source_id": "HT-RG-001",
     "date": "2026-09-30", "supplier": "成都建工劳务有限公司", "note": "人工费合同累计"},
    {"source_type": "进度款", "subject": "钢材", "amount": 1200, "source_id": "JD-2026-09",
     "date": "2026-10-25", "supplier": "四川钢材商贸有限公司", "note": "9 月进度款"},
]
LEDGER = [
    {"subject": "人工费", "budget": 1000, "actual": 1060, "deviation": 60,
     "deviation_ratio": 0.06, "level": "黄警"},
    {"subject": "材料费", "budget": 2000, "actual": 2360, "deviation": 360,
     "deviation_ratio": 0.18, "level": "红警"},
]


def _wb_bytes(build):
    wb = Workbook()
    build(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_template_contains_three_sheets_with_headers():
    raw = cost_io.build_template_bytes()
    wb = load_workbook(io.BytesIO(raw))
    assert {"预算", "成本来源", "填写说明"} <= set(wb.sheetnames)
    ws = wb["成本来源"]
    headers = [c.value for c in ws[1]]
    assert headers[:4] == ["来源类型", "科目/别名", "金额（万元）", "来源ID"]
    assert "日期" in headers and "供应商/对方" in headers and "备注" in headers


def test_template_roundtrip_parses_budget_subjects():
    parsed = cost_io.parse_ledger_bytes(cost_io.build_template_bytes())
    subjects = [r["subject"] for r in parsed["budget_rows"]]
    assert "人工费" in subjects and "材料费" in subjects
    assert all(r["budget"] == 0 for r in parsed["budget_rows"])
    assert parsed["sources"] == []


def test_export_workbook_has_sheets_and_totals():
    raw = cost_io.build_export_bytes(BUDGET, SOURCES, ledger=LEDGER)
    wb = load_workbook(io.BytesIO(raw))
    assert {"预算", "成本来源", "台账", "填写说明"} <= set(wb.sheetnames)
    budget_ws = wb["预算"]
    rows = [[c.value for c in r] for r in budget_ws.iter_rows()]
    total_row = [r for r in rows if r[0] == "合计"][0]
    assert total_row[1] == 3000
    sources_ws = wb["成本来源"]
    srows = [[c.value for c in r] for r in sources_ws.iter_rows()]
    stotal = [r for r in srows if r[0] == "合计"][0]
    assert stotal[2] == 1820
    # 按来源类型小计
    assert any(r[0] == "按来源类型小计" for r in srows)


def test_parse_export_roundtrip():
    raw = cost_io.build_export_bytes(BUDGET, SOURCES)
    parsed = cost_io.parse_ledger_bytes(raw)
    assert [r["subject"] for r in parsed["budget_rows"]] == ["人工费", "材料费"]
    assert len(parsed["sources"]) == 2
    assert parsed["sources"][0]["supplier"] == "成都建工劳务有限公司"
    assert parsed["sources"][0]["date"] == "2026-09-30"


def test_parse_legacy_five_column_template():
    def build(wb):
        ws = wb.active
        ws.title = "成本来源"
        ws.append(["source_type", "subject", "amount", "source_id", "note"])
        ws.append(["合同", "劳务", 620, "HT-1", "人工合同"])
        ws2 = wb.create_sheet("预算")
        ws2.append(["subject", "budget"])
        ws2.append(["人工费", 1000])

    raw = _wb_bytes(build)
    parsed = cost_io.parse_ledger_bytes(raw)
    assert parsed["budget_rows"] == [{"subject": "人工费", "budget": 1000.0, "note": ""}]
    assert parsed["sources"][0]["amount"] == 620
    assert parsed["sources"][0]["date"] == ""


def test_parse_rejects_garbage():
    try:
        cost_io.parse_ledger_bytes(b"not an excel file")
        assert False
    except Exception:
        pass

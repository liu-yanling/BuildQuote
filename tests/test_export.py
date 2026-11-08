"""真实报告导出测试：快照重算、docx/pdf 生成、格式校验、文件名安全、报告快照加密落盘。"""
import io
import json
import sqlite3
import zipfile

import pytest

from app import export as ex

ITEMS = [
    {"supplier": "四川钢材商贸", "material": "螺纹钢", "spec": "HRB400 Φ20", "unit": "吨",
     "unit_price": 3580, "quantity": 100, "tax_rate": 0.13, "freight": 200},
    {"supplier": "成都建材供应", "material": "螺纹钢", "spec": "HRB400 Φ20", "unit": "吨",
     "unit_price": 3620, "quantity": 100, "tax_rate": 0.13, "freight": 150},
    {"supplier": "成都金属材料", "material": "螺纹钢", "spec": "HRB400 Φ20", "unit": "吨",
     "unit_price": 3560, "quantity": 100, "tax_rate": 0.13, "freight": 260},
]


def _snap():
    return ex.make_report_snapshot(
        {"inquiry_id": "IQ-2026-001", "project_name": "江山云墅项目", "items": ITEMS,
         "summary": "测试摘要：三家供应商均可比。"},
        "RPT-2026-001", {"user_id": "u1", "role": "成本经理"},
        reviewed_at="2026-11-05T10:00:00")


def test_snapshot_recomputes_with_rules_engine():
    snap = _snap()
    assert snap["comparison"]["comparable"] is True
    assert snap["snapshot_id"] == snap["comparison"]["snapshot_id"]
    assert snap["rule_version"] == snap["comparison"]["rule_version"]
    # 排名由规则引擎按含税单价升序给出：3560 * 1.13 最低
    assert [r["supplier"] for r in snap["comparison"]["ranking"]][0] == "成都金属材料"


def test_snapshot_requires_items():
    with pytest.raises(ValueError):
        ex.make_report_snapshot({"items": []}, "RPT-X", {"user_id": "u1", "role": "成本经理"})


def test_docx_bytes_valid_package():
    data = ex.render_report_bytes(_snap(), ".docx")
    assert data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert "word/document.xml" in z.namelist()
        xml = z.read("word/document.xml").decode("utf-8")
    assert "房建项目材料比价报告" in xml
    assert "RPT-2026-001" in xml
    assert "四川钢材商贸" in xml
    assert "规则引擎" in xml


def test_detail_rows_include_quantity_and_rule_numbers():
    rows, header = ex._detail_rows(_snap())
    assert header[6] == "数量"
    body = rows[1:]
    assert all(r[6] == "100" for r in body)          # 数量来自规则引擎快照
    assert body[0][1] == "成都金属材料"               # 排名第一
    assert body[0][5] == "4,022.80"                  # 含税单价
    assert body[0][7] == "402,540.00"                # 含税总价
    assert body[0][8] == "13.0%"                     # 税率


def test_pdf_bytes_valid():
    data = ex.render_report_bytes(_snap(), ".pdf")
    assert data[:4] == b"%PDF"
    assert len(data) > 3000
    assert data.rstrip().endswith(b"%%EOF")


def test_unsupported_format_rejected():
    with pytest.raises(ValueError):
        ex.render_report_bytes(_snap(), ".md")


def test_export_file_written(tmp_path):
    out = ex.export_report_file(_snap(), ".pdf", tmp_path)
    assert out["file"] == "RPT-2026-001.pdf"
    assert out["path"].is_file()
    assert out["path"].parent == tmp_path
    assert out["size"] > 3000
    assert out["media_type"] == "application/pdf"


def test_safe_report_id_blocks_path_traversal():
    assert "/" not in ex.safe_report_id("../../etc/passwd")
    assert ex.safe_report_id("") == "report"
    assert ex.safe_report_id("RPT-2026-001") == "RPT-2026-001"


def test_store_encrypts_report_snapshot_fields(tmp_path):
    from app.storage import Store

    store = Store(tmp_path / "t.db", "k" * 32)
    store.append("ent", "proj", "reports",
                 {"report_id": "R1", "items": [{"unit_price": 1}],
                  "comparison": {"snapshot_id": "calc-1"}})
    with sqlite3.connect(tmp_path / "t.db") as c:
        (raw,) = c.execute("SELECT data FROM records").fetchone()
    rec = json.loads(raw)
    # 报价明细 / 比价结果落盘必须是密文
    assert isinstance(rec["items"], str)
    assert isinstance(rec["comparison"], str)
    # 读回来仍是原文
    back = store.query("ent", "proj", "reports")
    assert back[0]["items"] == [{"unit_price": 1}]
    assert back[0]["comparison"] == {"snapshot_id": "calc-1"}

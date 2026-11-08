"""合同导入测试：三种格式解析出名称/编号/金额/日期/双方/科目。"""
from pathlib import Path

import pytest

from app import contract_import

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("name", ["合同样例.xlsx", "合同样例.docx", "合同样例.pdf"])
def test_extract_contract_fields(name):
    fmt, text = contract_import._extract_text((EXAMPLES / name).read_bytes(), name)
    assert fmt in {"Excel", "Word", "PDF"}
    f = contract_import.extract_fields(text, name)
    assert f["name"] == "钢材采购合同"
    assert f["number"] == "HT-2026-0912"
    assert f["amount_wan"] == 128.65
    assert f["date"] == "2026-09-15"
    assert f["supplier"] == "四川钢材商贸有限公司"
    assert "成都建工" in f["party_a"]
    assert f["subject"] == "钢材"


def test_amount_unit_forms():
    assert contract_import.extract_fields("合同金额：128.65 万元")["amount_wan"] == 128.65
    assert contract_import.extract_fields("合同价款：1,286,500.00 元")["amount_wan"] == 128.65


def test_unsupported_raises():
    with pytest.raises(ValueError):
        contract_import._extract_text(b"hello", "合同.txt")

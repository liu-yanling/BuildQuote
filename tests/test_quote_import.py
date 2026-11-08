"""报价单导入测试：PDF / Word / Excel 三种格式识别为 10 条报价行。"""
from pathlib import Path

import pytest

from app import quote_import
from app.quote_import import _cut

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("name", ["报价单样例.xlsx", "报价单样例.docx", "报价单样例.pdf"])
def test_parse_sample_formats(name):
    data = (EXAMPLES / name).read_bytes()
    out = quote_import.parse_quote_bytes(data, name)
    assert out["ok"] is True
    assert out["count"] == 10
    first = out["rows"][0]
    assert first["supplier"] == "四川钢材商贸"
    assert first["material"] == "螺纹钢"
    assert first["spec"] == "HRB400 Φ20"
    assert first["unit"] == "吨"
    assert first["unit_price"] == 3580.0
    assert first["quantity"] == 100.0
    assert first["tax_rate"] == 0.13
    assert first["freight"] == 200.0


def test_tax_rate_forms():
    assert quote_import._tax_rate("13%") == 0.13
    assert quote_import._tax_rate(13) == 0.13
    assert quote_import._tax_rate(0.13) == 0.13
    assert quote_import._tax_rate(None) == 0.0


def test_cut_multiple_fields_in_one_line():
    assert _cut("螺纹钢    规格：HRB400 Φ20    单位：吨") == "螺纹钢"
    assert _cut("HRB400 Φ20 单位：吨") == "HRB400 Φ20"


def test_unsupported_binary_raises():
    with pytest.raises(ValueError):
        quote_import.parse_quote_bytes(b"not a quote file", "note.txt")

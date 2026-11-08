"""报价单导入：PDF / Word / Excel → 结构化报价行（供应商 / 材料 / 规格 / 单位 / 单价 / 数量 / 税率 / 运费）。

策略：Excel 先按表头/位置解析表格；PDF、Word 提取文本后按「供应商：…」分块 + 关键字段正则解析；
都没有命中时，再尝试「一行一条报价」的位置解析。识别结果仅作候选，仍需人工确认后进入比价。
"""
from __future__ import annotations

import io
import re
from typing import Any

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "supplier": ("供应商", "供货单位", "供方", "报价单位", "单位名称", "厂商", "供应商名称"),
    "material": ("材料", "材料名称", "品名", "物资名称", "货物名称"),
    "spec": ("规格型号", "规格", "型号"),
    "unit": ("计量单位", "单位"),
    "unit_price": ("含税单价", "单价", "报价", "价格", "含税价"),
    "quantity": ("数量", "供货数量", "采购数量"),
    "tax_rate": ("税率", "增值税率", "增值税"),
    "freight": ("运费", "运输费", "运杂费"),
}

_LABEL_RE = re.compile(r"(?:供应商|供货单位|供方|报价单位|单位名称|供应商名称)[:：]?\s*")
# 标签必须出现在行首（允许缩进），避免「成都金属材料」这类公司名里的“材料”被误当成字段
_KEY_RES = {
    "supplier": re.compile(r"(?m)(?:^|[\u3000 ]{2,})[ \t]*(?:供应商|供货单位|供方|报价单位|单位名称|供应商名称)[:：]\s*([^\n\r]+)"),
    "material": re.compile(r"(?m)(?:^|[\u3000 ]{2,})[ \t]*(?:材料名称|材料|品名|物资名称|货物名称)[:：]\s*([^\n\r]+)"),
    "spec": re.compile(r"(?m)(?:^|[\u3000 ]{2,})[ \t]*(?:规格型号|规格|型号)[:：]\s*([^\n\r]+)"),
    "unit": re.compile(r"(?m)(?:^|[\u3000 ]{2,})[ \t]*(?:计量单位|单位)[:：]\s*([^\n\r]+)"),
    "unit_price": re.compile(r"(?m)(?:^|[\u3000 ]{2,})[ \t]*(?:含税单价|单价|报价|价格|含税价)[:：]\s*([0-9][0-9,]*(?:\.[0-9]+)?)"),
    "quantity": re.compile(r"(?m)(?:^|[\u3000 ]{2,})[ \t]*(?:数量|供货数量|采购数量)[:：]\s*([0-9][0-9,]*(?:\.[0-9]+)?)"),
    "tax_rate": re.compile(r"(?m)(?:^|[\u3000 ]{2,})[ \t]*(?:税率|增值税率|增值税)[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*%?"),
    "freight": re.compile(r"(?m)(?:^|[\u3000 ]{2,})[ \t]*(?:运费|运输费|运杂费)[:：]\s*([0-9][0-9,]*(?:\.[0-9]+)?)"),
}
_MATERIAL_FALLBACK = re.compile(
    r"(螺纹钢|圆钢|线材|盘螺|钢材|水泥|商品混凝土|混凝土|镀锌管|无缝管|PE管|PVC管|管材|电缆|保温材料|砖|砂石)")


def _fnum(v: Any) -> float:
    if v is None:
        return 0.0
    s = str(v).replace(",", "").replace("¥", "").replace("元", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _tax_rate(v: Any) -> float:
    """13% / 13 / 0.13 统一成 0.13。"""
    if v is None:
        return 0.0
    s = str(v).replace("%", "").strip()
    try:
        n = float(s)
    except ValueError:
        return 0.0
    return round(n / 100, 4) if n > 1 else round(n, 4)


def _clean(text: Any) -> str:
    return re.sub(r"[ \t\u3000]+", " ", str(text or "")).strip()


def _row(supplier: str, material: str, spec: str, unit: str, price: Any, qty: Any,
         tax: Any, freight: Any) -> dict[str, Any]:
    return {
        "supplier": _clean(supplier),
        "material": _clean(material),
        "spec": _clean(spec),
        "unit": _clean(unit),
        "unit_price": _fnum(price),
        "quantity": _fnum(qty),
        "tax_rate": _tax_rate(tax),
        "freight": _fnum(freight),
    }


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def _xlsx_rows(data: bytes) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    best_rows: list[dict[str, Any]] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        parsed = _parse_table(rows)
        if len(parsed) > len(best_rows):
            best_rows = parsed
    return best_rows


def _parse_table(rows: list[list[Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    # 找表头行
    header_idx, col_map = None, {}
    for i, row in enumerate(rows[:8]):
        cells = [_clean(c) for c in row]
        found = {}
        for key, aliases in FIELD_ALIASES.items():
            for j, cell in enumerate(cells):
                if any(cell == a or (a in cell and len(cell) <= len(a) + 4) for a in aliases):
                    found.setdefault(key, j)
                    break
        if "supplier" in found and "unit_price" in found and len(found) >= 4:
            header_idx, col_map = i, found
            break
    out: list[dict[str, Any]] = []
    if header_idx is not None:
        for row in rows[header_idx + 1:]:
            def cell(key: str) -> Any:
                j = col_map.get(key)
                return row[j] if j is not None and j < len(row) else None
            supplier = _clean(cell("supplier"))
            price = _fnum(cell("unit_price"))
            if not supplier or price <= 0 or supplier == "合计":
                continue
            out.append(_row(supplier, cell("material") or "", cell("spec") or "", cell("unit") or "",
                            price, cell("quantity"), cell("tax_rate"), cell("freight")))
        return out
    # 无表头：按位置（供应商, 材料, 规格, 单位, 单价, 数量, 税率, 运费）
    for row in rows:
        padded = (list(row) + [None] * 8)[:8]
        supplier = _clean(padded[0])
        price = _fnum(padded[4])
        if not supplier or price <= 0:
            continue
        out.append(_row(supplier, padded[1] or "", padded[2] or "", padded[3] or "",
                        price, padded[5], padded[6], padded[7]))
    return out


# ---------------------------------------------------------------------------
# Word / PDF 文本
# ---------------------------------------------------------------------------
def _docx_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append("\t".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


_CUT_RE = re.compile(
    r"\s+(?:供应商|供货单位|供方|报价单位|单位名称|材料名称|材料|品名|规格型号|规格|型号|计量单位|单位|含税单价|单价|报价|数量|税率|运费|运输费)[:：]")


def _cut(v: Any) -> str:
    """同行多个字段时，截断到下一个字段标签（如「螺纹钢    规格：…」）。"""
    raw = str(v or "").strip()
    return _clean(_CUT_RE.split(raw, maxsplit=1)[0])


def _extract_from_text(text: str) -> list[dict[str, Any]]:
    text = (text or "").replace("\r", "\n")
    # 1) 按「供应商：」分块解析
    starts = [m.start() for m in _LABEL_RE.finditer(text)]
    blocks = [text[s: (starts[i + 1] if i + 1 < len(starts) else len(text))]
              for i, s in enumerate(starts)]
    out: list[dict[str, Any]] = []
    for block in blocks:
        vals = {}
        for key, pat in _KEY_RES.items():
            m = pat.search(block)
            if m:
                vals[key] = m.group(1).strip()
        price = _fnum(vals.get("unit_price"))
        if vals.get("supplier") and price > 0:
            out.append(_row(_cut(vals.get("supplier")), _cut(vals.get("material") or ""),
                            _cut(vals.get("spec") or ""), _cut(vals.get("unit") or ""),
                            price, vals.get("quantity"), vals.get("tax_rate"), vals.get("freight")))
    if out:
        return out
    # 2) 位置解析：一行一条（供应商 材料 规格 单位 单价 数量 税率 运费）
    for raw in text.splitlines():
        line = _clean(raw)
        if not line or line.startswith(("供应商", "材料", "规格", "单位", "单价", "合计")):
            continue
        parts = [p for p in re.split(r"\t| {2,}|\|", line) if p.strip()]
        if len(parts) < 5:
            continue
        supplier = parts[0]
        if not re.search(r"(公司|商贸|材料|建材|钢铁|集团|市场|供应|厂|店)", supplier):
            continue
        nums = [p for p in parts[1:] if re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?%?", p.strip())]
        if len(nums) < 2:
            continue
        material = next((p for p in parts[1:] if _MATERIAL_FALLBACK.search(p)), "")
        spec = next((p for p in parts[1:] if re.search(r"(HRB|Φ|φ|DN|P\.O|C\d)", p)), "")
        unit = next((p for p in parts[1:] if p.strip() in ("吨", "公斤", "kg", "t", "米", "m", "根", "立方米")), "")
        pct = next((p for p in parts if p.strip().endswith("%")), None)
        out.append(_row(supplier, material, spec, unit, nums[0], nums[1],
                        pct if pct else (nums[2] if len(nums) > 2 else None),
                        nums[3] if len(nums) > 3 else None))
    return out


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def parse_quote_bytes(data: bytes, filename: str = "") -> dict[str, Any]:
    if not data:
        raise ValueError("空文件")
    name = (filename or "").lower()
    if data[:4] == b"%PDF" or name.endswith(".pdf"):
        fmt, text = "PDF", _pdf_text(data)
        rows = _extract_from_text(text)
    elif data[:2] == b"PK" and (name.endswith(".docx") or _is_docx(data)):
        fmt, text = "Word", _docx_text(data)
        rows = _extract_from_text(text)
    elif data[:2] == b"PK" or name.endswith((".xlsx", ".xls")):
        fmt, text = "Excel", ""
        rows = _xlsx_rows(data)
    else:
        raise ValueError(f"不支持的文件格式：{filename or '未知'}")
    note = "已按表头/字段自动识别，请人工确认后再进入比价。"
    if not rows:
        note = "未能自动识别出报价行（扫描件 PDF 需先 OCR），请手动补录。"
    return {
        "ok": True,
        "format": fmt,
        "rows": rows,
        "count": len(rows),
        "note": note,
        "text_preview": (text or "")[:300],
    }


def _is_docx(data: bytes) -> bool:
    try:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return any(n.startswith("word/") for n in z.namelist())
    except Exception:
        return False

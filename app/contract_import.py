"""合同导入：PDF / Word / Excel → 基础信息（名称 / 编号 / 金额 / 日期 / 双方 / 科目建议）。

用途：成本来源里「导入合同」时自动填行；点开某行可查看合同文件与解析结果，
并可让 DeepSeek 依据合同原文生成「合同要点」（金额/工期/付款/质保/违约，带原文依据）。
"""
from __future__ import annotations

import io
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "data" / "contracts"
_ID_RE = re.compile(r"^[a-f0-9]{10}$")

_SUBJECT_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("钢材", "钢筋", "型钢", "钢板", "钢结构", "螺纹钢"), "钢材"),
    (("混凝土", "商砼", "商混", "砼"), "商品混凝土"),
    (("劳务", "人工", "班组"), "劳务"),
    (("塔吊", "机械", "设备租赁", "挖机", "施工电梯", "租赁"), "机械"),
    (("装饰", "幕墙", "门窗", "分包", "专业承包"), "分包"),
    (("安全文明", "临时设施", "措施", "脚手架", "模板"), "措施费"),
    (("现场管理", "现场经费", "管理人员"), "现场经费"),
    (("管理费", "公司管理", "总部"), "管理费"),
    (("税", "增值税", "发票"), "税金"),
    (("材料", "采购", "供货", "买卖"), "钢材"),
]


def _fnum(v: Any) -> float:
    s = str(v or "").replace(",", "").replace("，", "").replace("¥", "").replace("￥", "").replace("元", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean(text: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", str(text or "")).strip()


def _extract_text(data: bytes, filename: str) -> tuple[str, str]:
    """返回 (格式, 文本)。"""
    name = (filename or "").lower()
    if data[:4] == b"%PDF" or name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "PDF", "\n".join((p.extract_text() or "") for p in reader.pages)
    if data[:2] == b"PK" and (name.endswith(".docx") or _is_docx(data)):
        from docx import Document

        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append("\t".join(c.text.strip() for c in row.cells))
        return "Word", "\n".join(parts)
    if data[:2] == b"PK" or name.endswith((".xlsx", ".xls")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None and str(c).strip()]
                if cells:
                    parts.append("\t".join(cells))
        return "Excel", "\n".join(parts)
    raise ValueError(f"不支持的文件格式：{filename or '未知'}")


def _is_docx(data: bytes) -> bool:
    try:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return any(n.startswith("word/") for n in z.namelist())
    except Exception:
        return False


def _pick_subject(text: str) -> str:
    for keys, subject in _SUBJECT_KEYWORDS:
        if any(k in text for k in keys):
            return subject
    return ""


def _normalize_date(raw: str) -> str:
    m = re.search(r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})", raw)
    if not m:
        return ""
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def extract_fields(text: str, filename: str = "") -> dict[str, Any]:
    text = (text or "").replace("\t", "：")  # 表格行「字段<TAB>内容」→「字段：内容」
    lines = [_clean(l) for l in text.splitlines() if _clean(l)]
    joined = "\n".join(lines)
    stem = re.sub(r"\.(pdf|docx|xlsx|xls)$", "", filename or "", flags=re.I)

    # 合同名称：优先「合同名称：」，否则取标题行/文件名
    name = ""
    m = re.search(r"合同名称[:：]\s*([^\n\r]+)", joined)
    if m:
        name = _clean(m.group(1))
    if not name:
        for line in lines[:6]:
            if ("合同" in line or "协议" in line) and len(line) <= 40 and "编号" not in line:
                name = line
                break
    if not name:
        name = stem
    name = re.sub(r"(合同书|协议书)$", "合同", name)[:60]

    number = ""
    m = re.search(r"(?:合同编号|合同编码|合同号|编号)[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-—_/（）()]{2,30})", joined)
    if m:
        number = m.group(1)

    # 金额：先找「合同金额/合同价款/总价」附近数字，单位万元/元
    amount_wan = 0.0
    m = re.search(r"(?:合同金额|合同总价|合同价款|总金额|总价款|价款合计|金额)[^\d]{0,15}"
                  r"([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(万元|元)?", joined)
    if m:
        val = _fnum(m.group(1))
        unit = m.group(2) or ""
        if unit == "万元":
            amount_wan = val
        elif val >= 100000:  # 元
            amount_wan = round(val / 10000, 2)
        else:
            amount_wan = val  # 数值较小按万元理解（合同样例中常见写法）

    date = ""
    for pat in (r"(?:签订日期|签订时间|签署日期|签约日期)[:：]?\s*([^\n\r]{4,24})",
                r"(?:签订|签署)[^\n\r]{0,10}(20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2})",
                r"(20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2})"):
        mm = re.search(pat, joined)
        if mm:
            date = _normalize_date(mm.group(1))
            if date:
                break

    supplier = ""
    m = re.search(r"(?:乙方|承包人|承包方|供方|供应商|卖方|施工单位|供货单位)[（(]?[^)）\n:：]{0,10}[)）]?[:：]\s*([^\n\r，,；;]{2,40})", joined)
    if m:
        supplier = _clean(m.group(1))
    party_a = ""
    m = re.search(r"(?:甲方|发包人|采购人|买方|建设单位)[（(]?[^)）\n:：]{0,10}[)）]?[:：]\s*([^\n\r，,；;]{2,40})", joined)
    if m:
        party_a = _clean(m.group(1))

    return {
        "name": name,
        "number": number,
        "amount_wan": round(amount_wan, 2),
        "date": date,
        "supplier": supplier,
        "party_a": party_a,
        "subject": _pick_subject(joined),
    }


def save_contract(data: bytes, filename: str) -> dict[str, Any]:
    fmt, text = _extract_text(data, filename)
    fields = extract_fields(text, filename)
    CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    cid = uuid.uuid4().hex[:10]
    ext = Path(filename or "").suffix.lower()
    if ext not in {".pdf", ".docx", ".xlsx", ".xls"}:
        ext = ".pdf" if fmt == "PDF" else (".docx" if fmt == "Word" else ".xlsx")
    stored = f"{cid}{ext}"
    (CONTRACT_DIR / stored).write_bytes(data)
    meta = {
        "contract_id": cid,
        "filename": filename or stored,
        "stored": stored,
        "format": fmt,
        "fields": fields,
        "text": text[:20000],
        "excerpt": _clean(text)[:300],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (CONTRACT_DIR / f"{cid}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {k: meta[k] for k in ("contract_id", "filename", "format", "fields", "excerpt", "created_at")}


def load_contract(contract_id: str) -> dict[str, Any] | None:
    if not _ID_RE.match(contract_id or ""):
        return None
    p = CONTRACT_DIR / f"{contract_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def contract_file(contract_id: str) -> Path | None:
    meta = load_contract(contract_id)
    if not meta:
        return None
    path = CONTRACT_DIR / meta.get("stored", "")
    return path if path.is_file() else None


def save_analysis(contract_id: str, analysis: str) -> None:
    meta = load_contract(contract_id)
    if not meta:
        return
    meta["analysis"] = analysis
    meta["analyzed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (CONTRACT_DIR / f"{contract_id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


ANALYZE_SYSTEM = (
    "你是工程造价合同助手。只能依据用户提供的合同原文总结，不得使用其他知识、不得编造；"
    "原文没有写的，明确说「合同未提及」。用自然语言 3–5 段回答，不要 Markdown 分点、不要出现编号列表。"
    "需要覆盖：合同双方与标的、合同金额与计价方式、工期/履行期限、付款与结算方式、质量保证金与违约责任。"
)


def analyze(meta: dict[str, Any], model) -> dict[str, Any]:
    from app.qa import naturalize_answer

    if meta.get("analysis"):
        return {"ok": True, "analysis": meta["analysis"], "cached": True}
    text = (meta.get("text") or "")[:9000]
    if not text:
        return {"ok": False, "reason": "该合同没有可分析文本（扫描件需 OCR）"}
    user = f"合同文件：{meta.get('filename', '')}\n合同原文如下：\n\n{text}\n\n请总结这份合同的要点。"
    try:
        resp = model.complete(ANALYZE_SYSTEM, [{"role": "user", "content": user}], tools=[], max_tokens=900)
        analysis = naturalize_answer(resp.get("text", ""))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"合同分析失败：{exc}"}
    save_analysis(meta["contract_id"], analysis)
    return {"ok": True, "analysis": analysis, "cached": False}

"""真实报告导出（硬化阶段）：报告快照 → .docx / .pdf 文件。

铁律（不可违背）：
1. 报告里的所有数字都来自 `rules.compute_price_rules` 的规则引擎快照，渲染层只做格式化，
   不做任何加减乘除（唯一算价处仍是 app/rules.py）。
2. 审核门禁在 `main.py`：只有成本经理审核通过、且内容快照存在的报告才允许导出；
   本模块只负责「给定已审核快照 → 生成文件」，不碰权限判断。
3. 每份报告都标注规则版本与快照 ID，可回溯到报价明细。
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path
from typing import Any

from app import rules

# 导出文件格式（与 tools.EXPORT_FORMATS 保持一致）
REPORT_FORMATS = (".pdf", ".docx")
MEDIA_TYPES = {".pdf": "application/pdf",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

_SAFE_ID = re.compile(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def safe_report_id(report_id: str) -> str:
    """报告编号 → 可用作文件名的安全字符串（防目录穿越/非法字符）。"""
    name = _SAFE_ID.sub("_", (report_id or "").strip()).strip("._") or "report"
    return name[:120]


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------
def make_report_snapshot(report: dict[str, Any], report_id: str, reviewer: dict[str, Any],
                         reviewed_at: str = "", status: str = "审核通过") -> dict[str, Any]:
    """把「人工确认后的报价明细」固化成报告快照：服务端重算比价，不信任前端数字。

    report 至少包含 items（报价明细）；支持 inquiry_id / project_name / suppliers / summary / config / reference。
    """
    items = [i for i in (report.get("items") or []) if isinstance(i, dict) and i.get("supplier")]
    if not items:
        raise ValueError("报告缺少报价明细（items），无法生成报告快照")
    comparison = rules.compute_price_rules(items, config=report.get("config"),
                                           reference=report.get("reference"))
    suppliers = report.get("suppliers") or sorted({str(i.get("supplier", "")) for i in items
                                                    if i.get("supplier")})
    return {
        "kind": "report_snapshot",
        "report_id": report_id,
        "inquiry_id": report.get("inquiry_id", ""),
        "project_name": report.get("project_name") or "房建项目",
        "suppliers": suppliers,
        "items": items,
        "comparison": comparison,
        "summary": (report.get("summary") or "").strip(),
        "rule_version": comparison.get("rule_version", ""),
        "snapshot_id": comparison.get("snapshot_id"),
        "total_quote_count": len(items),
        "review_status": status,
        "reviewer_role": reviewer.get("role", ""),
        "reviewer": reviewer.get("user_id", ""),
        "reviewed_at": reviewed_at or _now(),
        "generated_at": _now(),
    }


# ---------------------------------------------------------------------------
# 文案与格式化（只读快照，不做算术）
# ---------------------------------------------------------------------------
def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def conclusion_lines(snapshot: dict[str, Any]) -> list[str]:
    """比价结论：所有数值均引用快照内规则引擎结果，渲染层不重新计算。"""
    comparison = snapshot.get("comparison") or {}
    reasons = comparison.get("inconsistency_reasons") or []
    ranking = comparison.get("ranking") or []
    suppliers = snapshot.get("suppliers") or []
    lines: list[str] = [
        f"询价单号：{snapshot.get('inquiry_id') or '—'}；参与供应商 {len(suppliers)} 家，"
        f"报价 {snapshot.get('total_quote_count', len(snapshot.get('items') or []))} 份。"
    ]
    if not comparison.get("comparable"):
        lines.append("本期报价口径不一致，规则引擎未排序，不作为比价结论；请先统一口径后重新比价。")
        if reasons:
            lines.append("口径问题：" + "；".join(str(r) for r in reasons))
        return lines
    if not ranking:
        lines.append("规则引擎未产出可比排名，详见下文明细。")
        return lines
    top = ranking[0]
    lines.append(
        f"按可比口径含税单价升序，排名第一：{top.get('supplier', '—')}"
        f"（{top.get('material', '')} {top.get('spec', '')}，单位 {top.get('unit', '—')}，"
        f"含税单价 {_fmt_money(top.get('price_with_tax'))}，"
        f"含税总价 {_fmt_money(top.get('total'))}）。"
    )
    if len(ranking) > 1:
        others = "；".join(
            f"第 {r.get('rank')} 名 {r.get('supplier', '—')} 含税单价 {_fmt_money(r.get('price_with_tax'))}"
            for r in ranking[1:])
        lines.append("其余排名：" + others + "。")
    if any(r.get("diff") is not None for r in ranking):
        lines.append("较参考价差异见明细表（参考价非成交价，仅作口径参考）。")
    return lines


def _fmt_qty(value: Any) -> str:
    try:
        qty = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{int(qty):,}" if qty.is_integer() else f"{qty:,.3f}".rstrip("0").rstrip(".")


def _detail_rows(snapshot: dict[str, Any]) -> tuple[list[list[str]], list[str]]:
    comparison = snapshot.get("comparison") or {}
    rows = comparison.get("ranking") or comparison.get("items") or []
    header = ["排名", "供应商", "材料", "规格", "单位", "含税单价", "数量", "含税总价", "税率", "运费", "较参考价"]
    body: list[list[str]] = []
    for r in rows:
        rank = f"第 {r.get('rank')} 名" if r.get("rank") else "—"
        diff = "—"
        if r.get("diff") is not None:
            diff = f"{float(r.get('diff', 0)):+,.2f}（{_fmt_pct(r.get('diff_ratio'))}）"
        body.append([
            rank, str(r.get("supplier", "")), str(r.get("material", "")), str(r.get("spec", "")),
            str(r.get("unit", "")), _fmt_money(r.get("price_with_tax")),
            _fmt_qty(r.get("quantity")), _fmt_money(r.get("total")),
            _fmt_pct(r.get("tax_rate")), _fmt_money(r.get("freight")), diff,
        ])
    return [header] + body, header


def _formula_lines(snapshot: dict[str, Any]) -> list[str]:
    comparison = snapshot.get("comparison") or {}
    return [f"{k} = {v}" for k, v in (comparison.get("formulas") or {}).items()]


def _audit_lines(snapshot: dict[str, Any]) -> list[str]:
    return [
        f"报告编号：{snapshot.get('report_id', '—')}",
        f"规则版本：{snapshot.get('rule_version', '—')}；计算快照：{snapshot.get('snapshot_id') or '—'}",
        f"审核状态：{snapshot.get('review_status', '—')}；审核人：{snapshot.get('reviewer', '—')}"
        f"（{snapshot.get('reviewer_role', '—')}）；审核时间：{snapshot.get('reviewed_at', '—')}",
        "溯源声明：本报告全部数字由规则引擎（app/rules.py）基于报价明细计算，模型不参与算价；"
        "缺失数据标注「—」，不作补全。",
        "使用范围：报告经成本经理审核通过后导出，仅供内部成本管理使用；参考价非成交价。",
    ]


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def render_docx_bytes(snapshot: dict[str, Any]) -> bytes:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    width, height = section.page_width, section.page_height
    section.page_width, section.page_height = max(width, height), min(width, height)
    section.left_margin = section.right_margin = Cm(1.6)
    section.top_margin = section.bottom_margin = Cm(1.5)

    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    def para(text: str = "", bold: bool = False, size: float = 10.5, align=None, space_after: float = 4):
        p = doc.add_paragraph()
        if align is not None:
            p.alignment = align
        p.paragraph_format.space_after = Pt(space_after)
        run = p.add_run(text)
        run.bold = bold
        run.font.size = Pt(size)
        run.font.name = "宋体"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
        return p

    def heading(text: str):
        p = para(text, bold=True, size=12, space_after=6)
        p.paragraph_format.space_before = Pt(10)
        return p

    # 标题
    para("房建项目材料比价报告", bold=True, size=18, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
    para(f"报告编号：{snapshot.get('report_id', '—')} · 项目：{snapshot.get('project_name', '—')}",
         size=10, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=12)

    # 一、比价结论
    heading("一、比价结论")
    summary = snapshot.get("summary") or ""
    if summary:
        for line in summary.splitlines():
            para(line.strip())
    for line in conclusion_lines(snapshot):
        para(line)

    # 二、比价明细
    heading("二、比价明细（数字来自规则引擎快照）")
    rows, header = _detail_rows(snapshot)
    table = doc.add_table(rows=len(rows), cols=len(header))
    table.style = "Table Grid"
    table.autofit = False
    widths = [Cm(1.8), Cm(3.4), Cm(2.4), Cm(3.2), Cm(1.4), Cm(2.6), Cm(1.6), Cm(2.8), Cm(1.6), Cm(1.8), Cm(3.2)]
    for ci, w in enumerate(widths):
        for cell in table.columns[ci].cells:
            cell.width = w
    for ri, row in enumerate(rows):
        for ci, value in enumerate(row):
            cell = table.cell(ri, ci)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(value)
            run.font.size = Pt(9)
            run.font.name = "宋体"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
            run.bold = ri == 0
            if ri == 0:
                tc_pr = cell._tc.get_or_add_tcPr()
                shd = OxmlElement("w:shd")
                shd.set(qn("w:fill"), "E8EEF7")
                tc_pr.append(shd)

    # 三、口径与公式
    heading("三、口径与计算公式")
    for line in _formula_lines(snapshot) or ["（未提供口径公式）"]:
        para(line, size=9.5)
    reasons = (snapshot.get("comparison") or {}).get("inconsistency_reasons") or []
    if reasons:
        para("口径不一致提示：" + "；".join(str(r) for r in reasons), size=9.5)

    # 四、审核与溯源
    heading("四、审核与溯源")
    for line in _audit_lines(snapshot):
        para(line, size=9.5)

    para("", space_after=2)
    para("本报告由 BuildQuote 成本监控工作台自动生成。", size=9)

    # 页脚
    footer = section.footer.paragraphs[0]
    footer.text = (f"BuildQuote · 规则引擎快照 {snapshot.get('snapshot_id') or '—'} · "
                   f"生成时间 {snapshot.get('generated_at', '—')}")
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in footer.runs:
        run.font.size = Pt(8)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def render_pdf_bytes(snapshot: dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    font_name = "STSong-Light"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))
        pdfmetrics.registerFontFamily(font_name, normal=font_name, bold=font_name,
                                      italic=font_name, boldItalic=font_name)

    def esc(text: Any) -> str:
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    title_style = ParagraphStyle("title", fontName=font_name, fontSize=17, leading=22,
                                 alignment=TA_CENTER, spaceAfter=4)
    sub_style = ParagraphStyle("sub", fontName=font_name, fontSize=9.5, leading=13,
                               alignment=TA_CENTER, spaceAfter=10, textColor=colors.HexColor("#555555"))
    h_style = ParagraphStyle("h", fontName=font_name, fontSize=12, leading=16, spaceBefore=8, spaceAfter=4)
    body_style = ParagraphStyle("body", fontName=font_name, fontSize=9.5, leading=14, spaceAfter=3)
    cell_style = ParagraphStyle("cell", fontName=font_name, fontSize=8, leading=10.5)
    head_cell_style = ParagraphStyle("cellh", fontName=font_name, fontSize=8, leading=10.5,
                                     textColor=colors.white)
    small_style = ParagraphStyle("small", fontName=font_name, fontSize=8.5, leading=12)

    story: list[Any] = [
        Paragraph("房建项目材料比价报告", title_style),
        Paragraph(f"报告编号：{esc(snapshot.get('report_id', '—'))} · 项目：{esc(snapshot.get('project_name', '—'))}",
                  sub_style),
        Paragraph("一、比价结论", h_style),
    ]
    summary = snapshot.get("summary") or ""
    if summary:
        for line in summary.splitlines():
            if line.strip():
                story.append(Paragraph(esc(line.strip()), body_style))
    for line in conclusion_lines(snapshot):
        story.append(Paragraph(esc(line), body_style))

    story.append(Paragraph("二、比价明细（数字来自规则引擎快照）", h_style))
    rows, header = _detail_rows(snapshot)
    table_rows = [[Paragraph(esc(c), head_cell_style) for c in header]]
    for row in rows[1:]:
        table_rows.append([Paragraph(esc(c), cell_style) for c in row])
    col_widths = [34, 92, 62, 82, 38, 68, 44, 78, 36, 48, 86]
    detail = Table(table_rows, colWidths=col_widths, repeatRows=1)
    detail.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5597")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0B7C3")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6FB")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(detail)

    story.append(Paragraph("三、口径与计算公式", h_style))
    for line in _formula_lines(snapshot) or ["（未提供口径公式）"]:
        story.append(Paragraph(esc(line), small_style))
    reasons = (snapshot.get("comparison") or {}).get("inconsistency_reasons") or []
    if reasons:
        story.append(Paragraph(esc("口径不一致提示：" + "；".join(str(r) for r in reasons)), small_style))

    story.append(Paragraph("四、审核与溯源", h_style))
    for line in _audit_lines(snapshot):
        story.append(Paragraph(esc(line), small_style))

    story.append(Spacer(1, 6))
    story.append(Paragraph("本报告由 BuildQuote 成本监控工作台自动生成。",
                           ParagraphStyle("last", fontName=font_name, fontSize=8.5,
                                          leading=12, textColor=colors.HexColor("#666666"))))

    footer_text = (f"BuildQuote · 规则引擎快照 {snapshot.get('snapshot_id') or '—'} · "
                   f"生成时间 {snapshot.get('generated_at', '—')}")

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(font_name, 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawCentredString(landscape(A4)[0] / 2, 8 * mm, footer_text)
        canvas.restoreState()

    buf = io.BytesIO()
    doc_ = SimpleDocTemplate(buf, pagesize=landscape(A4),
                             leftMargin=14 * mm, rightMargin=14 * mm,
                             topMargin=12 * mm, bottomMargin=16 * mm,
                             title=f"BuildQuote 比价报告 {snapshot.get('report_id', '')}",
                             author="BuildQuote")
    doc_.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------
def render_report_bytes(snapshot: dict[str, Any], fmt: str) -> bytes:
    fmt = (fmt or "").lower()
    if fmt not in REPORT_FORMATS:
        raise ValueError(f"不支持的导出格式 {fmt}，可选 .pdf / .docx")
    return render_docx_bytes(snapshot) if fmt == ".docx" else render_pdf_bytes(snapshot)


def export_report_file(snapshot: dict[str, Any], fmt: str, out_dir: Path) -> dict[str, Any]:
    """生成报告文件到 out_dir，返回 {file, path, size, format, media_type}。"""
    fmt = (fmt or "").lower()
    data = render_report_bytes(snapshot, fmt)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_report_id(str(snapshot.get('report_id', 'report')))}{fmt}"
    path = out_dir / filename
    path.write_bytes(data)
    return {"file": filename, "path": path, "size": len(data), "format": fmt,
            "media_type": MEDIA_TYPES[fmt]}

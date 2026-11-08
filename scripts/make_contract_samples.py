"""生成合同导入演示文件（Excel / Word / PDF 各一份，内容相同）。"""
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

FIELDS = [
    ("合同名称", "钢材采购合同"),
    ("合同编号", "HT-2026-0912"),
    ("甲方（采购人）", "成都建工集团第三建筑工程有限公司"),
    ("乙方（供应商）", "四川钢材商贸有限公司"),
    ("合同金额", "1,286,500.00 元"),
    ("签订日期", "2026-09-15"),
    ("工期", "2026-10-01 至 2026-12-31"),
    ("付款方式", "按月计量，次月 10 日前支付已完货款的 80%，供货完成并验收合格后 30 日内付至 97%"),
    ("质量保证金", "结算价的 3%，缺陷责任期 24 个月，期满无质量问题后无息返还"),
    ("违约责任", "逾期交货按逾期部分货款的 0.5‰/天支付违约金，累计不超过合同价的 5%"),
]
PARAS = [
    "钢材采购合同",
    "合同编号：HT-2026-0912",
    "甲方（采购人）：成都建工集团第三建筑工程有限公司",
    "乙方（供应商）：四川钢材商贸有限公司",
    "合同名称：钢材采购合同",
    "合同金额：1,286,500.00 元（大写：壹佰贰拾捌万陆仟伍佰元整）",
    "签订日期：2026-09-15",
    "供货范围：HRB400E 螺纹钢、盘螺、线材，用于项目主体结构工程。",
    "工期：2026-10-01 至 2026-12-31。",
    "付款方式：按月计量，次月 10 日前支付已完货款的 80%，供货完成并验收合格后 30 日内付至 97%。",
    "质量保证金：结算价的 3%，缺陷责任期 24 个月，期满无质量问题后无息返还。",
    "违约责任：逾期交货按逾期部分货款的 0.5‰/天支付违约金，累计不超过合同价的 5%。",
]


def make_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "合同信息"
    ws.append(["字段", "内容"])
    for k, v in FIELDS:
        ws.append([k, v])
    ws.append([])
    ws.append(["合同条款", ""])
    for line in PARAS[7:]:
        ws.append([line])
    wb.save(path)


def make_docx(path: Path) -> None:
    doc = Document()
    doc.add_heading("钢材采购合同", level=1)
    for line in PARAS[1:]:
        doc.add_paragraph(line)
    doc.save(path)


def make_pdf(path: Path) -> None:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("STSong-Light", 12)
    y = h - 60
    for line in PARAS:
        c.drawString(50, y, line)
        y -= 24
    c.save()


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "examples"
    make_xlsx(out / "合同样例.xlsx")
    make_docx(out / "合同样例.docx")
    make_pdf(out / "合同样例.pdf")
    print("wrote examples/合同样例.xlsx/.docx/.pdf")

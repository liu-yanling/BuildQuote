"""生成报价单导入演示文件（Excel / Word / PDF，各含 10 家供应商报价）。"""
from pathlib import Path

from openpyxl import Workbook
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

ROWS = [
    ("四川钢材商贸", "螺纹钢", "HRB400 Φ20", "吨", 3580, 100, 13, 200),
    ("成都建材供应", "螺纹钢", "HRB400 Φ20", "吨", 3620, 100, 13, 150),
    ("成都金属材料", "螺纹钢", "HRB400 Φ20", "吨", 3560, 100, 13, 260),
    ("重庆钢铁集团", "螺纹钢", "HRB400 Φ20", "吨", 3575, 100, 13, 180),
    ("德阳建材有限公司", "螺纹钢", "HRB400 Φ20", "吨", 3610, 100, 13, 220),
    ("资阳钢材市场", "螺纹钢", "HRB400 Φ20", "吨", 3590, 100, 13, 240),
    ("宜宾金属材料", "螺纹钢", "HRB400 Φ20", "吨", 3630, 100, 13, 120),
    ("泸州钢铁贸易", "螺纹钢", "HRB400 Φ20", "吨", 3545, 100, 13, 300),
    ("南充建材集团", "螺纹钢", "HRB400 Φ20", "吨", 3605, 100, 13, 160),
    ("遂宁钢铁物资", "螺纹钢", "HRB400 Φ20", "吨", 3650, 100, 13, 140),
]
HEADERS = ("供应商", "材料", "规格", "单位", "含税单价", "数量", "税率", "运费")


def make_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "报价单"
    ws.append(list(HEADERS))
    for r in ROWS:
        ws.append([r[0], r[1], r[2], r[3], r[4], r[5], f"{r[6]}%", r[7]])
    wb.save(path)


def make_docx(path: Path) -> None:
    doc = Document()
    doc.add_heading("螺纹钢采购报价单（HRB400 Φ20）", level=1)
    doc.add_paragraph("致：成本管理部")
    for r in ROWS:
        doc.add_paragraph(f"供应商：{r[0]}")
        doc.add_paragraph(f"材料：{r[1]}    规格：{r[2]}    单位：{r[3]}")
        doc.add_paragraph(f"含税单价：{r[4]}    数量：{r[5]}    税率：{r[6]}%    运费：{r[7]}")
        doc.add_paragraph("")
    doc.save(path)


def make_pdf(path: Path) -> None:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("STSong-Light", 14)
    c.drawString(60, h - 60, "螺纹钢采购报价单（HRB400 Φ20）")
    y = h - 100
    c.setFont("STSong-Light", 10)
    for r in ROWS:
        c.drawString(60, y, f"供应商：{r[0]}")
        c.drawString(300, y, f"材料：{r[1]}  规格：{r[2]}  单位：{r[3]}")
        y -= 16
        c.drawString(60, y, f"含税单价：{r[4]}  数量：{r[5]}  税率：{r[6]}%  运费：{r[7]}")
        y -= 26
        if y < 80:
            c.showPage()
            c.setFont("STSong-Light", 10)
            y = h - 60
    c.save()


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "examples"
    make_xlsx(out / "报价单样例.xlsx")
    make_docx(out / "报价单样例.docx")
    make_pdf(out / "报价单样例.pdf")
    print("wrote examples/报价单样例.xlsx/.docx/.pdf")

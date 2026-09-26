"""Create a small fictional native-text + scanned-page parser acceptance fixture."""

import os
import shutil
import subprocess
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def generate():
    temporary = Path("tmp/pdfs")
    output = Path("output/pdf")
    temporary.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    font_path = os.environ.get("RAG_TEST_FONT", "/System/Library/Fonts/Supplemental/Songti.ttc")
    pdfmetrics.registerFont(TTFont("STSong-Light", font_path, subfontIndex=0))
    source = temporary / "rag-native.pdf"
    document = canvas.Canvas(str(source), pagesize=(595, 842))
    for page in (1, 2):
        document.setFont("STSong-Light", 20)
        document.drawString(50, 780, f"虚构 RAG 验收资料 / Page {page}")
        document.setFont("STSong-Light", 12)
        document.drawString(50, 745, "仅用于软件测试，不可用于真实作业。")
        document.drawString(50, 710, f"设备 DEMO-00{page} 的演示标签为 LABEL-{page}。")
        document.drawString(50, 680, "未知原因必须保留为未知，不得由相似案例推断。")
        document.setFont("STSong-Light", 16)
        document.drawString(50, 630, "标签对照表")
        rows = [
            ["型号 / Model", "标签 / Label", "条件 / Condition"],
            [f"DEMO-00{page}", f"LABEL-{page}", "仅适用该型号"],
            [f"DEMO-00{page}-B", "未提供", "不可沿用其他型号"],
        ]
        document.setFont("STSong-Light", 11)
        for index, row in enumerate(rows):
            y = 595 - index * 38
            for column, value in enumerate(row):
                document.rect(50 + column * 165, y - 28, 165, 38)
                document.drawString(58 + column * 165, y - 12, value)
        document.setFont("STSong-Light", 10)
        document.drawString(50, 55, f"Synthetic fixture | version 1 | page {page}/2")
        document.showPage()
    document.save()
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise RuntimeError("pdftoppm required for scanned fixture")
    subprocess.run(
        [
            renderer,
            "-f",
            "2",
            "-singlefile",
            "-r",
            "160",
            "-png",
            str(source),
            str(temporary / "rag-scan"),
        ],
        check=True,
    )
    scanned = temporary / "rag-scanned-page.pdf"
    raster = canvas.Canvas(str(scanned), pagesize=(595, 842))
    raster.drawImage(str(temporary / "rag-scan.png"), 0, 0, width=595, height=842)
    raster.save()
    writer = PdfWriter()
    writer.add_page(PdfReader(source).pages[0])
    writer.add_page(PdfReader(scanned).pages[0])
    destination = output / "rag-fixture.pdf"
    writer.write(destination)
    return destination


if __name__ == "__main__":
    print(generate().resolve())

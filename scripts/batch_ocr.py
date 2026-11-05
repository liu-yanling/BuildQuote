"""批量 OCR：把 inbox/江山云墅合同PDF扫描件 下所有 PDF 转成文本，存 inbox/合同OCR文本/。

已存在的（>100 字符）跳过，可断点续跑。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "inbox" / "江山云墅合同PDF扫描件"
OUT_DIR = ROOT / "inbox" / "合同OCR文本"
OCR = ROOT / "scripts" / "ocr"

pdfs = sorted([p for p in PDF_DIR.rglob("*.pdf")])
total = len(pdfs)
t0 = time.time()
processed = 0
skipped = 0
failed = 0
for i, p in enumerate(pdfs, 1):
    rel = p.relative_to(PDF_DIR)
    out = OUT_DIR / (str(rel).replace("/", "__").replace(".pdf", ".txt"))
    if out.exists() and out.stat().st_size > 100:
        skipped += 1
        continue
    r = subprocess.run([str(OCR), str(p), str(out)], capture_output=True, text=True)
    if out.exists() and out.stat().st_size > 100:
        processed += 1
        status = "OK"
    else:
        failed += 1
        status = "FAIL"
        if r.stderr:
            print(f"[{i}/{total}] {status} {p.name[:50]} :: {r.stderr.strip()[-120:]}", flush=True)
            continue
    print(f"[{i}/{total}] {status} {out.stat().st_size if out.exists() else 0:>6} 字符  {p.name[:46]}", flush=True)

print(f"\n完成：新处理 {processed}、失败 {failed}、跳过已存在 {skipped}、共 {total}；用时 {time.time()-t0:.0f}s", flush=True)
sys.exit(1 if failed else 0)

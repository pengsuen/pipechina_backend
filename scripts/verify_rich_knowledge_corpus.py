"""Verify corpus integrity and render all PDF and Word pages for visual review."""

# mypy: disable-error-code="import-untyped,import-not-found"

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader

RUNTIME = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"
RENDERER = (
    Path.home()
    / ".codex/plugins/cache/openai-primary-runtime/documents/26.915.20218"
    / "skills/documents/render_docx.py"
)


def verify(root, qa):
    qa.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / "manifest.json").read_text())
    versions = [v for d in manifest["documents"] for v in d["versions"]]
    assert len(versions) == 75
    assert len({d["code"] for d in manifest["documents"]}) == 60
    pdfs, words = [], []
    total_pages = image_pages = 0
    for v in versions:
        p = root / v["path"]
        data = p.read_bytes()
        assert hashlib.sha256(data).hexdigest() == v["sha256"]
        assert len(data) == v["size_bytes"]
        assert v["body_characters"] >= 3500
        if p.suffix == ".pdf":
            reader = PdfReader(p)
            assert len(reader.pages) >= 5
            texts = [(page.extract_text() or "").strip() for page in reader.pages]
            images = [len(page.images) for page in reader.pages]
            blanks = sum(not t for t in texts)
            if v["layout"] == "scan":
                assert blanks == len(texts) and all(images)
            elif v["layout"] == "mixed":
                assert 0 < blanks < len(texts) and all(
                    images[i] for i, t in enumerate(texts) if not t
                )
            else:
                assert sum(images) >= 2 and blanks == 0
            if v["layout"] == "landscape":
                assert all(page.mediabox.width > page.mediabox.height for page in reader.pages)
            assert not any(marker in "".join(texts) for marker in ["演示", "虚构", "DEMO"])
            pdfs.append(p)
            total_pages += len(texts)
            image_pages += blanks
        elif p.suffix == ".docx":
            words.append(p)

    def render_pdf(p):
        directory = qa / p.stem
        directory.mkdir(exist_ok=True)
        subprocess.run(
            [
                str(RUNTIME / "bin/override/pdftoppm"),
                "-r",
                "75",
                "-png",
                str(p),
                str(directory / "page"),
            ],
            check=True,
            capture_output=True,
        )
        return p.stem

    with ThreadPoolExecutor(max_workers=4) as pool:
        for name in pool.map(render_pdf, pdfs):
            print("rendered", name, flush=True)
    env = dict(os.environ)
    env["PATH"] = str(RUNTIME / "bin/override") + os.pathsep + env["PATH"]
    for p in words:
        subprocess.run(
            [
                str(RUNTIME / "python/bin/python3"),
                str(RENDERER),
                str(p),
                "--output_dir",
                str(qa / p.stem),
                "--dpi",
                "75",
                "--emit_pdf",
            ],
            check=True,
            env=env,
            capture_output=True,
        )
        print("rendered", p.stem, flush=True)
    # Keep source order per file. Contact sheets make missing/overflow pages visible.
    all_pages = sorted(qa.glob("*/page*.png"))
    sheets = []
    for batch in range(0, len(all_pages), 16):
        group = all_pages[batch : batch + 16]
        sheet = Image.new("RGB", (1280, 4 * 475), "#e8e8e8")
        draw = ImageDraw.Draw(sheet)
        for j, p in enumerate(group):
            im = Image.open(p).convert("RGB")
            im.thumbnail((310, 435))
            x = (j % 4) * 320
            y = (j // 4) * 475
            sheet.paste(im, (x, y + 32))
            draw.text((x + 3, y + 3), p.parent.name, fill="black")
            draw.text((x + 3, y + 17), p.name, fill="black")
        dest = qa / f"contact-{batch // 16:02d}.jpg"
        sheet.save(dest, quality=88)
        sheets.append(str(dest))
    report = {
        "documents": 60,
        "versions": 75,
        "questions": len(manifest["questions"]),
        "formats": dict(Counter(Path(v["path"]).suffix for v in versions)),
        "pdf_pages": total_pages,
        "image_only_pages": image_pages,
        "body_characters_min": min(v["body_characters"] for v in versions),
        "body_characters_max": max(v["body_characters"] for v in versions),
        "rendered_pages": len(all_pages),
        "contact_sheets": sheets,
        "parser_end_to_end": "not_run",
    }
    (qa / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("qa", type=Path)
    args = parser.parse_args()
    verify(args.root, args.qa)

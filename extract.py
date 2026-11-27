#!/usr/bin/env python3
"""Step 1 of the ELA study-guide pipeline.

* Finds every PDF under ``ela_pdfs/``.
* Detects byte-identical duplicates with SHA-256 and keeps one file per hash.
* Extracts text from each unique PDF with ``pdftotext -layout`` (fallback:
  PyMuPDF). PDFs without a usable text layer are OCR'd with ``ocrmypdf``
  (fallback: ``pdftoppm`` + ``tesseract``).
* Saves the text to ``work/<name>.txt`` (pages separated by form feeds).
* Extracts embedded figures to ``latex/figs/``.
* Writes ``work/manifest.json`` which ``summarize.py`` consumes.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "ela_pdfs"
WORK = ROOT / "work"
FIGS = ROOT / "latex" / "figs"

MIN_CHARS_PER_PAGE = 40      # below this a page is treated as having no text layer
MIN_FIG_PX = 120             # ignore icons / logos / rules smaller than this
MAX_FIGS_PER_FILE = 12


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def slugify(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()


def page_count(pdf: Path) -> int:
    try:
        import pymupdf as fitz  # PyMuPDF
        with fitz.open(pdf) as doc:
            return doc.page_count
    except Exception:
        pass
    if have("pdfinfo"):
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        if m:
            return int(m.group(1))
    return 1


def text_pdftotext(pdf: Path) -> str | None:
    if not have("pdftotext"):
        return None
    res = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"],
                         capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else None


def text_pymupdf(pdf: Path) -> str | None:
    try:
        import pymupdf as fitz
    except ImportError:
        return None
    try:
        with fitz.open(pdf) as doc:
            return "\f".join(page.get_text("text", sort=True) for page in doc)
    except Exception:
        return None


def text_ocr(pdf: Path) -> str | None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if have("ocrmypdf"):
            sidecar = tmp / "side.txt"
            res = subprocess.run(["ocrmypdf", "--force-ocr", "-l", "eng", "--sidecar", str(sidecar),
                                  "--output-type", "pdf", str(pdf), str(tmp / "out.pdf")],
                                 capture_output=True, text=True)
            if res.returncode == 0 and sidecar.exists():
                return sidecar.read_text(encoding="utf-8", errors="replace")
            print(f"    ocrmypdf failed ({res.returncode}): {res.stderr.strip()[-300:]}")
        if have("pdftoppm") and have("tesseract"):
            subprocess.run(["pdftoppm", "-r", "300", "-png", str(pdf), str(tmp / "p")], check=False)
            pages = []
            for png in sorted(tmp.glob("p-*.png")):
                res = subprocess.run(["tesseract", str(png), "-", "-l", "eng", "--psm", "6"],
                                     capture_output=True, text=True)
                pages.append(res.stdout)
            if pages:
                return "\f".join(pages)
    return None


def needs_ocr(text: str | None, pages: int) -> bool:
    if not text:
        return True
    letters = len(re.findall(r"[A-Za-z]", text))
    return letters < MIN_CHARS_PER_PAGE * max(1, pages)


def extract_text(pdf: Path) -> tuple[str, str]:
    pages = page_count(pdf)
    text, method = text_pdftotext(pdf), "pdftotext -layout"
    if needs_ocr(text, pages):
        alt = text_pymupdf(pdf)
        if not needs_ocr(alt, pages):
            text, method = alt, "PyMuPDF"
    if needs_ocr(text, pages):
        ocr = text_ocr(pdf)
        if ocr and (not text or len(ocr.strip()) > len(text.strip())):
            text, method = ocr, "OCR"
    return (text or ""), method


def extract_figures(pdf: Path, slug: str, scanned: bool) -> list[dict]:
    """Extract embedded raster figures. Full-page scans are skipped."""
    figs: list[dict] = []
    try:
        import pymupdf as fitz
    except ImportError:
        fitz = None
    if fitz is not None:
        seen: set[str] = set()
        with fitz.open(pdf) as doc:
            # images reused on many pages are logos / headers, not figures
            uses: dict[int, int] = {}
            for page in doc:
                for img in page.get_images(full=True):
                    uses[img[0]] = uses.get(img[0], 0) + 1
            for pno, page in enumerate(doc, start=1):
                page_area = page.rect.width * page.rect.height
                for img in page.get_images(full=True):
                    xref = img[0]
                    if doc.page_count > 2 and uses.get(xref, 0) > 2:
                        continue
                    try:
                        pix = fitz.Pixmap(doc, xref)
                        if pix.width < MIN_FIG_PX or pix.height < MIN_FIG_PX:
                            continue
                        rects = page.get_image_rects(xref)
                        if rects and scanned:
                            r = rects[0]
                            if r.width * r.height > 0.6 * page_area:
                                continue   # this is the scanned page itself
                        digest = hashlib.sha1(pix.samples).hexdigest()
                        if digest in seen:
                            continue
                        seen.add(digest)
                        if pix.n - pix.alpha >= 4:          # CMYK -> RGB
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        if pix.alpha:
                            pix = fitz.Pixmap(pix, 0)
                        name = f"{slug}-p{pno:02d}-{len(figs) + 1:02d}.png"
                        pix.save(FIGS / name)
                        figs.append({"file": name, "page": pno, "w": pix.width, "h": pix.height})
                    except Exception as exc:  # broken image streams are skipped
                        print(f"    skipped image xref {xref}: {exc}")
                    if len(figs) >= MAX_FIGS_PER_FILE:
                        return figs
        return figs
    if have("pdfimages") and not scanned:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["pdfimages", "-png", "-p", str(pdf), f"{tmp}/img"], check=False)
            for png in sorted(Path(tmp).glob("img-*.png"))[:MAX_FIGS_PER_FILE]:
                m = re.match(r"img-(\d+)-", png.name)
                name = f"{slug}-{png.name}"
                shutil.copy(png, FIGS / name)
                figs.append({"file": name, "page": int(m.group(1)) if m else 0})
    return figs


def unpack_zips() -> list[Path]:
    """PDFs inside any .zip uploaded to ela_pdfs/ (only .pdf members are extracted)."""
    out: list[Path] = []
    dest_root = WORK / "unzipped"
    if dest_root.exists():
        shutil.rmtree(dest_root)
    for z in sorted(SRC_DIR.rglob("*")):
        if z.suffix.lower() != ".zip":
            continue
        try:
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    name = Path(info.filename).name
                    if info.is_dir() or not name.lower().endswith(".pdf") or name.startswith("._"):
                        continue
                    dest = dest_root / slugify(z.stem) / name   # flattened: no path traversal
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, dest.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    out.append(dest)
            print(f"  unpacked {z.name}: {len(out)} PDF(s) so far")
        except zipfile.BadZipFile:
            print(f"  WARNING: {z.name} is not a valid zip; skipped")
    return out


def main() -> int:
    WORK.mkdir(exist_ok=True)
    pdfs = sorted(p for p in SRC_DIR.rglob("*") if p.suffix.lower() == ".pdf") if SRC_DIR.exists() else []
    pdfs += unpack_zips() if SRC_DIR.exists() else []
    if not pdfs:
        print(f"ERROR: no PDFs found in {SRC_DIR}. Upload PDFs (or a .zip of PDFs) to ela_pdfs/ first.")
        return 1
    FIGS.mkdir(parents=True, exist_ok=True)
    for old in FIGS.glob("*.png"):
        old.unlink()

    by_hash: dict[str, list[Path]] = {}
    for p in pdfs:
        by_hash.setdefault(sha256(p), []).append(p)

    manifest = {"files": [], "duplicates": []}
    stems: set[str] = set()
    for digest, group in by_hash.items():
        group.sort(key=lambda p: (p.name, str(p)))
        keep, dups = group[0], group[1:]
        stem = keep.stem
        if stem in stems:                      # same file name in another folder / zip
            stem = f"{stem}-{digest[:8]}"
        stems.add(stem)
        print(f"* {keep.name}")
        text, method = extract_text(keep)
        (WORK / f"{stem}.txt").write_text(text, encoding="utf-8")
        npages = page_count(keep)
        figs = extract_figures(keep, (slugify(stem)[-50:] + "-" + digest[:6]), scanned=(method == "OCR"))
        print(f"    {npages} pages, {len(text)} chars via {method}, {len(figs)} figures,"
              f" {len(dups)} duplicate(s)")
        manifest["files"].append({
            "name": keep.name if stem == keep.stem else f"{stem}.pdf", "stem": stem, "path": str(keep.relative_to(ROOT)),
            "sha256": digest, "pages": npages, "method": method,
            "text": str((WORK / f"{stem}.txt").relative_to(ROOT)), "figures": figs,
            "duplicates": [d.name for d in dups],
        })
        for d in dups:
            manifest["duplicates"].append({"name": d.name, "same_as": keep.name, "sha256": digest})

    manifest["files"].sort(key=lambda f: f["name"])
    (WORK / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"{len(pdfs)} PDFs, {len(manifest['files'])} unique, {len(manifest['duplicates'])} duplicates")
    return 0


if __name__ == "__main__":
    sys.exit(main())

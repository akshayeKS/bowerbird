"""Deck -> slides/sNN.png, plus whatever text the deck can tell us about itself.

Backends, picked by file type unless you force one in config:
  images  a folder of PNG/JPG already in order        (no dependencies)
  chrome  an HTML deck, screenshotted per <section>   (Google Chrome)
  pdf     a PDF, via pdftoppm                         (brew install poppler)
  office  .pptx/.ppt/.odp -> PDF -> pdf backend       (LibreOffice)

Keynote is deliberately unsupported: LibreOffice's .key import is unreliable.
Export to PDF from Keynote instead.
"""
from __future__ import annotations

import glob
import html as _html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp")
OFFICE_EXTS = (".pptx", ".ppt", ".odp")
CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
)
PPT_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


class RasterError(RuntimeError):
    pass


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def find_chrome() -> str | None:
    for p in CHROME_PATHS:
        if p and os.path.exists(p):
            return p
    return None


def pick_backend(deck: str, forced: str = "auto") -> str:
    if forced and forced != "auto":
        return forced
    if os.path.isdir(deck):
        return "images"
    ext = os.path.splitext(deck)[1].lower()
    if ext in (".html", ".htm"):
        return "chrome"
    if ext == ".pdf":
        return "pdf"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in IMAGE_EXTS:
        return "images"
    raise RasterError(
        "I don't know how to read %s.\n"
        "Supported: a folder of images, .html, .pdf, .pptx/.ppt/.odp.\n"
        "Keynote: use File > Export To > PDF first." % (ext or deck))


def _clear(outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    for p in glob.glob(os.path.join(outdir, "s[0-9]*.png")):
        os.remove(p)


# ---------------------------------------------------------------- images
def _from_images(deck: str, outdir: str, cfg) -> int:
    src = deck if os.path.isdir(deck) else os.path.dirname(deck) or "."
    files = [p for p in glob.glob(os.path.join(src, "*"))
             if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
    files.sort(key=lambda p: natural_key(os.path.basename(p)))
    if not files:
        raise RasterError("No images found in %s" % src)
    _clear(outdir)
    for i, f in enumerate(files, 1):
        shutil.copyfile(f, os.path.join(outdir, "s%02d.png" % i))
    return len(files)


# ---------------------------------------------------------------- chrome / html
def html_sections(raw: str) -> list[str]:
    """Each slide of an HTML deck. Prefers <section class="...slide...">."""
    secs = re.findall(r"<section\b[^>]*class=['\"][^'\"]*slide[^'\"]*['\"][^>]*>.*?</section>",
                      raw, re.S | re.I)
    if not secs:
        secs = re.findall(r"<section\b[^>]*>.*?</section>", raw, re.S | re.I)
    return secs


def _slide_box(css: str, default_w: int, default_h: int) -> tuple[int, int]:
    """Native CSS size of a .slide, so we screenshot at 1:1 then scale up."""
    m = re.search(r"\.slide\s*\{([^}]*)\}", css)
    if m:
        body = m.group(1)
        w = re.search(r"width\s*:\s*(\d+)px", body)
        h = re.search(r"height\s*:\s*(\d+)px", body)
        if w and h:
            return int(w.group(1)), int(h.group(1))
    return default_w, default_h


def _from_chrome(deck: str, outdir: str, cfg) -> int:
    chrome = find_chrome()
    if not chrome:
        raise RasterError(
            "An HTML deck needs Chrome (or Chromium/Edge) to render.\n"
            "Install Google Chrome, or export the deck to PDF and pass that instead.")
    raw = open(deck, encoding="utf-8", errors="replace").read()
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", raw, re.S))
    secs = html_sections(raw)
    if not secs:
        raise RasterError("No <section> elements found in %s" % deck)

    bw, bh = _slide_box(styles, cfg.width, cfg.height)
    scale = min(cfg.width / bw, cfg.height / bh)
    override = (
        "html,body{margin:0!important;padding:0!important;background:#000!important;"
        "overflow:hidden!important;width:%dpx;height:%dpx}"
        ".slide{position:relative!important;top:0!important;left:0!important;"
        "transform:none!important;margin:0!important;width:%dpx!important;height:%dpx!important}"
        ".top,.nav,#prev,#next,#count{display:none!important}" % (bw, bh, bw, bh))

    _clear(outdir)
    tmp = tempfile.mkdtemp(prefix="bowerbird_")
    base = os.path.dirname(os.path.abspath(deck))
    for i, sec in enumerate(secs, 1):
        page = ("<!doctype html><html><head><meta charset='utf-8'>"
                "<base href='file://%s/'>"
                "<style>%s</style><style>%s</style></head><body>%s</body></html>"
                % (base, styles, override, sec))
        src = os.path.join(tmp, "s%02d.html" % i)
        open(src, "w", encoding="utf-8").write(page)
        dst = os.path.join(outdir, "s%02d.png" % i)
        r = subprocess.run([
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=%s" % scale,
            "--window-size=%d,%d" % (bw, bh),
            "--virtual-time-budget=8000",
            "--screenshot=%s" % dst, "file://" + src,
        ], capture_output=True, text=True)
        if not os.path.exists(dst):
            raise RasterError("Chrome rendered nothing for slide %d\n%s"
                              % (i, (r.stderr or "")[-600:]))
    return len(secs)


# ---------------------------------------------------------------- pdf
def _from_pdf(deck: str, outdir: str, cfg) -> int:
    if not shutil.which("pdftoppm"):
        raise RasterError(
            "Rasterizing a PDF needs pdftoppm (part of poppler).\n"
            "  macOS:  brew install poppler\n"
            "  Linux:  sudo apt install poppler-utils\n"
            "  Windows: winget install oschwartz10612.Poppler")
    _clear(outdir)
    subprocess.run(["pdftoppm", "-r", str(cfg.dpi), "-png", deck,
                    os.path.join(outdir, "page")], check=True,
                   capture_output=True)
    pages = sorted(glob.glob(os.path.join(outdir, "page-*.png")), key=natural_key)
    if not pages:
        raise RasterError("pdftoppm produced no pages from %s" % deck)
    for i, p in enumerate(pages, 1):
        os.rename(p, os.path.join(outdir, "s%02d.png" % i))
    return len(pages)


def _from_office(deck: str, outdir: str, cfg) -> int:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RasterError(
            "Reading %s needs LibreOffice to convert it to PDF.\n"
            "  macOS: brew install --cask libreoffice\n"
            "Or just export the deck to PDF yourself and pass the PDF."
            % os.path.splitext(deck)[1])
    tmp = tempfile.mkdtemp(prefix="bowerbird_office_")
    subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                    "--outdir", tmp, deck], check=True, capture_output=True)
    pdf = os.path.join(tmp, os.path.splitext(os.path.basename(deck))[0] + ".pdf")
    if not os.path.exists(pdf):
        raise RasterError("LibreOffice produced no PDF from %s" % deck)
    return _from_pdf(pdf, outdir, cfg)


_BACKENDS = {"images": _from_images, "chrome": _from_chrome,
             "pdf": _from_pdf, "office": _from_office}


def rasterize(deck: str, outdir: str, cfg) -> int:
    name = pick_backend(deck, cfg.rasterizer)
    fn = _BACKENDS.get(name)
    if fn is None:
        raise RasterError("Unknown rasterizer %r. Pick one of: %s"
                          % (name, ", ".join(sorted(_BACKENDS))))
    return fn(deck, outdir, cfg)


# ---------------------------------------------------------------- text
def _strip_tags(s: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def _pptx_text(deck: str) -> dict[int, str]:
    """Speaker notes if present, else the slide's own text."""
    out: dict[int, str] = {}
    rid = "{%s}id" % PPT_NS["r"]
    with zipfile.ZipFile(deck) as z:
        names = set(z.namelist())
        try:
            pres = ET.fromstring(z.read("ppt/presentation.xml"))
            prels = ET.fromstring(z.read("ppt/_rels/presentation.xml.rels"))
        except KeyError:
            return out
        target = {r.get("Id"): r.get("Target") for r in prels}
        order = []
        lst = pres.find("p:sldIdLst", PPT_NS)
        if lst is not None:
            for sid in lst:
                t = target.get(sid.get(rid))
                if t:
                    order.append("ppt/" + t.lstrip("/").replace("ppt/", "", 1).lstrip("./"))

        def texts(xml: bytes) -> str:
            root = ET.fromstring(xml)
            parts = []
            for sp in root.iter("{%s}sp" % PPT_NS["p"]):
                ph = sp.find(".//p:nvSpPr/p:nvPr/p:ph", PPT_NS)
                if ph is not None and ph.get("type") in ("sldNum", "dt", "ftr"):
                    continue
                for para in sp.iter("{%s}p" % PPT_NS["a"]):
                    line = "".join(t.text or "" for t in para.iter("{%s}t" % PPT_NS["a"]))
                    if line.strip():
                        parts.append(line.strip())
            return " ".join(parts)

        for i, slide_path in enumerate(order, 1):
            note = ""
            rels = "%s/_rels/%s.rels" % (os.path.dirname(slide_path),
                                         os.path.basename(slide_path))
            if rels in names:
                for r in ET.fromstring(z.read(rels)):
                    if r.get("Type", "").endswith("/notesSlide"):
                        np = os.path.normpath(os.path.join(
                            os.path.dirname(slide_path), r.get("Target"))).replace(os.sep, "/")
                        if np in names:
                            note = texts(z.read(np))
            if not note and slide_path in names:
                note = texts(z.read(slide_path))
            out[i] = note
    return out


def extract_text(deck: str) -> dict[int, str]:
    """Per-slide text, best effort. Empty dict is fine - the user can write blind."""
    if os.path.isdir(deck):
        return {}
    ext = os.path.splitext(deck)[1].lower()
    try:
        if ext in (".html", ".htm"):
            raw = open(deck, encoding="utf-8", errors="replace").read()
            raw = re.sub(r"data:[^\"')]{200,}", "", raw)   # drop base64 payloads
            return {i: _strip_tags(s) for i, s in enumerate(html_sections(raw), 1)}
        if ext == ".pptx":
            return _pptx_text(deck)
        if ext == ".pdf" and shutil.which("pdftotext"):
            tmp = tempfile.mktemp(suffix=".txt")
            subprocess.run(["pdftotext", "-layout", deck, tmp],
                           check=True, capture_output=True)
            pages = open(tmp, encoding="utf-8", errors="replace").read().split("\f")
            os.unlink(tmp)
            return {i: " ".join(p.split()) for i, p in enumerate(pages, 1) if p.strip()}
    except Exception as e:  # text is a convenience, never fatal
        print("note: couldn't read slide text (%s)" % e, file=sys.stderr)
    return {}

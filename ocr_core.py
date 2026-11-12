# -*- coding: utf-8 -*-
"""
แกนหลักของโปรแกรม OCR (ไม่มีส่วนหน้าจอ)
- อ่าน PDF / รูปภาพ ด้วยโมเดล iapp/OpenThai2.0-OCR-Text
- เขียนผลเป็น PDF ค้นหาได้ (.pdf), ข้อความ (.txt), Word (.docx)
ทำงานออฟไลน์ทั้งหมด
"""
from __future__ import annotations

import io
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pymupdf
from PIL import Image

PDF_EXT = {".pdf"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_EXT = PDF_EXT | IMG_EXT

# ฟอนต์ที่ใช้ฝังเลเยอร์ข้อความ (มองไม่เห็น) ใน PDF ต้องรองรับอักษรไทย
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\THSarabunNew.ttf",
    r"C:\Windows\Fonts\LeelawUI.ttf",
    r"C:\Windows\Fonts\leelawad.ttf",
    "/System/Library/Fonts/Supplemental/Tahoma.ttf",      # macOS
    "/System/Library/Fonts/Supplemental/Ayuthaya.ttf",    # macOS
    "/System/Library/Fonts/Supplemental/Silom.ttf",       # macOS
    "/usr/share/fonts/opentype/tlwg/Loma.otf",
    "/usr/share/fonts/truetype/tlwg/Loma.ttf",
    "/Library/Fonts/Tahoma.ttf",
]


# ---------------------------------------------------------------- paths
def resource_dir() -> Path:
    """โฟลเดอร์ที่เก็บไฟล์ประกอบ (ตอนเป็น exe คือ _internal)"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def model_dir() -> Path:
    return resource_dir() / "model"


def find_font() -> str | None:
    local = resource_dir() / "fonts"
    cands = list(local.glob("*.ttf")) + list(local.glob("*.otf")) if local.exists() else []
    for p in [*map(str, cands), *FONT_CANDIDATES]:
        if os.path.exists(p):
            return p
    return None


def collect_inputs(paths) -> list[Path]:
    """รับไฟล์/โฟลเดอร์ คืนรายชื่อไฟล์ที่รองรับ (ไม่ซ้ำ)"""
    out, seen = [], set()
    for p in map(Path, paths):
        items = sorted(p.rglob("*")) if p.is_dir() else [p]
        for f in items:
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXT:
                key = str(f.resolve()).lower()
                if key not in seen and "OCR_output" not in f.parts:
                    seen.add(key)
                    out.append(f)
    return out


def count_pages(path: Path) -> int:
    if path.suffix.lower() in PDF_EXT:
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise ValueError("PDF ติดรหัสผ่าน")
            return doc.page_count
    with Image.open(path) as im:
        return getattr(im, "n_frames", 1)


# ---------------------------------------------------------------- worker
_OCR = None


def init_worker(mdir: str, threads: int = 1):
    """โหลดโมเดลครั้งเดียวต่อ process"""
    global _OCR
    if mdir not in sys.path:
        sys.path.insert(0, mdir)  # ให้ import แพ็กเกจ runtime/ ที่มากับโมเดลได้
    from runtime.line_engine import LineOCR  # noqa: E402

    _OCR = LineOCR(mdir, threads=threads,
                   runtime={"backend": "onnxruntime", "precision": "fp32"})


def _load_image_frame(path: str, index: int) -> tuple[Image.Image, float]:
    with Image.open(path) as im:
        if index:
            im.seek(index)
        dpi = im.info.get("dpi", (0, 0))[0] or 0
        frame = im.convert("RGB")
    try:
        dpi = float(dpi)
    except (TypeError, ValueError):
        dpi = 0.0
    if dpi < 50 or dpi > 1200:
        dpi = 200.0
    return frame, dpi


def ocr_page(job: dict) -> dict:
    """ประมวลผล 1 หน้า (รันใน worker process)"""
    path, idx = job["path"], job["index"]
    res = {"file_id": job["file_id"], "index": idx, "source": "ocr",
           "text": "", "lines": [], "dpi": job["dpi"], "error": None}
    try:
        if job["kind"] == "pdf":
            with pymupdf.open(path) as doc:
                page = doc[idx]
                if job["use_text_layer"]:
                    existing = page.get_text("text", sort=True).strip()
                    if len(existing.replace(" ", "")) >= 30:
                        res.update(source="text", text=existing)
                        return res
                pix = page.get_pixmap(dpi=job["dpi"], alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        else:
            img, dpi = _load_image_frame(path, idx)
            res["dpi"] = dpi
            res["size"] = img.size
        r = _OCR.read(img)
        res["text"] = r.get("text", "")
        res["lines"] = [{"text": ln["text"], "confidence": float(ln.get("confidence", 0)),
                         "box": ln["box"]} for ln in r.get("lines", []) if ln.get("text")]
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
    return res


# ---------------------------------------------------------------- writers
def _unique(path: Path, used: set) -> Path:
    p, n = path, 2
    while str(p).lower() in used:  # กันชื่อชนกันในรอบเดียวกัน (ไฟล์จากรอบก่อนจะถูกเขียนทับ)
        p = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    used.add(str(p).lower())
    return p


def _add_text_layer(page: pymupdf.Page, lines: list, scale: float, font: pymupdf.Font,
                    fontname: str, fontfile: str):
    """วางข้อความล่องหนทับตำแหน่งบรรทัดที่ OCR อ่านได้ ทำให้ค้นหา/คัดลอกได้"""
    derot = page.derotation_matrix
    for ln in lines:
        text = ln["text"].strip()
        if not text:
            continue
        (x0, y0), (x1, y1), _, (x3, y3) = ln["box"]
        # พิกัดที่มองเห็น (pt) -> พิกัดจริงของหน้า (ก่อนหมุน)
        p0 = pymupdf.Point(x0 * scale, y0 * scale) * derot
        p1 = pymupdf.Point(x1 * scale, y1 * scale) * derot
        p3 = pymupdf.Point(x3 * scale, y3 * scale) * derot
        width = abs(p1 - p0)
        height = abs(p3 - p0)
        if width < 1 or height < 1:
            continue
        fs = max(height * 0.8, 1.0)
        tl = font.text_length(text, fontsize=fs)
        if tl <= 0:
            continue
        sx = width / tl
        angle = math.degrees(math.atan2(p1.y - p0.y, p1.x - p0.x))
        # จุดฐานบรรทัด: ใกล้มุมล่างซ้าย (ขยับขึ้นเล็กน้อยตามแนวตั้งของกล่อง)
        base = p3 + (p0 - p3) * 0.2
        mat = pymupdf.Matrix(sx, 1) * pymupdf.Matrix(-angle)  # แกน y ของ PDF ชี้ลง
        page.insert_text(base, text, fontsize=fs, fontname=fontname, fontfile=fontfile,
                         render_mode=3, morph=(base, mat))


def write_searchable_pdf(src: Path, kind: str, pages: list[dict], out: Path, fontfile: str):
    font = pymupdf.Font(fontfile=fontfile)
    fontname = "ocrth"
    if kind == "pdf":
        doc = pymupdf.open(src)
        for pr in pages:
            if pr["source"] != "ocr" or not pr["lines"]:
                continue
            _add_text_layer(doc[pr["index"]], pr["lines"], 72.0 / pr["dpi"], font, fontname, fontfile)
    else:
        doc = pymupdf.open()
        for pr in pages:
            img, dpi = _load_image_frame(str(src), pr["index"])
            w, h = img.size
            scale = 72.0 / dpi
            page = doc.new_page(width=w * scale, height=h * scale)
            buf = io.BytesIO()
            hist = img.convert("L").histogram()
            if hist[0] + hist[255] == w * h:  # สแกนขาวดำ เก็บเป็น PNG 1 บิต (เล็กและคม)
                img.convert("1").save(buf, "PNG")
            else:
                img.save(buf, "JPEG", quality=85)
            page.insert_image(page.rect, stream=buf.getvalue())
            if pr["lines"]:
                _add_text_layer(page, pr["lines"], scale, font, fontname, fontfile)
    try:
        doc.subset_fonts()
    except Exception:  # noqa: BLE001
        pass
    doc.save(out, garbage=3, deflate=True)
    doc.close()


def write_txt(pages: list[dict], out: Path):
    parts = []
    for pr in pages:
        parts.append(f"===== หน้า {pr['index'] + 1} =====\n{pr['text'].strip()}")
    out.write_text("\n\n".join(parts) + "\n", encoding="utf-8-sig")


def write_docx(title: str, pages: list[dict], out: Path):
    from docx import Document
    from docx.enum.section import WD_ORIENT  # noqa: F401
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    FONT = "TH Sarabun New"
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Cm(2.54))

    def set_font(style_or_run, size):
        f = style_or_run.font
        f.name = FONT
        f.size = Pt(size)
        rpr = style_or_run.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = rpr.makeelement(qn("w:rFonts"), {})
            rpr.append(rfonts)
        for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(a), FONT)
        szcs = rpr.find(qn("w:szCs"))
        if szcs is None:
            szcs = rpr.makeelement(qn("w:szCs"), {})
            rpr.append(szcs)
        szcs.set(qn("w:val"), str(int(size * 2)))

    normal = doc.styles["Normal"]
    set_font(normal, 16)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.0

    p = doc.add_paragraph()
    r = p.add_run(title)
    r.bold = True
    set_font(r, 18)

    for i, pr in enumerate(pages):
        if i:
            doc.add_page_break()
        hp = doc.add_paragraph()
        hr = hp.add_run(f"หน้า {pr['index'] + 1}")
        hr.bold = True
        set_font(hr, 16)
        for line in pr["text"].strip().split("\n"):
            doc.add_paragraph(line)
    doc.save(out)


# ---------------------------------------------------------------- driver
class Cancelled(Exception):
    pass


def run_batch(files: list[Path], out_mode: str, out_dir: Path | None, formats: set[str],
              dpi: int = 200, use_text_layer: bool = True, workers: int = 2,
              log=print, progress=lambda done, total: None, stop_flag=lambda: False,
              engine_factory=None, on_result=None):
    """
    on_result(src_path, pages): เรียกทุกครั้งที่อ่านจบ 1 ไฟล์ (ใช้แสดงข้อความในหน้าต่าง)
    formats ว่างได้ = อ่านอย่างเดียว ไม่บันทึกไฟล์
    out_mode: 'beside' = โฟลเดอร์ OCR_output ข้างไฟล์ต้นฉบับ, 'folder' = ใช้ out_dir
    formats: {'pdf','txt','docx'}
    engine_factory: ใช้ตอนทดสอบเท่านั้น (แทนการโหลดโมเดลจริง)
    """
    fontfile = find_font()
    if "pdf" in formats and not fontfile:
        log("⚠ ไม่พบฟอนต์ภาษาไทยในเครื่อง ข้ามการสร้าง PDF ค้นหาได้")
        formats = formats - {"pdf"}

    # 1) นับหน้า
    jobs, meta = [], {}
    for fid, f in enumerate(files):
        try:
            n = count_pages(f)
        except Exception as e:  # noqa: BLE001
            log(f"✗ เปิดไฟล์ไม่ได้: {f.name} ({e})")
            continue
        kind = "pdf" if f.suffix.lower() in PDF_EXT else "image"
        meta[fid] = {"path": f, "kind": kind, "n": n, "pages": {}}
        for i in range(n):
            jobs.append({"file_id": fid, "path": str(f), "kind": kind, "index": i,
                         "dpi": dpi, "use_text_layer": use_text_layer})
    total = len(jobs)
    if not total:
        log("ไม่มีหน้าที่ต้องประมวลผล")
        return []
    log(f"เริ่มประมวลผล {len(meta)} ไฟล์ รวม {total} หน้า (ใช้ {workers} process)")
    progress(0, total)

    used, outputs = set(), []

    def finish_file(fid):
        m = meta[fid]
        pages = [m["pages"][i] for i in range(m["n"])]
        src: Path = m["path"]
        if on_result:
            try:
                on_result(src, pages)
            except Exception:  # noqa: BLE001
                pass
        dest = (src.parent / "OCR_output") if out_mode == "beside" else out_dir
        if formats:
            dest.mkdir(parents=True, exist_ok=True)
        stem = src.stem
        errs = [p for p in pages if p["error"]]
        for p in errs:
            log(f"  ✗ {src.name} หน้า {p['index'] + 1}: {p['error']}")
        confs = [ln["confidence"] for p in pages for ln in p["lines"]]
        low = sum(c < 0.9 for c in confs)
        n_text = sum(p["source"] == "text" for p in pages)
        note = f", ใช้ข้อความเดิมใน PDF {n_text} หน้า" if n_text else ""
        avg = f"{100 * sum(confs) / len(confs):.1f}%" if confs else "-"
        try:
            if "pdf" in formats:
                o = _unique(dest / f"{stem}_ocr.pdf", used)
                write_searchable_pdf(src, m["kind"], pages, o, fontfile)
                outputs.append(o)
            if "txt" in formats:
                o = _unique(dest / f"{stem}.txt", used)
                write_txt(pages, o)
                outputs.append(o)
            if "docx" in formats:
                o = _unique(dest / f"{stem}.docx", used)
                write_docx(src.name, pages, o)
                outputs.append(o)
            log(f"✓ {src.name}: {m['n']} หน้า, ความมั่นใจเฉลี่ย {avg}, "
                f"บรรทัดที่ควรตรวจ (<90%) {low} บรรทัด{note}")
        except PermissionError:
            log(f"✗ {src.name}: เขียนไฟล์ผลลัพธ์ไม่ได้ (ไฟล์อาจเปิดค้างอยู่ใน Word/PDF reader)")
        except Exception as e:  # noqa: BLE001
            log(f"✗ {src.name}: บันทึกผลไม่สำเร็จ ({type(e).__name__}: {e})")

    def collect(res):
        m = meta[res["file_id"]]
        m["pages"][res["index"]] = res
        if len(m["pages"]) == m["n"]:
            finish_file(res["file_id"])

    done = 0
    mdir = str(model_dir())
    if workers <= 1 or total == 1:
        if engine_factory:
            global _OCR
            _OCR = engine_factory()
        elif _OCR is None:
            init_worker(mdir, threads=max(1, min(4, os.cpu_count() or 1)))
        for j in jobs:
            if stop_flag():
                raise Cancelled()
            collect(ocr_page(j))
            done += 1
            progress(done, total)
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=init_worker,
                                 initargs=(mdir, 1)) as ex:
            futs = [ex.submit(ocr_page, j) for j in jobs]
            try:
                for fu in as_completed(futs):
                    if stop_flag():
                        raise Cancelled()
                    collect(fu.result())
                    done += 1
                    progress(done, total)
            except BaseException:
                for fu in futs:
                    fu.cancel()
                raise
    return outputs


# ---------------------------------------------------------------- อ่านภาพเดี่ยว (คลิปบอร์ด)
def ocr_image(img: Image.Image) -> dict:
    """อ่านภาพ PIL 1 ภาพใน process หลัก (ใช้กับภาพหน้าจอที่วางจากคลิปบอร์ด)"""
    if _OCR is None:
        init_worker(str(model_dir()), threads=max(1, min(4, os.cpu_count() or 1)))
    r = _OCR.read(img.convert("RGB"))
    return {"index": 0, "source": "ocr", "text": r.get("text", ""), "error": None,
            "lines": [{"text": ln["text"], "confidence": float(ln.get("confidence", 0)),
                       "box": ln["box"]} for ln in r.get("lines", []) if ln.get("text")]}

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
import re
import sys
import threading
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
    "/Library/Fonts/Tahoma.ttf",
    # Linux (Ubuntu/Debian: fonts-thai-tlwg, fonts-noto-core / Fedora: google-noto-sans-thai-fonts)
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    "/usr/share/fonts/google-noto/NotoSansThai-Regular.ttf",
    "/usr/share/fonts/google-noto-vf/NotoSansThai[wght].ttf",
    "/usr/share/fonts/opentype/tlwg/Loma.otf",
    "/usr/share/fonts/truetype/tlwg/Loma.ttf",
    "/usr/share/fonts/opentype/tlwg/Garuda.otf",
    "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
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
    if sys.platform.startswith("linux"):  # Linux: หาฟอนต์ไทยตัวไหนก็ได้ในเครื่อง
        for d in ("/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.local/share/fonts")):
            if os.path.isdir(d):
                for pat in ("**/*Thai*.ttf", "**/*Sarabun*.ttf", "**/tlwg/*.ttf", "**/tlwg/*.otf"):
                    hit = sorted(Path(d).glob(pat))
                    if hit:
                        return str(hit[0])
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
_LOCK = threading.Lock()  # กันหลาย thread เรียกโมเดลพร้อมกัน (เช่น OCR คลิปบอร์ด + หาข้อมูลส่วนบุคคล)


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
                        if job.get("tables"):
                            _add_tables(res, lambda: _tables().blocks_from_textlayer(page))
                        return res
                pix = page.get_pixmap(dpi=job["dpi"], alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        else:
            img, dpi = _load_image_frame(path, idx)
            res["dpi"] = dpi
            res["size"] = img.size
        with _LOCK:
            r = _OCR.read(img)
        res["text"] = r.get("text", "")
        res["lines"] = [{"text": ln["text"], "confidence": float(ln.get("confidence", 0)),
                         "box": ln["box"]} for ln in r.get("lines", []) if ln.get("text")]
        if job.get("tables"):
            _add_tables(res, lambda: _tables().blocks_from_ocr(img, res["lines"]))
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
    return res


def _tables():
    import thai_tables
    return thai_tables


def _add_tables(res: dict, make_blocks):
    """หาตารางในหน้า ถ้าเจอ: เก็บ blocks และเปลี่ยนข้อความให้ตารางคั่นช่องด้วย Tab"""
    try:
        blocks = make_blocks()
    except Exception as e:  # noqa: BLE001  ไม่ให้การหาตารางทำให้ทั้งหน้าพัง
        res["table_error"] = f"{type(e).__name__}: {e}"
        return
    if any(b["type"] == "table" for b in blocks):
        res["blocks"] = blocks
        res["text"] = _tables().blocks_to_text(blocks)


# ---------------------------------------------------------------- writers
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _xml(s) -> str:
    """ตัดอักขระที่ Word/Excel ไม่รับ (ผล OCR จากภาพถ่ายบางครั้งมีอักขระควบคุมปนมา)"""
    return _XML_BAD.sub("", s) if isinstance(s, str) else s


def _emit(jobs, used: set, outputs: list, log, who: str) -> list[str]:
    """เขียนผลทีละรูปแบบแยกกัน: รูปแบบหนึ่งพังไม่ทำให้รูปแบบอื่นหาย
    jobs = [(ชื่อรูปแบบ, path, ฟังก์ชันเขียน(path) -> คืน False ถ้าไม่ได้สร้างไฟล์)]"""
    made = []
    for fmt, path, fn in jobs:
        o = _unique(path, used)
        try:
            try:
                ok = fn(o)
            except PermissionError:  # ไฟล์ชื่อเดิมเปิดค้างอยู่ใน Word/Excel/PDF reader: บันทึกเป็นชื่อใหม่แทน
                o2 = _unique(path.with_name(f"{path.stem} (ใหม่){path.suffix}"), used)
                ok = fn(o2)
                log(f"  ! {o.name} เปิดค้างอยู่ในโปรแกรมอื่น บันทึกเป็น {o2.name} แทน")
                o = o2
            if ok is not False:
                outputs.append(o)
                made.append(fmt)
        except PermissionError:
            log(f"  ✗ {who}: เขียน {fmt} ไม่ได้ (ไฟล์เปิดค้างอยู่ หรือโฟลเดอร์ไม่อนุญาตให้เขียน)")
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ {who}: สร้าง {fmt} ไม่สำเร็จ ({type(e).__name__}: {e})")
    return made


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


def build_searchable_doc(src: Path, kind: str, pages: list[dict], fontfile: str) -> pymupdf.Document:
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
    return doc


def _save_pdf(doc: pymupdf.Document, out: Path):
    try:
        doc.subset_fonts()
    except Exception:  # noqa: BLE001
        pass
    doc.save(out, garbage=3, deflate=True)
    doc.close()


def write_searchable_pdf(src: Path, kind: str, pages: list[dict], out: Path, fontfile: str):
    _save_pdf(build_searchable_doc(src, kind, pages, fontfile), out)


def write_combined_pdf(items: list[tuple], out: Path, fontfile: str):
    """items = [(src, kind, pages)] เรียงตามลำดับ รวมเป็น PDF เดียว พร้อมบุ๊กมาร์กชื่อไฟล์"""
    big = pymupdf.open()
    toc = []
    for src, kind, pages in items:
        start = big.page_count + 1
        d = build_searchable_doc(src, kind, pages, fontfile)
        big.insert_pdf(d)
        d.close()
        toc.append([1, Path(src).name, start])
    try:
        big.set_toc(toc)
    except Exception:  # noqa: BLE001
        pass
    _save_pdf(big, out)


def _label(pr: dict) -> str:
    return pr.get("label") or f"หน้า {pr['index'] + 1}"


def write_txt(pages: list[dict], out: Path):
    parts = []
    for pr in pages:
        parts.append(f"===== {_label(pr)} =====\n{pr['text'].strip()}")
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
    r = p.add_run(_xml(title))
    r.bold = True
    set_font(r, 18)

    for i, pr in enumerate(pages):
        if i:
            doc.add_page_break()
        hp = doc.add_paragraph()
        hr = hp.add_run(_xml(_label(pr)))
        hr.bold = True
        set_font(hr, 16)
        if pr.get("blocks"):
            for b in pr["blocks"]:
                if b["type"] == "text":
                    doc.add_paragraph(_xml(b["text"]))
                    continue
                rows = [r for r in b.get("rows") or [] if r]
                if not rows:
                    continue
                ncol = max(len(r) for r in rows)
                tbl = doc.add_table(rows=len(rows), cols=ncol)
                tbl.style = "Table Grid"
                for ri, row in enumerate(rows):
                    for ci in range(ncol):
                        cell = tbl.cell(ri, ci)
                        cell.text = _xml(row[ci]) if ci < len(row) else ""
                        for para in cell.paragraphs:
                            for run in para.runs:
                                set_font(run, 14)
                                run.bold = ri == 0
                doc.add_paragraph("")
        else:
            for line in (pr.get("text") or "").strip().split("\n"):
                doc.add_paragraph(_xml(line))
    doc.save(out)


def _to_number(s: str):
    """แปลงข้อความตัวเลข (เช่น 1,250.50) เป็นตัวเลข แต่ไม่แตะเลขบัตร/รหัสที่ขึ้นต้นด้วย 0"""
    import re
    t = s.strip().replace(" ", "")
    if not re.fullmatch(r"-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?", t):
        return s
    digits = t.replace(",", "").replace("-", "").split(".")[0]
    if len(digits) >= 10 or (len(digits) > 1 and digits.startswith("0") and "." not in t):
        return s
    v = float(t.replace(",", ""))
    return int(v) if "." not in t else v


def write_xlsx(title: str, pages: list[dict], out: Path) -> int:
    """ตารางทั้งหมดในไฟล์ลง Excel: 1 ตาราง = 1 ชีต คืนจำนวนตาราง"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    n = 0
    for pr in pages:
        k = 0
        for b in pr.get("blocks") or []:
            if b["type"] != "table":
                continue
            n += 1
            k += 1
            ws = wb.create_sheet(f"หน้า{pr.get('gpage', pr['index'] + 1)}-ตาราง{k}"[:31])
            widths: dict[int, int] = {}
            for ri, row in enumerate(b["rows"], start=1):
                for ci, val in enumerate(row, start=1):
                    val = _xml(val)
                    v = _to_number(val) if ri > 1 else val
                    c = ws.cell(row=ri, column=ci, value=v)
                    c.font = Font(name="TH Sarabun New", size=14, bold=ri == 1)
                    c.alignment = Alignment(wrap_text=True, vertical="top")
                    c.border = border
                    if isinstance(v, float):
                        c.number_format = "#,##0.00"
                    elif isinstance(v, int) and abs(v) >= 1000:
                        c.number_format = "#,##0"
                    longest = max((len(x) for x in str(val).split("\n")), default=0)
                    widths[ci] = max(widths.get(ci, 6), min(50, longest + 2))
            for ci, w in widths.items():
                ws.column_dimensions[get_column_letter(ci)].width = w
            ws.freeze_panes = "A2"
    if n:
        wb.properties.title = _xml(title)
        wb.save(out)
    return n


# ---------------------------------------------------------------- driver
class Cancelled(Exception):
    pass


def run_batch(files: list[Path], out_mode: str, out_dir: Path | None, formats: set[str],
              dpi: int = 200, use_text_layer: bool = True, workers: int = 2,
              log=print, progress=lambda done, total: None, stop_flag=lambda: False,
              engine_factory=None, on_result=None, find_tables: bool = True,
              combine: bool = False, combine_name: str = ""):
    """
    on_result(src_path, pages): เรียกทุกครั้งที่อ่านจบ 1 ไฟล์ (ใช้แสดงข้อความในหน้าต่าง)
    formats ว่างได้ = อ่านอย่างเดียว ไม่บันทึกไฟล์
    out_mode: 'beside' = โฟลเดอร์ OCR_output ข้างไฟล์ต้นฉบับ, 'folder' = ใช้ out_dir
    formats: {'pdf','txt','docx'}
    engine_factory: ใช้ตอนทดสอบเท่านั้น (แทนการโหลดโมเดลจริง)
    combine: รวมผลทุกไฟล์ (ตามลำดับในรายการ) เป็นไฟล์เดียวต่อรูปแบบ ตั้งชื่อด้วย combine_name
    """
    combine = combine and len(files) > 1
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
                         "dpi": dpi, "use_text_layer": use_text_layer,
                         "tables": find_tables or "xlsx" in formats})
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
        if formats and not combine:
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
        if combine:  # เก็บไว้รวมตอนท้าย
            m["done"] = pages
            n_tab = sum(1 for p in pages for b in p.get("blocks") or [] if b["type"] == "table")
            tab_note = f", พบตาราง {n_tab} ตาราง" if n_tab else ""
            log(f"✓ {src.name}: {m['n']} หน้า, ความมั่นใจเฉลี่ย {avg}, "
                f"บรรทัดที่ควรตรวจ (<90%) {low} บรรทัด{note}{tab_note}")
            return
        n_tab = sum(1 for p in pages for b in p.get("blocks") or [] if b["type"] == "table")
        tab_note = f", พบตาราง {n_tab} ตาราง" if n_tab else ""
        log(f"✓ {src.name}: {m['n']} หน้า, ความมั่นใจเฉลี่ย {avg}, "
            f"บรรทัดที่ควรตรวจ (<90%) {low} บรรทัด{note}{tab_note}")
        _emit(_jobs_for(formats, dest, stem, src.name, pages, n_tab, fontfile,
                        pdf_fn=lambda o: write_searchable_pdf(src, m["kind"], pages, o, fontfile)),
              used, outputs, log, src.name)

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
    if combine and formats:
        write_combined(meta, files, out_mode, out_dir, formats, fontfile, combine_name, used, outputs, log)
    return outputs


def _safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    name = "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip(" .")
    return name[:120]


def write_combined(meta, files, out_mode, out_dir, formats, fontfile, combine_name, used, outputs, log):
    items = [meta[fid] for fid in sorted(meta) if "done" in meta[fid]]
    if not items:
        return
    first: Path = items[0]["path"]
    dest = (first.parent / "OCR_output") if out_mode == "beside" else out_dir
    dest.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(combine_name) or f"รวม_{first.stem}_และอีก{len(items) - 1}ไฟล์"
    # ใส่ป้ายชื่อไฟล์ให้แต่ละหน้า และเลขหน้ารวม (ใช้ตั้งชื่อชีต Excel)
    allpages, g = [], 0
    for m in items:
        for p in m["done"]:
            g += 1
            q = dict(p)
            q["label"] = f"{m['path'].name} — หน้า {p['index'] + 1}" if m["n"] > 1 else m["path"].name
            q["gpage"] = g
            allpages.append(q)
    n_tab = sum(1 for p in allpages for b in p.get("blocks") or [] if b["type"] == "table")
    made = _emit(_jobs_for(formats, dest, stem, stem, allpages, n_tab, fontfile,
                           pdf_fn=lambda o: write_combined_pdf(
                               [(m["path"], m["kind"], m["done"]) for m in items], o, fontfile)),
                 used, outputs, log, "ไฟล์รวม")
    if made:
        log(f"■ รวม {len(items)} ไฟล์ ({len(allpages)} หน้า) เป็นไฟล์เดียว: {stem} → {dest}")


def _jobs_for(formats, dest, stem, title, pages, n_tab, fontfile, pdf_fn):
    jobs = []
    if "pdf" in formats:
        jobs.append(("PDF", dest / f"{stem}_ocr.pdf", pdf_fn))
    if "txt" in formats:
        jobs.append((".txt", dest / f"{stem}.txt", lambda o: write_txt(pages, o)))
    if "docx" in formats:
        jobs.append(("Word", dest / f"{stem}.docx", lambda o: write_docx(title, pages, o)))
    if "xlsx" in formats and n_tab:
        jobs.append(("Excel", dest / f"{stem}_ตาราง.xlsx", lambda o: write_xlsx(title, pages, o) > 0))
    return jobs


# ---------------------------------------------------------------- อ่านภาพเดี่ยว (คลิปบอร์ด)
def ocr_image(img: Image.Image) -> dict:
    """อ่านภาพ PIL 1 ภาพใน process หลัก (ใช้กับภาพหน้าจอที่วางจากคลิปบอร์ด)"""
    with _LOCK:
        if _OCR is None:
            init_worker(str(model_dir()), threads=max(1, min(4, os.cpu_count() or 1)))
        r = _OCR.read(img.convert("RGB"))
    return {"index": 0, "source": "ocr", "text": r.get("text", ""), "error": None,
            "lines": [{"text": ln["text"], "confidence": float(ln.get("confidence", 0)),
                       "box": ln["box"]} for ln in r.get("lines", []) if ln.get("text")]}

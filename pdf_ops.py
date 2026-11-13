# -*- coding: utf-8 -*-
"""
งานแก้ไข PDF (ไม่มีส่วนหน้าจอ)
- บันทึกสิ่งที่ผู้ใช้วางบนหน้า: ข้อความไทย, กล่องขาว, ไฮไลต์, คาดดำ (ลบข้อมูลจริง)
- หาข้อมูลส่วนบุคคล: เลขบัตรประชาชน (ตรวจ checksum), เบอร์โทร, คำที่กำหนดเอง
- รวมไฟล์, แยกหน้า, บีบอัด
พิกัดของทุกอย่างเป็นหน่วย pt ตาม "หน้าที่มองเห็น" (หลังหมุนแล้ว) เหมือนที่แสดงบนจอ
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf

IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# ---------------------------------------------------------------- ฟอนต์สำหรับพิมพ์ข้อความ
_WIN = os.environ.get("WINDIR", r"C:\Windows")
_LOCAL = os.environ.get("LOCALAPPDATA", "")
FONT_CHOICES = [
    # (ชื่อที่แสดง, ชื่อฟอนต์ใน Tk, ไฟล์ที่เป็นไปได้)
    ("TH Sarabun New", "TH Sarabun New", [
        rf"{_WIN}\Fonts\THSarabunNew.ttf",
        rf"{_LOCAL}\Microsoft\Windows\Fonts\THSarabunNew.ttf",
        "/Library/Fonts/THSarabunNew.ttf",
        os.path.expanduser("~/Library/Fonts/THSarabunNew.ttf"),
        "/usr/share/fonts/truetype/THSarabunNew.ttf",
        os.path.expanduser("~/.local/share/fonts/THSarabunNew.ttf"),
        os.path.expanduser("~/.fonts/THSarabunNew.ttf"),
    ]),
    ("Tahoma", "Tahoma", [
        rf"{_WIN}\Fonts\tahoma.ttf",
        "/System/Library/Fonts/Supplemental/Tahoma.ttf",
        "/Library/Fonts/Tahoma.ttf",
    ]),
    ("Leelawadee UI", "Leelawadee UI", [rf"{_WIN}\Fonts\LeelawUI.ttf"]),
    # Linux
    ("Noto Sans Thai", "Noto Sans Thai", [
        "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
        "/usr/share/fonts/google-noto/NotoSansThai-Regular.ttf",
    ]),
    ("Laksaman (คล้าย Sarabun)", "Laksaman", [
        "/usr/share/fonts/opentype/tlwg/Laksaman.otf",
        "/usr/share/fonts/truetype/tlwg/Laksaman.ttf",
    ]),
    ("Garuda", "Garuda", ["/usr/share/fonts/opentype/tlwg/Garuda.otf", "/usr/share/fonts/truetype/tlwg/Garuda.ttf"]),
    ("Loma", "Loma", ["/usr/share/fonts/opentype/tlwg/Loma.otf", "/usr/share/fonts/truetype/tlwg/Loma.ttf"]),
]


def available_fonts() -> list[tuple[str, str, str]]:
    """คืน [(ชื่อแสดง, ชื่อ Tk, path)] เฉพาะฟอนต์แนะนำที่มีในเครื่อง (เร็ว ใช้ตอนเปิดโปรแกรม)"""
    out = []
    for name, tk, paths in FONT_CHOICES:
        for p in paths:
            if p and os.path.exists(p):
                out.append((name, tk, p))
                break
    return out


# ---------------------------------------------------------------- ฟอนต์ทั้งหมดในเครื่อง
def _font_dirs() -> list[Path]:
    if sys.platform == "win32":
        dirs = [Path(_WIN) / "Fonts", Path(_LOCAL) / "Microsoft" / "Windows" / "Fonts"]
    elif sys.platform == "darwin":
        dirs = [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    else:
        dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
                Path.home() / ".local" / "share" / "fonts"]
    return [d for d in dirs if d.is_dir()]


def _read_names(path: str) -> tuple[str, str] | None:
    """อ่านชื่อ family / style จากตาราง 'name' ของไฟล์ฟอนต์ .ttf/.otf"""
    import struct
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12:
                return None
            n_tables = struct.unpack(">H", head[4:6])[0]
            table = None
            for _ in range(n_tables):
                tag, _c, off, ln = struct.unpack(">4sIII", f.read(16))
                if tag == b"name":
                    table = (off, ln)
                    break
            if not table:
                return None
            f.seek(table[0])
            data = f.read(table[1])
        _fmt, count, str_off = struct.unpack(">HHH", data[:6])
        found: dict[int, tuple[int, str]] = {}
        for i in range(count):
            pid, eid, lid, nid, ln, off = struct.unpack(">HHHHHH", data[6 + 12 * i: 18 + 12 * i])
            if nid not in (1, 2, 16, 17):
                continue
            raw = data[str_off + off: str_off + off + ln]
            if pid == 3 or pid == 0:
                txt = raw.decode("utf-16-be", "ignore")
                rank = 3 if (pid == 3 and lid == 0x409) else 2
            elif pid == 1 and eid == 0:
                txt, rank = raw.decode("latin-1", "ignore"), 1
            else:
                continue
            if txt and (nid not in found or rank > found[nid][0]):
                found[nid] = (rank, txt)
        fam = found.get(1, (0, ""))[1]
        sub = found.get(2, (0, ""))[1]
        return (fam.strip(), sub.strip()) if fam else None
    except Exception:  # noqa: BLE001
        return None


_REGULAR = {"regular", "normal", "book", "roman", "medium", "standard", ""}
_SYS_FONTS: list[tuple[str, str, str]] | None = None


def system_fonts() -> list[tuple[str, str, str]]:
    """
    ฟอนต์ทั้งหมดในเครื่อง (เฉพาะแบบตัวปกติ) คืน [(ชื่อแสดง, ชื่อ Tk, path)]
    ฟอนต์ที่มีอักษรไทยอยู่ก่อน (ชื่อแสดงขึ้นต้นด้วย ไทย ·) ใช้เวลาสักครู่ ควรเรียกใน thread
    """
    global _SYS_FONTS
    if _SYS_FONTS is not None:
        return _SYS_FONTS
    by_fam: dict[str, tuple[int, str]] = {}
    for d in _font_dirs():
        for p in d.rglob("*"):
            if p.suffix.lower() not in (".ttf", ".otf"):
                continue
            names = _read_names(str(p))
            if not names:
                continue
            fam, sub = names
            score = 2 if sub.lower() in _REGULAR else 0
            if fam not in by_fam or score > by_fam[fam][0]:
                by_fam[fam] = (score, str(p))
    thai, other = [], []
    for fam, (_s, path) in by_fam.items():
        try:
            has_thai = pymupdf.Font(fontfile=path).has_glyph(0x0E01) != 0
        except Exception:  # noqa: BLE001
            continue
        (thai if has_thai else other).append((fam, fam, path))
    pref = [f[0] for f in FONT_CHOICES]
    thai.sort(key=lambda f: (pref.index(f[0]) if f[0] in pref else 99, f[0].lower()))
    other.sort(key=lambda f: f[0].lower())
    _SYS_FONTS = [(f"ไทย · {n}", t, p) for n, t, p in thai] + other
    return _SYS_FONTS


def default_font() -> tuple[str, str, str] | None:
    f = available_fonts()
    return f[0] if f else None


def to_thai_digits(s: str) -> str:
    return s.translate(str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙"))


def _css_font(font_path: str | None, size: float, color=(0, 0, 0), extra: str = ""):
    css = (f"* {{font-size:{size}pt; color:{_hex(color)}; line-height:1.2; margin:0; padding:0; {extra}}}")
    archive = None
    if font_path and os.path.exists(font_path):
        css = (f"@font-face {{font-family: thfont; src: url({Path(font_path).name});}}\n"
               + css.replace("* {", "* {font-family: thfont; "))
        archive = pymupdf.Archive(str(Path(font_path).parent))
    return css, archive


def watermark_doc(doc, text: str, font_path: str | None, scale: float = 0.8,
                  opacity: float = 0.15, angle: int = 45, color=(0.5, 0.5, 0.5)) -> None:
    """ใส่ลายน้ำข้อความกลางทุกหน้า (แก้เอกสารที่เปิดอยู่)"""
    size = 100
    try:
        tl = pymupdf.Font(fontfile=font_path).text_length(text, fontsize=size) if font_path else len(text) * size * 0.6
    except Exception:  # noqa: BLE001
        tl = len(text) * size * 0.6
    tmp = pymupdf.open()
    tp = tmp.new_page(width=tl + size * 0.6, height=size * 1.7)
    css, arch = _css_font(font_path, size, color, "text-align:center;")
    tp.insert_htmlbox(tp.rect, _html_escape(text), css=css, archive=arch, opacity=opacity)
    for page in doc:
        if page.rotation:
            page.remove_rotation()
        r = page.rect
        w, h = r.width * scale, r.height * scale
        box = pymupdf.Rect(r.x0 + (r.width - w) / 2, r.y0 + (r.height - h) / 2,
                           r.x0 + (r.width + w) / 2, r.y0 + (r.height + h) / 2)
        page.show_pdf_page(box, tmp, 0, rotate=angle, overlay=True, keep_proportion=True)


def _save_doc(doc, out_path):
    try:
        doc.subset_fonts()
    except Exception:  # noqa: BLE001
        pass
    doc.save(str(out_path), garbage=3, deflate=True)


def add_watermark(src, out_path, text: str, font_path: str | None, scale: float = 0.8,
                  opacity: float = 0.15, angle: int = 45, color=(0.5, 0.5, 0.5)) -> int:
    doc = open_as_pdf(src)
    watermark_doc(doc, text, font_path, scale, opacity, angle, color)
    n = doc.page_count
    _save_doc(doc, out_path)
    doc.close()
    return n


def page_numbers_doc(doc, template: str = "หน้า {n} / {total}", position: str = "bottom-center",
                     font_path: str | None = None, size: float = 12, start: int = 1,
                     thai_digits: bool = False, skip_first: bool = False, color=(0, 0, 0)) -> None:
    """
    ใส่ข้อความหัว/ท้ายกระดาษทุกหน้า ใช้ {n} = เลขหน้า, {total} = จำนวนหน้า
    position: top-left|top-center|top-right|bottom-left|bottom-center|bottom-right
    """
    total = doc.page_count
    vpos, hpos = position.split("-")
    css, arch = _css_font(font_path, size, color, f"text-align:{hpos};")
    margin = 28
    band = size * 2.2
    for i, page in enumerate(doc):
        if skip_first and i == 0:
            continue
        if page.rotation:
            page.remove_rotation()
        txt = template.replace("{n}", str(start + i)).replace("{total}", str(start + total - 1))
        if thai_digits:
            txt = to_thai_digits(txt)
        r = page.rect
        y0 = r.y0 + margin * 0.6 if vpos == "top" else r.y1 - margin * 0.6 - band
        page.insert_htmlbox(pymupdf.Rect(r.x0 + margin, y0, r.x1 - margin, y0 + band),
                            _html_escape(txt), css=css, archive=arch)


def add_page_numbers(src, out_path, template: str = "หน้า {n} / {total}", position: str = "bottom-center",
                     font_path: str | None = None, size: float = 12, start: int = 1,
                     thai_digits: bool = False, skip_first: bool = False, color=(0, 0, 0)) -> int:
    doc = open_as_pdf(src)
    page_numbers_doc(doc, template, position, font_path, size, start, thai_digits, skip_first, color)
    total = doc.page_count
    _save_doc(doc, out_path)
    doc.close()
    return total


def compress_doc(doc, dpi: int = 150, quality: int = 70) -> None:
    try:
        doc.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality=quality,
                           lossy=True, lossless=True, bitonal=False, color=True, gray=True)
    except Exception:  # noqa: BLE001
        pass


def doc_size(doc) -> int:
    return len(doc.tobytes(garbage=3, deflate=True))


# ---------------------------------------------------------------- เปิดไฟล์
def open_as_pdf(path: str | Path) -> pymupdf.Document:
    """เปิด PDF หรือรูปภาพ คืนเป็นเอกสาร PDF ในหน่วยความจำ (ไม่แตะไฟล์ต้นฉบับ)"""
    path = Path(path)
    src = pymupdf.open(path)
    if path.suffix.lower() in IMG_EXT or not src.is_pdf:
        pdf = pymupdf.open("pdf", src.convert_to_pdf())
        src.close()
        return pdf
    if src.needs_pass:
        raise ValueError("PDF ติดรหัสผ่าน")
    data = src.tobytes()
    src.close()
    return pymupdf.open("pdf", data)


# ---------------------------------------------------------------- บันทึกการแก้ไข
def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in rgb)


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace("\n", "<br/>"))


def apply_items(doc: pymupdf.Document, items: list[dict]) -> None:
    """
    วางสิ่งที่ผู้ใช้แก้ลงในเอกสารจริง
    item: {"type": "text"|"white"|"highlight"|"black", "page": i, "rect": [x0,y0,x1,y1],
           "text", "size", "color"(rgb 0-1), "font"(path)}
    """
    by_page: dict[int, list[dict]] = {}
    for it in items:
        by_page.setdefault(it["page"], []).append(it)
    for pno, its in by_page.items():
        page = doc[pno]
        if page.rotation:
            page.remove_rotation()  # ให้พิกัดหน้าตรงกับที่มองเห็น
        # 1) คาดดำ: ลบข้อความ/ภาพใต้กล่องจริง ๆ ไม่ใช่แค่ทับ
        blacks = [it for it in its if it["type"] in ("black", "erase")]
        for it in blacks:  # erase = ลบข้อความเดิมแล้วเติมขาว (ใช้กับ "แก้ข้อความเดิม")
            page.add_redact_annot(pymupdf.Rect(it["rect"]), fill=(0, 0, 0) if it["type"] == "black" else (1, 1, 1))
        if blacks:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        # 2) กล่องขาวและไฮไลต์ก่อน แล้วค่อยข้อความ (ข้อความจะไม่ถูกสีทับ)
        order = {"white": 0, "highlight": 1, "text": 2}
        for it in sorted((i for i in its if i["type"] not in ("black", "erase")), key=lambda i: order[i["type"]]):
            r = pymupdf.Rect(it["rect"])
            if it["type"] == "white":
                page.draw_rect(r, color=None, fill=(1, 1, 1), overlay=True)
            elif it["type"] == "highlight":
                page.draw_rect(r, color=None, fill=(1, 0.92, 0.2), fill_opacity=0.4, overlay=True)
            elif it["type"] == "text" and it.get("text", "").strip():
                font = it.get("font")
                size = float(it.get("size", 14))
                box = pymupdf.Rect(r.x0, r.y0, max(r.x1, page.rect.x1 - 1), page.rect.y1)
                css, archive = _css_font(font, size, it.get("color", (0, 0, 0)))
                page.insert_htmlbox(box, _html_escape(it["text"]), css=css, archive=archive,
                                    scale_low=1)


def save_with_items(doc: pymupdf.Document, items: list[dict], out_path: str | Path) -> None:
    """บันทึกเป็นไฟล์ใหม่ (ไม่แก้เอกสารที่เปิดอยู่)"""
    work = pymupdf.open("pdf", doc.tobytes())
    apply_items(work, items)
    try:
        work.subset_fonts()
    except Exception:  # noqa: BLE001
        pass
    work.save(str(out_path), garbage=3, deflate=True)
    work.close()


# ---------------------------------------------------------------- หาข้อมูลส่วนบุคคล
def thai_id_valid(d: str) -> bool:
    if len(d) != 13 or not d.isdigit() or d[0] == "0":
        return False
    s = sum(int(d[i]) * (13 - i) for i in range(12))
    return (11 - s % 11) % 10 == int(d[12])


_SEP = r"[ \-–]?"
ID_RE = re.compile(rf"(?<!\d)(\d{_SEP}\d{{4}}{_SEP}\d{{5}}{_SEP}\d{{2}}{_SEP}\d)(?!\d)")
PHONE_RE = re.compile(rf"(?<!\d)(0\d{{1,2}}{_SEP}\d{{3}}{_SEP}\d{{4}})(?!\d)")


def find_pii(text: str, kinds: set[str], keywords: list[str] | None = None) -> list[tuple[int, int, str]]:
    """คืนตำแหน่ง [(start, end, ชนิด)] ในข้อความ"""
    found: list[tuple[int, int, str]] = []
    if "id" in kinds:
        for m in ID_RE.finditer(text):
            if thai_id_valid(re.sub(r"\D", "", m.group(1))):
                found.append((m.start(1), m.end(1), "เลขบัตรประชาชน"))
    if "phone" in kinds:
        for m in PHONE_RE.finditer(text):
            if not any(a <= m.start(1) < b for a, b, _ in found):
                found.append((m.start(1), m.end(1), "เบอร์โทร"))
    for kw in keywords or []:
        kw = kw.strip()
        if not kw:
            continue
        for m in re.finditer(re.escape(kw), text):
            found.append((m.start(), m.end(), "คำที่กำหนด"))
    return sorted(found)


def _visual_len(s: str) -> int:
    """จำนวนตัวอักษรที่กินที่แนวนอน (ไม่นับสระบน/ล่าง วรรณยุกต์)"""
    return sum(1 for ch in s if unicodedata.category(ch) not in ("Mn", "Me", "Cf"))


def span_rect_in_line(line_text: str, box_pts: list, start: int, end: int, pad_chars: float = 0.35):
    """
    ประมาณกรอบของข้อความช่วง [start:end] ในบรรทัด OCR จากสัดส่วนจำนวนตัวอักษร
    box_pts = 4 มุมของบรรทัด (หน่วย pt) เรียง บนซ้าย บนขวา ล่างขวา ล่างซ้าย
    """
    n = max(1, _visual_len(line_text))
    a = max(0.0, (_visual_len(line_text[:start]) - pad_chars) / n)
    b = min(1.0, (_visual_len(line_text[:end]) + pad_chars) / n)
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = box_pts
    xs, ys = [], []
    for t in (a, b):
        xs += [x0 + (x1 - x0) * t, x3 + (x2 - x3) * t]
        ys += [y0 + (y1 - y0) * t, y3 + (y2 - y3) * t]
    h = (max(ys) - min(ys)) * 0.12
    return [min(xs), min(ys) - h, max(xs), max(ys) + h]


def find_pii_on_page(page: pymupdf.Page, kinds: set[str], keywords: list[str],
                     ocr_func=None, dpi: int = 200) -> list[tuple[list, str]]:
    """
    คืน [(rect ในพิกัดที่มองเห็น, ชนิด)] ของข้อมูลส่วนบุคคลบนหน้า
    ใช้ข้อความเดิมใน PDF ถ้ามี ไม่งั้นใช้ OCR (ocr_func รับภาพ PIL คืน dict แบบ ocr_core)
    """
    out: list[tuple[list, str]] = []
    rot = page.rotation_matrix
    existing = page.get_text("text")
    if len(existing.replace(" ", "").replace("\n", "")) >= 30:
        for line in existing.splitlines():
            for s, e, kind in find_pii(line, kinds, keywords):
                frag = line[s:e]
                for r in page.search_for(frag):
                    # หดด้านข้างเล็กน้อย ไม่ให้กินตัวอักษรข้างเคียง (ตัวที่ซ้อนกล่องจะถูกลบทั้งตัวอยู่แล้ว)
                    r = pymupdf.Rect(r).normalize()
                    dx = min(r.width * 0.2, r.height * 0.25)
                    r = pymupdf.Rect(r.x0 + dx, r.y0 - 1, r.x1 - dx, r.y1 + 1)
                    rr = pymupdf.Rect(r * rot).normalize() if page.rotation else r
                    item = ([rr.x0, rr.y0, rr.x1, rr.y1], kind)
                    if item not in out:
                        out.append(item)
        return out
    if ocr_func is None:
        return out
    from PIL import Image
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    res = ocr_func(img)
    k = 72.0 / dpi
    for ln in res.get("lines", []):
        text = ln["text"]
        pts = [[x * k, y * k] for x, y in ln["box"]]
        for s, e, kind in find_pii(text, kinds, keywords):
            out.append((span_rect_in_line(text, pts, s, e), kind))
    return out


# ---------------------------------------------------------------- เครื่องมือ PDF
def merge(files: list[str | Path], out_path: str | Path) -> int:
    out = pymupdf.open()
    for f in files:
        src = open_as_pdf(f)
        out.insert_pdf(src)
        src.close()
    n = out.page_count
    out.save(str(out_path), garbage=3, deflate=True)
    out.close()
    return n


def parse_ranges(spec: str, n_pages: int) -> list[int]:
    """'1-3, 5, 8-' -> [0,1,2,4,7,...] (เลขหน้าเริ่มที่ 1)"""
    pages: list[int] = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else n_pages
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ValueError(f"รูปแบบหน้าไม่ถูกต้อง: {part}")
        if a < 1 or b > n_pages or a > b:
            raise ValueError(f"หน้า {part} อยู่นอกช่วง 1-{n_pages}")
        pages.extend(range(a - 1, b))
    if not pages:
        raise ValueError("ไม่ได้ระบุหน้า")
    return pages


def extract_pages(src: str | Path, spec: str, out_path: str | Path) -> int:
    doc = open_as_pdf(src)
    pages = parse_ranges(spec, doc.page_count)
    doc.select(pages)
    doc.save(str(out_path), garbage=3, deflate=True)
    doc.close()
    return len(pages)


def split_each_page(src: str | Path, out_dir: str | Path) -> int:
    doc = open_as_pdf(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(src).stem
    width = len(str(doc.page_count))
    for i in range(doc.page_count):
        one = pymupdf.open()
        one.insert_pdf(doc, from_page=i, to_page=i)
        one.save(str(out_dir / f"{stem}_หน้า{str(i + 1).zfill(width)}.pdf"), garbage=3, deflate=True)
        one.close()
    n = doc.page_count
    doc.close()
    return n


def compress(src: str | Path, out_path: str | Path, dpi: int = 150, quality: int = 70) -> tuple[int, int]:
    """ลดขนาด: ย่อรูปที่ละเอียดเกิน dpi แล้วบีบ JPEG คืน (ขนาดเดิม, ขนาดใหม่) เป็นไบต์"""
    before = os.path.getsize(src)
    doc = open_as_pdf(src)
    try:
        doc.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality=quality,
                           lossy=True, lossless=True, bitonal=False, color=True, gray=True)
    except Exception:  # noqa: BLE001  (PyMuPDF รุ่นเก่า)
        pass
    doc.save(str(out_path), garbage=4, deflate=True, clean=True)
    doc.close()
    return before, os.path.getsize(out_path)


def unique_path(path: Path) -> Path:
    p, n = path, 2
    while p.exists():
        p = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return p


def open_file(path: str | Path):
    p = str(path)
    if sys.platform == "win32":
        os.startfile(p)  # noqa: S606
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", p])

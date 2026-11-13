# -*- coding: utf-8 -*-
"""
หาตารางในหน้าเอกสาร แล้วจัดข้อความลงช่อง
- PDF ที่มีข้อความอยู่แล้ว: ใช้ page.find_tables() ของ PyMuPDF
- ภาพสแกน: หาเส้นตารางด้วย OpenCV แล้วเอาบรรทัดจาก OCR ไปใส่ช่องตามตำแหน่ง
ผลลัพธ์ของแต่ละหน้าคือ "blocks" เรียงจากบนลงล่าง:
    {"type": "text", "text": str, "y": float}
    {"type": "table", "rows": [[str, ...], ...], "y": float, "bbox": [x0, y0, x1, y1]}
"""
from __future__ import annotations

import unicodedata

import numpy as np


# ---------------------------------------------------------------- ช่วยเหลือ
def _vlen(s: str) -> int:
    return sum(1 for ch in s if unicodedata.category(ch) not in ("Mn", "Me", "Cf"))


def _cut_by_fraction(text: str, a: float, b: float) -> str:
    """ตัดข้อความตามสัดส่วนความกว้าง a..b (0-1) โดยไม่ตัดกลางสระ/วรรณยุกต์"""
    n = _vlen(text)
    if n == 0:
        return ""
    ia, ib = round(a * n), round(b * n)
    out, k = [], 0
    for ch in text:
        base = unicodedata.category(ch) not in ("Mn", "Me", "Cf")
        if base:
            k += 1
        idx = k - 1 if base else k - 1
        if ia <= idx < ib:
            out.append(ch)
    return "".join(out).strip()


def split_at_fractions(text: str, fracs: list[float]) -> list[str]:
    """แบ่งข้อความเป็นช่วงตามสัดส่วนความกว้าง ถ้าใกล้ช่องว่างจะตัดที่ช่องว่าง (ไม่ตัดกลางคำ)"""
    base = [i for i, ch in enumerate(text) if unicodedata.category(ch) not in ("Mn", "Me", "Cf")]
    n = len(base)
    if n == 0:
        return [""] * (len(fracs) + 1)
    spaces = [i for i, ch in enumerate(text) if ch.isspace()]
    cuts = []
    for f in fracs:
        k = min(n - 1, max(0, round(f * n)))
        pos = base[k]
        near = [sp for sp in spaces if abs(base.index(sp) - k) <= 4] if spaces else []
        if near:
            pos = min(near, key=lambda sp: abs(base.index(sp) - k))
        cuts.append(pos)
    parts, prev = [], 0
    for c in cuts:
        c = max(c, prev)
        parts.append(text[prev:c].strip())
        prev = c
    parts.append(text[prev:].strip())
    return parts


def _cluster(positions: np.ndarray, gap: int = 4) -> list[float]:
    """รวมตำแหน่งพิกเซลที่อยู่ติดกันเป็นเส้นเดียว คืนค่ากึ่งกลาง"""
    if len(positions) == 0:
        return []
    out, start, prev = [], positions[0], positions[0]
    for p in positions[1:]:
        if p - prev > gap:
            out.append((start + prev) / 2)
            start = p
        prev = p
    out.append((start + prev) / 2)
    return out


def _box(ln):
    xs = [p[0] for p in ln["box"]]
    ys = [p[1] for p in ln["box"]]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------- หาเส้นตารางจากภาพ
def _line_positions(m: np.ndarray, span: int, axis: int) -> list[float]:
    """หาตำแหน่งเส้นในกรอบตาราง ทนกับภาพถ่ายที่เส้นเอียงเล็กน้อย
    axis=0: เส้นแนวนอน (คืนค่า y), axis=1: เส้นแนวตั้ง (คืนค่า x)"""
    import cv2

    # เส้นเอียงจะขาดเป็นขั้นบันได: ขยายตั้งฉากกับเส้นนิดหน่อยให้ต่อกันเป็นชิ้นเดียว
    k = np.ones((7, 1) if axis == 0 else (1, 7), np.uint8)
    mm = cv2.dilate((m > 0).astype(np.uint8), k)
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(mm, connectivity=8)
    pos = []
    for i in range(1, n):
        length = stats[i, cv2.CC_STAT_WIDTH] if axis == 0 else stats[i, cv2.CC_STAT_HEIGHT]
        thick = stats[i, cv2.CC_STAT_HEIGHT] if axis == 0 else stats[i, cv2.CC_STAT_WIDTH]
        if length > 0.25 * span and thick < max(40, 0.06 * length):
            pos.append(cent[i][1] if axis == 0 else cent[i][0])
    return _cluster(np.array(sorted(pos)), gap=12) if pos else []


def detect_grids(img) -> list[dict]:
    """คืน [{"bbox": (x0,y0,x1,y1), "xs": [...], "ys": [...]}] พิกัดพิกเซล"""
    import cv2

    arr = np.asarray(img.convert("L")) if hasattr(img, "convert") else img
    H, W = arr.shape[:2]
    bw = cv2.adaptiveThreshold(255 - arr, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, W // 35), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, H // 70)))
    horiz = cv2.dilate(cv2.erode(bw, hk), hk)
    vert = cv2.dilate(cv2.erode(bw, vk), vk)
    mask = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    grids, hlines = [], []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w >= W * 0.15 and h < H * 0.02:
            hlines.append((x, y, w, h))  # เส้นแนวนอนเดี่ยว อาจเป็นตารางแบบไม่มีเส้นตั้ง
            continue
        if w < W * 0.15 or h < H * 0.02:
            continue
        ys = _line_positions(horiz[y:y + h, x:x + w], w, axis=0)
        xs = _line_positions(vert[y:y + h, x:x + w], h, axis=1)
        ys = [y + v for v in ys]
        xs = [x + v for v in xs]
        # เติมขอบที่ไม่มีเส้น (ตารางเปิดข้าง)
        if not xs or xs[0] - x > 8:
            xs = [float(x)] + xs
        if xs[-1] < x + w - 8:
            xs = xs + [float(x + w)]
        if not ys or ys[0] - y > 8:
            ys = [float(y)] + ys
        if ys[-1] < y + h - 8:
            ys = ys + [float(y + h)]
        if len(ys) < 3:
            continue  # กรอบสี่เหลี่ยม/แถวเดียว ไม่ใช่ตาราง
        edges = (x <= 6) + (y <= 6) + (x + w >= W - 6) + (y + h >= H - 6)
        if edges >= 2:
            continue  # ขอบกระดาษ/เงาในภาพถ่าย ไม่ใช่ตาราง
        grids.append({"bbox": (x, y, x + w, y + h), "xs": xs, "ys": ys})
    # รวมเส้นแนวนอนที่ยาวใกล้เคียงกันและอยู่ห่างกันไม่มาก เป็นตารางแบบเปิด (มีแต่เส้นแนวนอน)
    hlines.sort(key=lambda t: t[1])
    group: list = []

    def flush():
        if len(group) >= 3:
            gx0 = min(t[0] for t in group)
            gx1 = max(t[0] + t[2] for t in group)
            ys = [t[1] + t[3] / 2 for t in group]
            bbox = (gx0, int(ys[0]), gx1, int(ys[-1]) + 1)
            if not any(g["bbox"][0] <= gx0 + 5 and g["bbox"][2] >= gx1 - 5 and g["bbox"][1] <= ys[0] + 5
                       and g["bbox"][3] >= ys[-1] - 5 for g in grids):
                grids.append({"bbox": bbox, "xs": [float(gx0), float(gx1)], "ys": ys})

    for t in hlines:
        if group:
            last = group[-1]
            similar = abs(t[0] - group[0][0]) < W * 0.05 and abs(t[0] + t[2] - group[0][0] - group[0][2]) < W * 0.05
            if similar and t[1] - last[1] < H * 0.08:
                group.append(t)
                continue
            flush()
        group = [t]
    flush()
    grids.sort(key=lambda g: g["bbox"][1])
    return grids


def _columns_from_gaps(lines, x0, x1, min_gap) -> list[float]:
    """ตารางที่มีแต่เส้นแนวนอน: หาคอลัมน์จากช่องว่างแนวตั้งระหว่างข้อความ"""
    cover = np.zeros(int(x1 - x0) + 1, dtype=bool)
    for ln in lines:
        a, _, b, _ = _box(ln)
        cover[max(0, int(a - x0)):max(0, int(b - x0)) + 1] = True
    xs, run = [float(x0)], 0
    for i, c in enumerate(cover):
        if not c:
            run += 1
        else:
            if run >= min_gap and i - run > 0:
                xs.append(x0 + i - run / 2)
            run = 0
    xs.append(float(x1))
    return xs


def fill_grid(grid: dict, lines: list[dict], page_w: float) -> tuple[list[list[str]], set[int]]:
    """เอาบรรทัด OCR ใส่ช่องตาราง คืน (rows, index ของบรรทัดที่ใช้แล้ว)"""
    x0, y0, x1, y1 = grid["bbox"]
    inside = [i for i, ln in enumerate(lines)
              if x0 - 2 <= (_box(ln)[0] + _box(ln)[2]) / 2 <= x1 + 2
              and y0 - 2 <= (_box(ln)[1] + _box(ln)[3]) / 2 <= y1 + 2]
    xs, ys = list(grid["xs"]), list(grid["ys"])
    if len(xs) == 2 and len(ys) >= 3:
        xs = _columns_from_gaps([lines[i] for i in inside], x0, x1, max(12, page_w * 0.015))
    nr, nc = len(ys) - 1, len(xs) - 1
    cells: list[list[list[tuple[float, float, str]]]] = [[[] for _ in range(nc)] for _ in range(nr)]
    for i in inside:
        ln = lines[i]
        bx0, by0, bx1, by1 = _box(ln)
        cy = (by0 + by1) / 2
        r = next((k for k in range(nr) if ys[k] - 2 <= cy <= ys[k + 1] + 2), None)
        if r is None:
            continue
        width = max(1.0, bx1 - bx0)
        spans = []
        for c in range(nc):
            ov = min(bx1, xs[c + 1]) - max(bx0, xs[c])
            if ov > 0:
                spans.append((c, ov))
        if not spans:
            continue
        big = [s for s in spans if s[1] / width > 0.15]
        if len(big) <= 1:  # อยู่ช่องเดียว
            c = max(spans, key=lambda s: s[1])[0]
            cells[r][c].append((cy, bx0, ln["text"].strip()))
        else:  # บรรทัด OCR คร่อมหลายช่อง: แบ่งข้อความตามเส้นแบ่งช่อง
            big.sort()
            fracs = [(xs[c + 1] - bx0) / width for c, _ov in big[:-1]]
            for (c, _ov), part in zip(big, split_at_fractions(ln["text"], fracs)):
                if part:
                    cells[r][c].append((cy, max(bx0, xs[c]), part))
    rows = []
    for r in range(nr):
        row = []
        for c in range(nc):
            items = sorted(cells[r][c])
            txt, last_y = "", None
            for y, _x, t in items:
                if last_y is None:
                    txt = t
                elif abs(y - last_y) < 8:
                    txt += " " + t
                else:
                    txt += "\n" + t
                last_y = y
            row.append(txt)
        rows.append(row)
    # ตัดแถว/คอลัมน์ที่ว่างทั้งหมด
    rows = [r for r in rows if any(c.strip() for c in r)]
    if rows:
        keep = [c for c in range(len(rows[0])) if any(r[c].strip() for r in rows)]
        rows = [[r[c] for c in keep] for r in rows]
    return rows, set(inside)


# ---------------------------------------------------------------- สร้าง blocks ของหน้า
def blocks_from_ocr(img, lines: list[dict]) -> list[dict]:
    """ภาพสแกน: หาตาราง + ข้อความนอกตาราง"""
    W = img.size[0] if hasattr(img, "size") else img.shape[1]
    blocks, used = [], set()
    for g in detect_grids(img):
        rows, idx = fill_grid(g, lines, W)
        if rows and (len(rows) >= 2 or (rows and len(rows[0]) >= 2)):
            blocks.append({"type": "table", "rows": rows, "y": g["bbox"][1], "bbox": list(g["bbox"])})
            used |= idx
    for i, ln in enumerate(lines):
        if i not in used and ln["text"].strip():
            blocks.append({"type": "text", "text": ln["text"].strip(), "y": _box(ln)[1]})
    blocks.sort(key=lambda b: b["y"])
    return blocks


def blocks_from_textlayer(page) -> list[dict]:
    """PDF ที่มีข้อความ: ใช้ตัวหาตารางของ PyMuPDF"""
    import pymupdf
    blocks, boxes = [], []
    try:
        found = page.find_tables()
        tabs = list(found.tables) if hasattr(found, "tables") else list(found)
    except Exception:  # noqa: BLE001
        tabs = []
    for t in tabs:
        rows = []
        for row in t.rows:
            cells = []
            for cb in row.cells:
                if cb is None:
                    cells.append("")
                    continue
                txt = page.get_text("text", clip=pymupdf.Rect(cb), sort=True).strip()
                cells.append("\n".join(x.strip() for x in txt.splitlines() if x.strip()))
            rows.append(cells)
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        bb = pymupdf.Rect(t.bbox)
        boxes.append(bb)
        blocks.append({"type": "table", "rows": rows, "y": bb.y0, "bbox": list(bb)})
    for b in page.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            r = pymupdf.Rect(ln["bbox"])
            c = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            if any(bb.contains(c) for bb in boxes):
                continue
            t = "".join(s["text"] for s in ln["spans"]).strip()
            if t:
                blocks.append({"type": "text", "text": t, "y": r.y0})
    blocks.sort(key=lambda b: b["y"])
    return blocks


def blocks_to_text(blocks: list[dict]) -> str:
    """ข้อความล้วน: ตารางคั่นช่องด้วย Tab (คัดลอกไปวางใน Excel แล้วแยกช่องให้เอง)"""
    out = []
    for b in blocks:
        if b["type"] == "text":
            out.append(b["text"])
        else:
            out.append("")
            for r in b["rows"]:
                out.append("\t".join(c.replace("\n", " ").replace("\t", " ") for c in r))
            out.append("")
    return "\n".join(out).strip()


def count_tables(pages: list[dict]) -> int:
    return sum(1 for p in pages for b in p.get("blocks") or [] if b["type"] == "table")

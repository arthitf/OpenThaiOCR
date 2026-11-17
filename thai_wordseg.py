# -*- coding: utf-8 -*-
"""ตัดคำภาษาไทยด้วย OpenThai2.0-LATTE-WordSeg-Lite (iApp Technology, CC BY-SA 4.0)
- tokenize / tokenize_batch : ตัดคำ (หน่วย long / short / minimal)
- fix_ocr_spaces            : ลบช่องว่างที่ OCR ใส่ผิดกลางคำ เช่น "โรง พยาบาล" -> "โรงพยาบาล"
ข้อความเดิมไม่ถูกแก้ไขตัวอักษรใดๆ (ไม่แปลงเลขไทย ไม่เปลี่ยนสะกด) นอกจากลบช่องว่างกลางคำ
"""
from __future__ import annotations

import re
import threading
from collections import Counter

UNITS = {"long": "คำประสม (ยาว)", "short": "คำ (มาตรฐาน)", "minimal": "หน่วยย่อย (สั้น)"}

_SEG = None
_LOCK = threading.Lock()


def segmenter():
    """โหลดโมเดลครั้งเดียว (ประมาณครึ่งวินาที)"""
    global _SEG
    with _LOCK:
        if _SEG is None:
            import wordseg_latte
            _SEG = wordseg_latte.Segmenter(unit="short", threads=1)
        return _SEG


def available() -> bool:
    try:
        segmenter()
        return True
    except Exception:  # noqa: BLE001
        return False


def tokenize(text: str, unit: str = "short", keep_whitespace: bool = True) -> list[str]:
    return segmenter().tokenize(text, keep_whitespace=keep_whitespace, unit=unit)


def tokenize_batch(texts: list[str], unit: str = "short", keep_whitespace: bool = True) -> list[list[str]]:
    if not texts:
        return []
    return segmenter().tokenize_batch(list(texts), keep_whitespace=keep_whitespace, unit=unit)


def in_dictionary(word: str) -> bool:
    import numpy as np
    import wordseg_latte as w
    lex = segmenter().lexicon
    table = lex.hashes.get(len(word))
    if table is None or len(word) < 2:
        return False
    h = w._hash_rows(w._codes(word).reshape(1, len(word)))
    i = min(int(np.searchsorted(table, h[0])), len(table) - 1)
    return bool(table[i] == h[0])


# ---------------------------------------------------------------- แก้ช่องว่างกลางคำจาก OCR
_THAI_LETTER = re.compile(r"[ก-ฮะ-ฺเ-ๅ็-๎]")
# ช่องว่าง 1-2 ตัวที่มีอักษรไทยทั้งสองฝั่ง (ไม่นับ ฯ ๆ และเลขไทย ซึ่งเว้นวรรคต่อท้ายได้ตามปกติ)
_GAP = re.compile(r"(?<=[ก-ฮะ-ฺเ-ๅ็-๎]) {1,2}(?=[ก-ฮเ-ไ])")


def fix_ocr_spaces(text: str) -> tuple[str, int]:
    """ลบช่องว่างระหว่างอักษรไทยที่อยู่ "กลางคำ" คืน (ข้อความใหม่, จำนวนที่ลบ)
    ลบเฉพาะเมื่อ (1) ตัวตัดคำ (หน่วย long) ไม่ตัดคำตรงช่องว่างนั้น (2) คำที่ได้หลังต่อกันอยู่ในพจนานุกรม และ
    (3) อย่างน้อยหนึ่งฝั่งของช่องว่างไม่ใช่คำ เช่น "พยา บาล" -> "พยาบาล"
    แต่ "สิทธิ บัตรทอง" "เบา หวาน" ไม่ต่อ เพราะทั้งสองฝั่งเป็นคำอยู่แล้ว (อาจเว้นวรรคตั้งใจ)"""
    if not text or " " not in text:
        return text, 0
    out_lines, total = [], 0
    for line in text.split("\n"):
        gaps = list(_GAP.finditer(line))
        if not gaps:
            out_lines.append(line)
            continue
        # ข้อความที่ตัดช่องว่างตัวเลือกออก + ตำแหน่งรอยต่อในข้อความใหม่
        parts, joins, pos, last = [], [], 0, 0
        for m in gaps:
            parts.append(line[last:m.start()])
            pos += m.start() - last
            joins.append((pos, m.group(0)))
            last = m.end()
        parts.append(line[last:])
        joined = "".join(parts)
        toks = tokenize(joined, unit="long")
        spans, p = [], 0
        for t in toks:
            spans.append((p, p + len(t), t))
            p += len(t)
        remove = set()
        cuts = [jp for jp, _ in joins]
        for k, jp in enumerate(cuts):
            for a, b, t in spans:
                if a < jp < b:          # รอยต่ออยู่กลางคำ
                    # ส่วนซ้าย/ขวาของช่องว่างภายในคำนั้น (ถึงช่องว่างตัวถัดไป)
                    lo = max([a] + [c for c in cuts if a < c < jp])
                    hi = min([b] + [c for c in cuts if jp < c < b])
                    left, right = joined[lo:jp], joined[jp:hi]
                    # ต่อเฉพาะเมื่อคำเต็มอยู่ในพจนานุกรม และช่องว่างผ่ากลาง "คำ" จริง (มีส่วนที่ไม่ใช่คำ)
                    # ถ้าทั้งสองส่วนเป็นคำอยู่แล้ว (เช่น "สิทธิ บัตรทอง") ถือว่าเว้นวรรคตั้งใจ ไม่ต่อ
                    if in_dictionary(t) and not (in_dictionary(left) and in_dictionary(right)):
                        remove.add(k)
                    break
        if not remove:
            out_lines.append(line)
            continue
        res, last = [], 0
        for k, m in enumerate(gaps):
            res.append(line[last:m.start()])
            if k not in remove:
                res.append(m.group(0))
            last = m.end()
        res.append(line[last:])
        out_lines.append("".join(res))
        total += len(remove)
    return "\n".join(out_lines), total


def fix_page(res: dict) -> int:
    """แก้ช่องว่างในผล OCR 1 หน้า (ข้อความ, บรรทัด, ตาราง) คืนจำนวนที่แก้"""
    n = 0
    if res.get("text"):
        res["text"], n = fix_ocr_spaces(res["text"])
    for ln in res.get("lines") or []:
        ln["text"], _ = fix_ocr_spaces(ln.get("text", ""))
    for b in res.get("blocks") or []:
        if b.get("type") == "text" and b.get("text"):
            b["text"], _ = fix_ocr_spaces(b["text"])
        for row in b.get("rows") or []:
            for i, c in enumerate(row):
                if isinstance(c, str) and c:
                    row[i], _ = fix_ocr_spaces(c)
    return n


# ---------------------------------------------------------------- นับคำ
_PUNCT = re.compile(r"^[\W_]+$", re.UNICODE)


def is_word(tok: str) -> bool:
    t = tok.strip()
    return bool(t) and not _PUNCT.match(t)


def word_counts(token_lists) -> Counter:
    c = Counter()
    for toks in token_lists:
        c.update(t for t in toks if is_word(t))
    return c

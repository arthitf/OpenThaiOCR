# -*- coding: utf-8 -*-
"""ทดสอบเทียบ OpenThai2.0-OCR-Text (ที่โปรแกรมใช้อยู่ + ตัวหาตารางของเรา)
กับ OpenThai2.1-OCR-Markdown (iApp, 0.63B, รันด้วย transformers บน CPU)
- ข้อความทั้งหน้า (หน้าเดียวกับ akson_vs_openthai.py)
- ตาราง: ตารางสมมติ 3 แบบ x 4 สภาพภาพ วัดว่าแถวข้อมูลออกมาครบ/ถูกทุกช่องกี่แถว
- หน้าว่าง/หน้าที่มีแต่ภาพ: ดูว่าโมเดลแต่งข้อความขึ้นมาเองไหม
ทุกอย่างเป็นข้อมูลสมมติ ไม่มีข้อมูลจริง

python bench/markdown_vs_openthai.py --model model --md md_model --out bench_md
"""
from __future__ import annotations

import argparse
import html.parser
import json
import os
import random
import re
import sys
import time
import traceback
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import akson_vs_openthai as B  # noqa: E402

# ---------------------------------------------------------------- ตารางสมมติ
TABLES = [
    {"name": "เรียกเก็บ",
     "head": ["ลำดับ", "HN", "ชื่อ-สกุล", "วันที่รับบริการ", "รายการ", "จำนวนเงิน (บาท)"],
     "rows": [["1", "0012345", "นายสมชาย ใจดี", "01/10/2569", "ตรวจรักษาผู้ป่วยนอก", "250.00"],
              ["2", "0045871", "นางสาวมาลี ศรีสุข", "03/10/2569", "ทำแผลขนาดเล็ก", "180.00"],
              ["3", "0098765", "นายวิชัย พรหมมา", "05/10/2569", "ฉีดวัคซีนไข้หวัดใหญ่", "320.50"],
              ["4", "0102938", "นางบุญมี แก้วกล้า", "08/10/2569", "ตรวจน้ำตาลในเลือด", "60.00"],
              ["5", "0114477", "เด็กชายภูมิ ทองดี", "12/10/2569", "ทันตกรรม ขูดหินปูน", "400.00"],
              ["6", "0120001", "นายประเสริฐ บุญยืน", "15/10/2569", "กายภาพบำบัด", "200.00"],
              ["7", "0133690", "นางสาวกานดา สุขใจ", "20/10/2569", "นวดไทยเพื่อการรักษา", "250.00"],
              ["8", "0145522", "นายเอกชัย ศรีวงศ์", "28/10/2569", "ตรวจคัดกรองมะเร็ง", "1,000.00"]]},
    {"name": "ผลแล็บ",
     "head": ["รายการตรวจ", "ผล", "หน่วย", "ค่าปกติ"],
     "rows": [["Fasting blood sugar", "182", "mg/dL", "70-100"],
              ["HbA1c", "8.4", "%", "4.0-6.0"],
              ["Creatinine", "1.12", "mg/dL", "0.7-1.3"],
              ["eGFR", "68", "mL/min", "> 90"],
              ["Cholesterol", "245", "mg/dL", "< 200"],
              ["Triglyceride", "198", "mg/dL", "< 150"],
              ["LDL", "162", "mg/dL", "< 130"],
              ["Hemoglobin", "12.1", "g/dL", "12-16"]]},
    {"name": "สรุปตรวจสอบ",  # หัวตารางมีช่องรวม (ผลการตรวจสอบ คร่อม 2 คอลัมน์)
     "head": ["หน่วยบริการ", "จำนวนที่สุ่ม", "ผ่าน", "ไม่ผ่าน", "ร้อยละ"],
     "group": (2, 4, "ผลการตรวจสอบ"),
     "rows": [["รพ.สต.บ้านหนองแสง", "20", "18", "2", "90.00"],
              ["รพ.สต.โนนสะอาด", "15", "15", "0", "100.00"],
              ["คลินิกชุมชนอบอุ่น", "30", "24", "6", "80.00"],
              ["รพ.ชุมพลบุรี", "40", "37", "3", "92.50"],
              ["รพ.สต.หนองไผ่", "12", "10", "2", "83.33"],
              ["รวม", "117", "104", "13", "88.89"]]},
]


def render_table(t, fnt, W=1654, H=2339, margin=150):
    name, path = fnt
    px = 34 if name not in B.FONT_PX else int(B.FONT_PX[name] * 0.8)
    f = ImageFont.truetype(path, px)
    fb = f
    cols = len(t["head"])
    pad = 18
    widths = []
    for c in range(cols):
        cells = [t["head"][c]] + [r[c] for r in t["rows"]]
        widths.append(max(f.getlength(x) for x in cells) + 2 * pad)
    scale = min(1.0, (W - 2 * margin) / sum(widths))
    if scale < 1.0:
        px = int(px * scale)
        f = fb = ImageFont.truetype(path, px)
        widths = [w * scale for w in widths]
    asc, desc = f.getmetrics()
    rh = int((asc + desc) * 1.7)
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    title = f"ตารางที่ {random.randint(1, 9)} {t['name']} (ข้อมูลสมมติ)"
    d.text((margin, margin - rh), title, font=f, fill=20)
    x0, y = margin, margin + rh // 2
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)
    head_rows = 2 if t.get("group") else 1
    y_top = y
    # หัวตาราง
    if t.get("group"):
        a, b, label = t["group"]
        # แถวบน: ช่องรวม + ช่องคร่อม 2 แถว
        for c in range(cols):
            if a <= c < b:
                continue
            d.text((xs[c] + pad, y + rh // 2 + (rh - asc - desc) // 2), t["head"][c], font=fb, fill=20)
        lw = f.getlength(label)
        d.text(((xs[a] + xs[b]) / 2 - lw / 2, y + (rh - asc - desc) // 2), label, font=fb, fill=20)
        for c in range(a, b):
            d.text((xs[c] + pad, y + rh + (rh - asc - desc) // 2), t["head"][c], font=fb, fill=20)
        d.line([(xs[a], y + rh), (xs[b], y + rh)], fill=20, width=2)
    else:
        for c in range(cols):
            d.text((xs[c] + pad, y + (rh - asc - desc) // 2), t["head"][c], font=fb, fill=20)
    y += rh * head_rows
    d.line([(xs[0], y), (xs[-1], y)], fill=20, width=3)
    for r in t["rows"]:
        for c in range(cols):
            d.text((xs[c] + pad, y + (rh - asc - desc) // 2), r[c], font=f, fill=20)
        y += rh
        d.line([(xs[0], y), (xs[-1], y)], fill=20, width=2)
    d.line([(xs[0], y_top), (xs[-1], y_top)], fill=20, width=3)
    for i, x in enumerate(xs):
        if t.get("group") and t["group"][0] < i < t["group"][1]:
            d.line([(x, y_top + rh), (x, y)], fill=20, width=2)
        else:
            d.line([(x, y_top), (x, y)], fill=20, width=2)
    d.text((margin, y + rh // 2), "หมายเหตุ ข้อมูลในตารางเป็นข้อมูลสมมติเพื่อการทดสอบ", font=f, fill=20)
    return im


def blank_pages():
    W, H = 1654, 2339
    blank = Image.new("L", (W, H), 252)
    pic = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(pic)
    rng = np.random.default_rng(7)
    for _ in range(40):  # ภาพลายเส้น/วงกลม ไม่มีตัวอักษร
        x, y = rng.integers(100, W - 300), rng.integers(100, H - 300)
        s = int(rng.integers(40, 250))
        if rng.random() < 0.5:
            d.ellipse([x, y, x + s, y + s], outline=int(rng.integers(0, 150)), width=4)
        else:
            d.rectangle([x, y, x + s, y + s // 2], fill=int(rng.integers(120, 230)))
    return [("ว่างเปล่า", blank), ("มีแต่รูปทรง ไม่มีตัวอักษร", pic)]


# ---------------------------------------------------------------- อ่านตารางจาก HTML
class _TP(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_rows(text: str) -> list[list[str]]:
    p = _TP()
    p.feed(text)
    return [r for t in p.tables for r in t]


def md_to_plain(md: str) -> str:
    t = re.sub(r"</t[dh]>", " ", md)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[#*_`|$]", " ", t)
    return t


def table_score(gt_rows, pred_rows):
    key = lambda r: tuple(B.norm(c) for c in r)  # noqa: E731
    pred = [key(r) for r in pred_rows]
    hit_rows = 0
    pool = list(pred)
    for r in gt_rows:
        k = key(r)
        if k in pool:
            hit_rows += 1
            pool.remove(k)
    cells = [B.norm(c) for r in pred_rows for c in r]
    hit_cells = 0
    for r in gt_rows:
        for c in r:
            c = B.norm(c)
            if c in cells:
                hit_cells += 1
                cells.remove(c)
    n_cells = sum(len(r) for r in gt_rows)
    return hit_rows, len(gt_rows), hit_cells, n_cells


# ---------------------------------------------------------------- เครื่องยนต์
class OpenThaiTables(B.OpenThai):
    def tables(self, im):
        import thai_tables
        r = self.e.read(im.convert("RGB"))
        blocks = thai_tables.blocks_from_ocr(im.convert("RGB"), r.get("lines") or [])
        rows = [row for b in blocks if b.get("type") == "table" for row in b.get("rows") or []]
        return rows, len(blocks)


class MarkdownVLM:
    name = "OpenThai2.1-OCR-Markdown (0.63B, CPU)"

    def __init__(self, mdir):
        import torch
        from transformers import AutoModel, AutoProcessor, GenerationConfig
        sys.path.insert(0, str(Path(mdir).resolve()))
        torch.set_num_threads(os.cpu_count() or 4)
        self.torch = torch
        self.model = AutoModel.from_pretrained(mdir, trust_remote_code=True, dtype=torch.float32).eval()
        self.proc = AutoProcessor.from_pretrained(mdir, trust_remote_code=True)
        self.gc = GenerationConfig.from_pretrained(mdir, trust_remote_code=True)
        self.gc.max_new_tokens = int(os.environ.get("MD_MAX_TOKENS", "4096"))
        self.gc.do_sample = False
        self.gc.num_beams = 1
        self.gc.repetition_penalty = 1.1
        from nemotron_parse.postprocessing import postprocess_document
        self.post = postprocess_document
        self.prompt = "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"

    def raw(self, im):
        inputs = self.proc(images=[im.convert("RGB")], text=self.prompt, return_tensors="pt",
                           add_special_tokens=False)
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, generation_config=self.gc)
        return self.proc.batch_decode(out, skip_special_tokens=False)[0]

    def markdown(self, im):
        raw = self.raw(im)
        doc = self.post(raw, image_size=im.size)
        return doc.get("markdown", "") if isinstance(doc, dict) else str(doc)

    def page(self, im):
        return md_to_plain(self.markdown(im))

    def tables(self, im):
        md = self.markdown(im)
        return html_rows(md), md.count("<table")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="model")
    ap.add_argument("--md", default="md_model")
    ap.add_argument("--out", default="bench_md")
    ap.add_argument("--text-pages", type=int, default=1, help="จำนวนหน้าข้อความต่อสภาพภาพ")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "samples").mkdir(parents=True, exist_ok=True)
    rng = random.Random(2570)
    random.seed(2570)
    fl = B.fonts()
    corpus = [ln.strip() for ln in (HERE / "corpus_th.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]

    text_set = []
    for k in range(a.text_pages):
        chunk = corpus[k * 18:(k + 1) * 18]
        base = B.render_page(chunk, fl[k % len(fl)])
        for cond in B.CONDITIONS:
            text_set.append((cond, "\n".join(chunk), B.degrade(base, cond, rng)))
    table_set = []
    for i, t in enumerate(TABLES):
        base = render_table(t, fl[i % len(fl)])
        for cond in B.CONDITIONS:
            table_set.append((cond, t, B.degrade(base, cond, rng)))
    for cond in B.CONDITIONS:
        next(x for x in table_set if x[0] == cond)[2].save(out / "samples" / f"table_{cond}.jpg", quality=80)
    blanks = blank_pages()

    engines = []
    for key, mk in (("openthai", lambda: OpenThaiTables(a.model)), ("md21", lambda: MarkdownVLM(a.md))):
        t0 = time.time()
        try:
            engines.append((key, mk()))
            print(f"loaded {key} in {time.time() - t0:.1f}s", flush=True)
        except Exception as ex:  # noqa: BLE001
            print(f"LOAD FAIL {key}: {type(ex).__name__}: {ex}\n{traceback.format_exc()}", flush=True)

    res = {"engines": {}}
    for key, e in engines:
        r = {"name": e.name, "text": {}, "tables": {}, "blank": [], "samples": []}
        # ข้อความทั้งหน้า
        sc = {c: B.Score() for c in B.CONDITIONS}
        for cond, ref, img in text_set:
            t0 = time.perf_counter()
            try:
                hyp = e.page(img)
            except Exception as ex:  # noqa: BLE001
                hyp = ""
                print(f"ERR {key} text: {type(ex).__name__}: {ex}", flush=True)
            dt = time.perf_counter() - t0
            sc[cond].add(ref, hyp, dt, cond)
            print(f"{key} text {cond}: {sc[cond].row()}", flush=True)
        r["text"] = {c: sc[c].row() for c in B.CONDITIONS}
        # ตาราง
        for cond, t, img in table_set:
            t0 = time.perf_counter()
            try:
                rows, ntab = e.tables(img)
            except Exception as ex:  # noqa: BLE001
                rows, ntab = [], 0
                print(f"ERR {key} table: {type(ex).__name__}: {ex}\n{traceback.format_exc()}", flush=True)
            dt = time.perf_counter() - t0
            hr, nr, hc, nc = table_score(t["rows"], rows)
            r["tables"].setdefault(cond, []).append({"table": t["name"], "rows_ok": hr, "rows": nr,
                                                     "cells_ok": hc, "cells": nc, "n_tables": ntab,
                                                     "sec": round(dt, 2)})
            print(f"{key} table {t['name']} {cond}: rows {hr}/{nr} cells {hc}/{nc} tables={ntab} {dt:.1f}s",
                  flush=True)
            if cond == "scan":
                r["samples"].append({"table": t["name"], "pred_rows": rows[:12]})
        # หน้าว่าง / ไม่มีตัวอักษร
        for label, img in blanks:
            try:
                hyp = e.page(img)
            except Exception as ex:  # noqa: BLE001
                hyp = f"ERR {ex}"
            n = len("".join(hyp.split()))
            r["blank"].append({"page": label, "chars": n, "text": hyp[:200]})
            print(f"{key} blank {label}: {n} chars {hyp[:120]!r}", flush=True)
        res["engines"][key] = r

    (out / "results_md.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    md = report(res)
    (out / "RESULTS_MD.md").write_text(md, encoding="utf-8")
    print(md)
    for key, r in res["engines"].items():
        for s in r["samples"]:
            print(f"--- {key} {s['table']} (scan) rows:")
            for row in s["pred_rows"]:
                print("   ", " | ".join(row))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md + "\n")


def report(res):
    C = B.CONDITIONS
    L = ["## OpenThai2.0-OCR-Text (ที่ใช้อยู่) เทียบ OpenThai2.1-OCR-Markdown", "",
         "### ข้อความทั้งหน้า (CER ยิ่งต่ำยิ่งดี)", "",
         "| เครื่องมือ | " + " | ".join(B.COND_TH[c] for c in C) + " | วินาที/หน้า |", "|---" * (len(C) + 2) + "|"]
    for r in res["engines"].values():
        t = r["text"]
        secs = np.mean([t[c]["sec_each"] for c in C]) if t else 0
        L.append(f"| {r['name']} | " + " | ".join(f"{t[c]['CER%']}%" for c in C) + f" | {secs:.1f} |")
    L += ["", "### ตาราง: แถวข้อมูลที่ถูกทุกช่อง (ช่องที่ถูก)", "",
          "| เครื่องมือ | " + " | ".join(B.COND_TH[c] for c in C) + " | รวม | วินาที/หน้า |", "|---" * (len(C) + 3) + "|"]
    for r in res["engines"].values():
        cells, tr, tn, tc, tcn, secs = [], 0, 0, 0, 0, []
        for c in C:
            xs = r["tables"].get(c, [])
            ro, rn = sum(x["rows_ok"] for x in xs), sum(x["rows"] for x in xs)
            co, cn = sum(x["cells_ok"] for x in xs), sum(x["cells"] for x in xs)
            tr, tn, tc, tcn = tr + ro, tn + rn, tc + co, tcn + cn
            secs += [x["sec"] for x in xs]
            cells.append(f"{ro}/{rn} ({100 * co / max(1, cn):.0f}%)")
        L.append(f"| {r['name']} | " + " | ".join(cells) +
                 f" | **{tr}/{tn}** ({100 * tc / max(1, tcn):.0f}%) | {np.mean(secs) if secs else 0:.1f} |")
    L += ["", "### หน้าที่ไม่มีตัวอักษร (ควรได้ 0 ตัวอักษร)", ""]
    for r in res["engines"].values():
        L.append(f"- {r['name']}: " + ", ".join(f"{b['page']} = {b['chars']} ตัวอักษร" for b in r["blank"]))
    return "\n".join(L)


if __name__ == "__main__":
    main()

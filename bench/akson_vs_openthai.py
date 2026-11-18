# -*- coding: utf-8 -*-
"""ทดสอบเทียบ OCR ภาษาไทย: OpenThai2.0-OCR-Text (ที่โปรแกรมใช้อยู่) กับ AksonOCR-Lite
ใช้ภาพที่สร้างขึ้นเองจากข้อความสมมติใน corpus_th.txt (ไม่มีข้อมูลจริง) จึงรู้คำตอบที่ถูกทุกตัวอักษร

python bench/akson_vs_openthai.py --model model --out bench_out [--quick]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import random
import sys
import time
import traceback
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

FONT_CANDIDATES = [
    # Linux (fonts-thai-tlwg) — Laksaman = แบบเดียวกับ TH Sarabun New
    ("Laksaman", "/usr/share/fonts/opentype/tlwg/Laksaman.otf"),
    ("Garuda", "/usr/share/fonts/opentype/tlwg/Garuda.otf"),
    ("Loma", "/usr/share/fonts/opentype/tlwg/Loma.otf"),
    ("Norasi", "/usr/share/fonts/opentype/tlwg/Norasi.otf"),
    ("Kinnari", "/usr/share/fonts/opentype/tlwg/Kinnari.otf"),
    ("Waree", "/usr/share/fonts/opentype/tlwg/Waree.otf"),
    # Windows
    ("Tahoma", r"C:\Windows\Fonts\tahoma.ttf"),
    ("Leelawadee UI", r"C:\Windows\Fonts\LeelawUI.ttf"),
    ("Leelawadee", r"C:\Windows\Fonts\leelawad.ttf"),
    ("Cordia New", r"C:\Windows\Fonts\cordia.ttc"),
    ("Angsana New", r"C:\Windows\Fonts\angsa.ttc"),
    ("Browallia New", r"C:\Windows\Fonts\browa.ttc"),
]
# ขนาดตัวอักษร (px ที่ 200 dpi) — ฟอนต์ตระกูล Sarabun/Angsana ตัวเล็กกว่าจึงใช้ขนาดใหญ่ขึ้น
FONT_PX = {"Laksaman": 46, "Norasi": 40, "Kinnari": 40, "Cordia New": 50, "Angsana New": 50,
           "Browallia New": 50}
CONDITIONS = ["clean", "scan", "fax", "photo"]
COND_TH = {"clean": "ไฟล์ดิจิทัลคมชัด", "scan": "สแกน 200dpi มีจุดรบกวน", "fax": "แฟกซ์/สแกนขาวดำความละเอียดต่ำ",
           "photo": "ถ่ายด้วยมือถือ เอียง แสงไม่เท่า"}


def fonts():
    out = []
    for name, p in FONT_CANDIDATES:
        if Path(p).exists():
            out.append((name, p))
            continue
        if p.startswith("/usr/share/fonts"):  # Ubuntu แต่ละรุ่นเก็บไว้ต่างที่ (.otf / .ttf)
            stem = Path(p).stem
            hit = sorted(Path("/usr/share/fonts").rglob(f"{stem}.*"))
            hit = [h for h in hit if h.suffix.lower() in (".otf", ".ttf")]
            if hit:
                out.append((name, str(hit[0])))
    if not out:
        raise SystemExit("ไม่พบฟอนต์ภาษาไทย")
    return out


# ---------------------------------------------------------------- สร้างภาพ
def render_line(text, font_path, px, pad=14):
    f = ImageFont.truetype(font_path, px)
    l, t, r, b = f.getbbox(text)
    asc, desc = f.getmetrics()
    w, h = r - l + pad * 2, asc + desc + pad * 2
    im = Image.new("L", (w, h), 255)
    ImageDraw.Draw(im).text((pad - l, pad), text, font=f, fill=20)
    return im


def render_page(lines, fnt, W=1654, H=2339, margin=150):
    name, path = fnt
    px = FONT_PX.get(name, 38)
    f = ImageFont.truetype(path, px)
    while max(f.getlength(t) for t in lines) > W - 2 * margin and px > 20:
        px -= 2
        f = ImageFont.truetype(path, px)
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    asc, desc = f.getmetrics()
    y = margin
    for t in lines:
        d.text((margin, y), t, font=f, fill=20)
        y += int((asc + desc) * 1.45)
    return im


def degrade(im: Image.Image, cond: str, rng: random.Random) -> Image.Image:
    if cond == "clean":
        return im.convert("RGB")
    a = np.asarray(im).astype(np.float32)
    if cond == "scan":
        im2 = im.rotate(rng.uniform(-0.8, 0.8), resample=Image.BICUBIC, expand=True, fillcolor=255)
        im2 = im2.filter(ImageFilter.GaussianBlur(0.7))
        a = np.asarray(im2).astype(np.float32) * 0.93 + 10
        a += np.random.default_rng(rng.randint(0, 9999)).normal(0, 9, a.shape)
        return _jpeg(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGB"), 72)
    if cond == "fax":
        w, h = im.size
        small = im.resize((max(1, w // 2), max(1, h // 2)), Image.BILINEAR)
        small = small.point(lambda v: 0 if v < 150 else 255)
        out = small.resize((w, h), Image.NEAREST)
        a = np.asarray(out).copy()
        noise = np.random.default_rng(rng.randint(0, 9999)).random(a.shape)
        a[noise < 0.004] = 0
        return Image.fromarray(a).convert("RGB")
    if cond == "photo":
        w, h = im.size
        im2 = im.rotate(rng.uniform(-2.5, 2.5), resample=Image.BICUBIC, expand=True, fillcolor=255)
        w, h = im2.size
        dx, dy = w * 0.03, h * 0.03
        coeffs = _persp([(0, 0), (w, 0), (w, h), (0, h)],
                        [(rng.uniform(0, dx), rng.uniform(0, dy)), (w - rng.uniform(0, dx), rng.uniform(0, dy)),
                         (w - rng.uniform(0, dx), h - rng.uniform(0, dy)), (rng.uniform(0, dx), h - rng.uniform(0, dy))])
        im2 = im2.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=255)
        a = np.asarray(im2).astype(np.float32)
        gx = np.linspace(rng.uniform(0.62, 0.8), 1.0, w)[None, :]
        gy = np.linspace(1.0, rng.uniform(0.75, 0.9), h)[:, None]
        a = a * gx * gy
        a = np.stack([a * 1.0, a * 0.97, a * 0.9], axis=2)
        a += np.random.default_rng(rng.randint(0, 9999)).normal(0, 6, a.shape)
        out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0))
        return _jpeg(out, 45)
    raise ValueError(cond)


def _jpeg(im, q):
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q)
    return Image.open(io.BytesIO(b.getvalue())).convert("RGB")


def _persp(src, dst):
    A, B = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); B.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); B.append(v)
    return np.linalg.solve(np.array(A, float), np.array(B, float)).tolist()


# ---------------------------------------------------------------- วัดผล
def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").replace("\u0e4d\u0e32", "\u0e33")
    return "".join(s.split())


def lev(a: str, b: str) -> int:
    try:
        from rapidfuzz.distance import Levenshtein
        return Levenshtein.distance(a, b)
    except ImportError:
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1]


class Score:
    def __init__(self):
        self.err = self.chars = self.exact = self.n = 0
        self.t = 0.0
        self.worst = []

    def add(self, ref, hyp, dt, tag=""):
        r, h = norm(ref), norm(hyp)
        e = lev(r, h)
        self.err += e
        self.chars += max(1, len(r))
        self.exact += int(r == h)
        self.n += 1
        self.t += dt
        if e:
            self.worst.append((e / max(1, len(r)), tag, ref, hyp))

    def row(self):
        return {"CER%": round(100 * self.err / max(1, self.chars), 2),
                "exact%": round(100 * self.exact / max(1, self.n), 1),
                "n": self.n, "sec_each": round(self.t / max(1, self.n), 3)}


# ---------------------------------------------------------------- เครื่องยนต์ OCR
class OpenThai:
    name = "OpenThai2.0-OCR-Text (ที่ใช้อยู่)"

    def __init__(self, mdir):
        import ocr_core
        ocr_core.init_worker(str(mdir), threads=4, lang="th")
        self.e = ocr_core._OCR
        print("OpenThai LineOCR methods:", [m for m in dir(self.e) if not m.startswith("_")], flush=True)

    def page(self, im: Image.Image) -> str:
        r = self.e.read(im.convert("RGB"))
        lines = sorted(r.get("lines") or [], key=lambda ln: _top(ln.get("box")))
        return "\n".join(ln.get("text", "") for ln in lines) if lines else r.get("text", "")

    line = page


def _top(box):
    try:
        b = np.asarray(box, float).reshape(-1, 2)
        return float(b[:, 1].min())
    except Exception:  # noqa: BLE001
        return 0.0


class Akson:
    name = "AksonOCR-Lite (ทั้งระบบ: หาบรรทัด+อ่าน)"

    def __init__(self):
        import aksonocr_lite
        self.conv = aksonocr_lite.load(device="cpu", tables=False)

    @staticmethod
    def _bgr(im):
        return np.ascontiguousarray(np.asarray(im.convert("RGB"))[:, :, ::-1])

    def page(self, im):
        lines = self.conv.ocr(self._bgr(im))
        return "\n".join(ln.text for ln in lines)

    line = page


class AksonRec:
    name = "AksonOCR-Lite (ตัวอ่านข้อความอย่างเดียว)"

    def __init__(self):
        from aksonocr_lite.rec import Recognizer
        self.rec = Recognizer(device="cpu")

    def line(self, im):
        bgr = np.ascontiguousarray(np.asarray(im.convert("RGB"))[:, :, ::-1])
        return self.rec([bgr])[0][0]


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "model"))
    ap.add_argument("--out", default="bench_out")
    ap.add_argument("--quick", action="store_true", help="ชุดเล็ก (ใช้ทดสอบบน Windows)")
    ap.add_argument("--engines", default="openthai,akson,aksonrec")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "samples").mkdir(parents=True, exist_ok=True)
    corpus = [ln.strip() for ln in (HERE / "corpus_th.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    fl = fonts()
    print("fonts:", [f[0] for f in fl], flush=True)
    rng = random.Random(2569)
    if a.quick:
        corpus = corpus[:24]

    # ชุดทดสอบ
    line_set = []  # (cond, font, ref, img)
    for i, ref in enumerate(corpus):
        name, path = fl[i % len(fl)]
        base = render_line(ref, path, FONT_PX.get(name, 38))
        for cond in CONDITIONS:
            line_set.append((cond, name, ref, degrade(base, cond, rng)))
    page_set = []  # (cond, font, ref_text, img)
    per = 18
    for k in range(0, len(corpus), per):
        chunk = corpus[k:k + per]
        fnt = fl[(k // per) % len(fl)]
        base = render_page(chunk, fnt)
        for cond in CONDITIONS:
            img = degrade(base, cond, rng)
            page_set.append((cond, fnt[0], "\n".join(chunk), img))
    for cond in CONDITIONS:  # ตัวอย่างภาพ (ข้อความสมมติ)
        next(p for p in page_set if p[0] == cond)[3].save(out / "samples" / f"page_{cond}.jpg", quality=80)
        next(p for p in line_set if p[0] == cond)[3].save(out / "samples" / f"line_{cond}.png")

    engines = []
    for key in a.engines.split(","):
        t0 = time.time()
        try:
            e = {"openthai": lambda: OpenThai(a.model), "akson": Akson, "aksonrec": AksonRec}[key]()
            engines.append((key, e))
            print(f"loaded {key} in {time.time() - t0:.1f}s", flush=True)
        except Exception as ex:  # noqa: BLE001
            print(f"LOAD FAIL {key}: {type(ex).__name__}: {ex}\n{traceback.format_exc()}", flush=True)

    res = {"platform": platform.platform(), "python": sys.version.split()[0], "fonts": [f[0] for f in fl],
           "lines": len(line_set), "pages": len(page_set), "engines": {}}
    for key, e in engines:
        rr = {"lines": {}, "pages": {}, "errors": 0}
        # warm-up
        try:
            e.line(line_set[0][3])
        except Exception:  # noqa: BLE001
            pass
        for kind, data, fn in (("lines", line_set, getattr(e, "line", None)),
                               ("pages", page_set, getattr(e, "page", None))):
            if fn is None:
                continue
            sc = {c: Score() for c in CONDITIONS}
            allsc = Score()
            for cond, font, ref, img in data:
                t0 = time.perf_counter()
                try:
                    hyp = fn(img)
                except Exception as ex:  # noqa: BLE001
                    hyp = ""
                    rr["errors"] += 1
                    if rr["errors"] <= 3:
                        print(f"ERR {key} {kind}: {type(ex).__name__}: {ex}", flush=True)
                dt = time.perf_counter() - t0
                for s in (sc[cond], allsc):
                    s.add(ref, hyp, dt, f"{cond}/{font}")
            rr[kind] = {c: sc[c].row() for c in CONDITIONS}
            rr[kind]["ALL"] = allsc.row()
            if kind == "lines":
                rr["worst_lines"] = [{"cer": round(w[0], 3), "where": w[1], "ref": w[2], "hyp": w[3]}
                                     for w in sorted(allsc.worst, reverse=True)[:25]]
            print(f"{key} {kind}: {rr[kind]['ALL']}", flush=True)
        res["engines"][key] = {"name": e.name, **rr}

    (out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    md = report(res)
    (out / "RESULTS.md").write_text(md, encoding="utf-8")
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md + "\n")


def report(res):
    L = [f"## ผลทดสอบ OCR ภาษาไทย ({res['platform']}, Python {res['python']})",
         f"ชุดทดสอบ: {res['lines']} ภาพบรรทัด, {res['pages']} หน้า A4 · ฟอนต์: {', '.join(res['fonts'])}",
         "CER = อัตราตัวอักษรผิด (ยิ่งต่ำยิ่งดี, ไม่นับช่องว่าง) · exact = บรรทัด/หน้าที่ถูกทุกตัวอักษร", ""]
    for kind, title in (("lines", "ทีละบรรทัด"), ("pages", "ทั้งหน้า")):
        L += [f"### {title}", "", "| เครื่องมือ | " + " | ".join(COND_TH[c] for c in CONDITIONS) +
              " | รวม | exact | วินาที/" + ("บรรทัด" if kind == "lines" else "หน้า") + " |",
              "|---" * (len(CONDITIONS) + 4) + "|"]
        for key, r in res["engines"].items():
            if not r.get(kind):
                continue
            cells = [f"{r[kind][c]['CER%']}%" for c in CONDITIONS]
            al = r[kind]["ALL"]
            L.append(f"| {r['name']} | " + " | ".join(cells) + f" | **{al['CER%']}%** | {al['exact%']}% | {al['sec_each']} |")
        L.append("")
    for key, r in res["engines"].items():
        if r.get("errors"):
            L.append(f"- {r['name']}: ล้มเหลว {r['errors']} ครั้ง")
    return "\n".join(L)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""แท็บ "ตัดคำ": ตัดคำภาษาไทยจากข้อความที่พิมพ์/วาง/ผล OCR หรือจากไฟล์ .txt .docx .xlsx .csv
ใช้โมเดล OpenThai2.0-LATTE-WordSeg-Lite (iApp Technology) ทำงานออฟไลน์
"""
from __future__ import annotations

import csv
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import thai_wordseg as ws

SEPS = {"เส้นตั้ง  |": "|", "ช่องว่าง": " ", "ขึ้นบรรทัดใหม่": "\n", "ทับ  /": "/", "จุลภาค  ,": ","}
UNIT_LABELS = {v: k for k, v in ws.UNITS.items()}


class WordSegTab:
    def __init__(self, parent, app_name: str, text_font, get_ocr_text=None, has_dnd=False, dnd_files=None):
        self.app = app_name
        self.get_ocr_text = get_ocr_text
        self.q: queue.Queue = queue.Queue()
        self.table = None          # (path, sheet rows: list[list], header: list[str]) ของไฟล์ Excel/CSV
        self.last_tokens: list[list[str]] = []

        top = ttk.Frame(parent)
        top.pack(fill="x", padx=6, pady=(8, 2))
        ttk.Button(top, text="เปิดไฟล์...", command=self.pick_file).pack(side="left")
        if get_ocr_text:
            ttk.Button(top, text="ใช้ข้อความจากแท็บ \"ข้อความ\"", command=self.use_ocr).pack(side="left", padx=4)
        ttk.Button(top, text="ล้าง", command=self.clear).pack(side="left", padx=(0, 12))
        ttk.Label(top, text="หน่วยคำ:").pack(side="left")
        self.v_unit = tk.StringVar(value=ws.UNITS["short"])
        ttk.Combobox(top, textvariable=self.v_unit, values=list(ws.UNITS.values()), state="readonly",
                     width=16).pack(side="left", padx=(2, 10))
        ttk.Label(top, text="คั่นคำด้วย:").pack(side="left")
        self.v_sep = tk.StringVar(value="เส้นตั้ง  |")
        ttk.Combobox(top, textvariable=self.v_sep, values=list(SEPS), state="readonly",
                     width=13).pack(side="left", padx=(2, 10))
        self.btn_run = ttk.Button(top, text="▶  ตัดคำ", command=self.run)
        self.btn_run.pack(side="left")

        # แถบสำหรับไฟล์ Excel / CSV
        self.f_tab = ttk.Frame(parent)
        ttk.Label(self.f_tab, text="ไฟล์ตาราง:").pack(side="left")
        self.lbl_file = ttk.Label(self.f_tab, foreground="#335")
        self.lbl_file.pack(side="left", padx=(2, 10))
        ttk.Label(self.f_tab, text="คอลัมน์ที่จะตัดคำ:").pack(side="left")
        self.v_col = tk.StringVar()
        self.cb_col = ttk.Combobox(self.f_tab, textvariable=self.v_col, state="readonly", width=28)
        self.cb_col.pack(side="left", padx=(2, 8))
        self.btn_col = ttk.Button(self.f_tab, text="ตัดคำทั้งคอลัมน์ → บันทึก Excel...", command=self.run_column)
        self.btn_col.pack(side="left")

        self.lbl = ttk.Label(parent, foreground="#555",
                             text="พิมพ์/วางข้อความด้านซ้าย หรือเปิดไฟล์ .txt .docx .xlsx .csv แล้วกด \"ตัดคำ\"")
        self.lbl.pack(fill="x", padx=8, pady=(2, 2))

        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        left = ttk.LabelFrame(pw, text=" ข้อความต้นฉบับ ")
        right = ttk.LabelFrame(pw, text=" ผลตัดคำ ")
        pw.add(left, weight=1)
        pw.add(right, weight=1)
        self.src = self._text(left, text_font)
        self.out = self._text(right, text_font)

        bot = ttk.Frame(parent)
        bot.pack(fill="x", padx=6, pady=(0, 8))
        ttk.Button(bot, text="คัดลอกผล", command=self.copy_out).pack(side="left")
        ttk.Button(bot, text="บันทึกผลเป็น .txt...", command=self.save_txt).pack(side="left", padx=4)
        ttk.Button(bot, text="นับคำ → Excel...", command=self.save_counts).pack(side="left")
        ttk.Label(bot, foreground="#999",
                  text="ตัวตัดคำ: OpenThai2.0-LATTE-WordSeg-Lite, iApp Technology (CC BY-SA 4.0)"
                  ).pack(side="right")

        if has_dnd and dnd_files:
            try:
                self.src.drop_target_register(dnd_files)
                self.src.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:  # noqa: BLE001
                pass
        parent.after(150, self._poll)

    # ---------------------------------------------------------------- UI helpers
    @staticmethod
    def _text(parent, font):
        f = ttk.Frame(parent)
        f.pack(fill="both", expand=True, padx=4, pady=4)
        t = tk.Text(f, wrap="word", font=font, undo=True, padx=8, pady=6)
        sb = ttk.Scrollbar(f, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        return t

    def _unit(self) -> str:
        return UNIT_LABELS.get(self.v_unit.get(), "short")

    def _sep(self) -> str:
        return SEPS.get(self.v_sep.get(), "|")

    def _busy(self, on: bool, msg: str = ""):
        state = "disabled" if on else "normal"
        self.btn_run.configure(state=state)
        self.btn_col.configure(state=state)
        if msg:
            self.lbl.configure(text=msg)

    def _poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "text":
                    tokens, msg = data
                    self.last_tokens = tokens
                    sep = self._sep()
                    self.out.delete("1.0", "end")
                    self.out.insert("1.0", "\n".join(sep.join(t for t in line if not t.isspace())
                                                     for line in tokens))
                    self._busy(False, msg)
                elif kind == "msg":
                    self._busy(False, data)
                elif kind == "err":
                    self._busy(False, "ผิดพลาด: " + data)
                    messagebox.showerror(self.app, data)
        except queue.Empty:
            pass
        self.lbl.after(150, self._poll)

    def clear(self):
        self.src.delete("1.0", "end")
        self.out.delete("1.0", "end")
        self.last_tokens = []
        self.table = None
        self.f_tab.pack_forget()

    def use_ocr(self):
        text = self.get_ocr_text() if self.get_ocr_text else ""
        if not text.strip():
            messagebox.showinfo(self.app, "ยังไม่มีข้อความในแท็บ \"ข้อความ\" (แปลงไฟล์หรือวางภาพหน้าจอก่อน)")
            return
        self.src.delete("1.0", "end")
        self.src.insert("1.0", text)
        self.run()

    # ---------------------------------------------------------------- ตัดคำข้อความ
    def run(self):
        text = self.src.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo(self.app, "ยังไม่มีข้อความ")
            return
        unit = self._unit()
        self._busy(True, "กำลังตัดคำ... (ครั้งแรกโหลดโมเดลประมาณ 1 วินาที)")

        def work():
            try:
                lines = text.split("\n")
                toks = ws.tokenize_batch(lines, unit=unit)
                n = sum(ws.is_word(t) for line in toks for t in line)
                uniq = len(ws.word_counts(toks))
                self.q.put(("text", (toks, f"ตัดคำแล้ว: {n:,} คำ ({uniq:,} คำไม่ซ้ำ) · หน่วย: {self.v_unit.get()}")))
            except Exception as e:  # noqa: BLE001
                self.q.put(("err", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def copy_out(self):
        t = self.out.get("1.0", "end-1c")
        if t:
            self.out.clipboard_clear()
            self.out.clipboard_append(t)
            self.lbl.configure(text="คัดลอกผลแล้ว")

    def save_txt(self):
        t = self.out.get("1.0", "end-1c")
        if not t.strip():
            messagebox.showinfo(self.app, "ยังไม่มีผลตัดคำ")
            return
        p = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("ข้อความ", "*.txt")],
                                         initialfile="ตัดคำ.txt")
        if p:
            Path(p).write_text(t, encoding="utf-8-sig")
            self.lbl.configure(text=f"บันทึกแล้ว: {p}")

    def save_counts(self):
        if not self.last_tokens:
            messagebox.showinfo(self.app, "กด \"ตัดคำ\" ก่อน แล้วค่อยนับคำ")
            return
        p = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
                                         initialfile="นับคำ.xlsx")
        if not p:
            return
        try:
            from openpyxl import Workbook
            wb = Workbook()
            sh = wb.active
            sh.title = "ความถี่คำ"
            self._write_counts(sh, ws.word_counts(self.last_tokens))
            wb.save(p)
            self.lbl.configure(text=f"บันทึกแล้ว: {p}")
        except PermissionError:
            messagebox.showerror(self.app, "บันทึกไม่ได้ ไฟล์อาจเปิดค้างอยู่ใน Excel")

    @staticmethod
    def _write_counts(sh, counts):
        from openpyxl.styles import Font
        sh.append(["ลำดับ", "คำ", "จำนวนครั้ง"])
        for c in sh[1]:
            c.font = Font(bold=True)
        for i, (w, n) in enumerate(counts.most_common(), 1):
            sh.append([i, w, n])
        sh.column_dimensions["B"].width = 30
        sh.freeze_panes = "A2"

    # ---------------------------------------------------------------- ไฟล์
    def _on_drop(self, event):
        paths = self.src.tk.splitlist(event.data)
        if paths:
            self.open_path(Path(paths[0]))
        return "break"

    def pick_file(self):
        p = filedialog.askopenfilename(filetypes=[("ข้อความ / Word / Excel / CSV", "*.txt *.docx *.xlsx *.csv"),
                                                  ("ทุกไฟล์", "*.*")])
        if p:
            self.open_path(Path(p))

    def open_path(self, p: Path):
        ext = p.suffix.lower()
        try:
            if ext == ".txt":
                self._set_src(_read_text(p))
            elif ext == ".docx":
                from docx import Document
                d = Document(str(p))
                parts = [para.text for para in d.paragraphs]
                for t in d.tables:
                    for row in t.rows:
                        parts.append("\t".join(c.text for c in row.cells))
                self._set_src("\n".join(parts))
            elif ext in (".xlsx", ".csv"):
                rows = _read_table(p)
                if not rows:
                    messagebox.showinfo(self.app, "ไฟล์ว่าง")
                    return
                header = [str(h) if h not in (None, "") else f"คอลัมน์ {i + 1}" for i, h in enumerate(rows[0])]
                self.table = (p, rows, header)
                # เลือกคอลัมน์ที่มีภาษาไทยมากที่สุดให้ก่อน
                def thai(i):
                    return sum(any("ก" <= ch <= "๛" for ch in str(r[i] if i < len(r) else ""))
                               for r in rows[1:200])
                best = max(range(len(header)), key=thai)
                self.cb_col.configure(values=header)
                self.v_col.set(header[best])
                self.lbl_file.configure(text=p.name)
                self.f_tab.pack(fill="x", padx=6, pady=(2, 2), before=self.lbl)
                sample = [str(r[best]) for r in rows[1:51] if best < len(r) and r[best] not in (None, "")]
                self._set_src("\n".join(sample))
                self.lbl.configure(text=f"{p.name}: {len(rows) - 1:,} แถว — แสดงตัวอย่าง 50 แถวแรกของคอลัมน์ "
                                        f"\"{header[best]}\" · กด \"ตัดคำทั้งคอลัมน์\" เพื่อทำทั้งไฟล์")
                return
            else:
                messagebox.showinfo(self.app, "รองรับไฟล์ .txt .docx .xlsx .csv")
                return
            self.table = None
            self.f_tab.pack_forget()
            self.lbl.configure(text=f"เปิด {p.name} แล้ว กด \"ตัดคำ\"")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(self.app, f"เปิดไฟล์ไม่ได้: {type(e).__name__}: {e}")

    def _set_src(self, text: str):
        self.src.delete("1.0", "end")
        self.src.insert("1.0", text)
        self.out.delete("1.0", "end")
        self.last_tokens = []

    def run_column(self):
        if not self.table:
            return
        path, rows, header = self.table
        try:
            ci = header.index(self.v_col.get())
        except ValueError:
            return
        out = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
                                           initialdir=str(path.parent), initialfile=f"{path.stem}_ตัดคำ.xlsx")
        if not out:
            return
        unit, sep, colname = self._unit(), self._sep(), header[ci]
        if sep == "\n":
            sep = " | "
        self._busy(True, f"กำลังตัดคำ {len(rows) - 1:,} แถว...")

        def work():
            try:
                from openpyxl import Workbook
                from openpyxl.styles import Alignment, Font
                vals = [str(r[ci]) if ci < len(r) and r[ci] not in (None, "") else "" for r in rows[1:]]
                toks = ws.tokenize_batch(vals, unit=unit)
                wb = Workbook()
                sh = wb.active
                sh.title = "ข้อมูล"
                head = list(header[:ci + 1]) + [f"{colname} (ตัดคำ)"] + list(header[ci + 1:])
                sh.append(head)
                for c in sh[1]:
                    c.font = Font(bold=True)
                for r, t in zip(rows[1:], toks):
                    r = list(r) + [None] * (len(header) - len(r))
                    joined = sep.join(x for x in t if not x.isspace())
                    sh.append(r[:ci + 1] + [joined] + r[ci + 1:])
                col = sh.cell(row=1, column=ci + 2).column_letter
                sh.column_dimensions[col].width = 60
                for cell in sh[col][1:]:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
                sh.freeze_panes = "A2"
                self._write_counts(wb.create_sheet("ความถี่คำ"), ws.word_counts(toks))
                wb.save(out)
                self.q.put(("msg", f"บันทึกแล้ว: {out}  (เพิ่มคอลัมน์ \"{colname} (ตัดคำ)\" และชีต \"ความถี่คำ\")"))
            except PermissionError:
                self.q.put(("err", "บันทึกไม่ได้ ไฟล์อาจเปิดค้างอยู่ใน Excel"))
            except Exception as e:  # noqa: BLE001
                self.q.put(("err", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()


def _read_text(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp874", "tis-620"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_table(p: Path) -> list[list]:
    if p.suffix.lower() == ".csv":
        text = _read_text(p)
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [row for row in csv.reader(text.splitlines(), dialect)]
    from openpyxl import load_workbook
    wb = load_workbook(str(p), read_only=True, data_only=True)
    sh = wb.worksheets[0]
    rows = [list(r) for r in sh.iter_rows(values_only=True)]
    wb.close()
    while rows and all(v in (None, "") for v in rows[-1]):
        rows.pop()
    return rows

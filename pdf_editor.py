# -*- coding: utf-8 -*-
"""
หน้าจอ "แก้ไข PDF" และ "เครื่องมือ PDF"
"""
from __future__ import annotations

import base64
import copy
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

import pymupdf

import pdf_ops

PAD = 16
TOOLS = [("hand", "✋ มือ"), ("grab", "ลากดึงข้อความ"), ("select", "เลือก / ย้าย"), ("text", "พิมพ์ข้อความ"), ("edit", "แก้ข้อความเดิม"), ("white", "กล่องขาวปิดทับ"),
         ("highlight", "ไฮไลต์"), ("black", "คาดดำ")]
COLORS = [("ดำ", (0, 0, 0)), ("แดง", (0.8, 0, 0)), ("น้ำเงิน", (0, 0.2, 0.8))]


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in rgb)


class EditorTab:
    def __init__(self, parent, ocr_image, has_dnd=False, dnd_files=None, mod="Control", mod_txt="Ctrl"):
        self.parent = parent
        self.ocr_image = ocr_image
        self.doc: pymupdf.Document | None = None
        self.path: Path | None = None
        self.page = 0
        self.zoom = 1.0
        self.fit = True
        self.items: list[dict] = []
        self.undo_stack: list[tuple] = []
        self.dirty = False
        self.sel: int | None = None
        self.drag = None
        self.busy = False
        self.img = None
        self.q: queue.Queue = queue.Queue()
        self.fonts = pdf_ops.available_fonts()

        # ---------- แถบบน: ไฟล์ / เครื่องมือ PDF / หน้า / ซูม
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=6, pady=(8, 2))
        ttk.Button(top, text="เปิดไฟล์...", command=self.pick_file).pack(side="left")
        self.btn_save = ttk.Button(top, text="บันทึกเป็น...", command=self.save, state="disabled")
        self.btn_save.pack(side="left", padx=(4, 6))
        mb = ttk.Menubutton(top, text="เครื่องมือ PDF ▾")
        menu = tk.Menu(mb, tearoff=0, font=("Tahoma", 10))
        menu.add_command(label="แทรกไฟล์ต่อท้าย (รวมไฟล์)...", command=self.tool_insert)
        menu.add_command(label="เก็บเฉพาะบางหน้า...", command=self.tool_keep_pages)
        menu.add_command(label="แยกทุกหน้าเป็นไฟล์ละหน้า...", command=self.tool_split)
        menu.add_separator()
        menu.add_command(label="ใส่ลายน้ำ...", command=self.tool_watermark)
        menu.add_command(label="ใส่เลขหน้า / หัว-ท้ายกระดาษ...", command=self.tool_pagenum)
        menu.add_separator()
        menu.add_command(label="บีบอัดขนาดไฟล์", command=self.tool_compress)
        menu.add_command(label="หาข้อมูลส่วนบุคคลอัตโนมัติ...", command=self.auto_redact)
        mb["menu"] = menu
        self.mb_tools = mb
        mb.pack(side="left", padx=(0, 12))
        ttk.Button(top, text="◀", width=3, command=lambda: self.goto(self.page - 1)).pack(side="left")
        self.lbl_page = ttk.Label(top, text="หน้า - / -", width=12, anchor="center")
        self.lbl_page.pack(side="left")
        ttk.Button(top, text="▶", width=3, command=lambda: self.goto(self.page + 1)).pack(side="left")
        ttk.Button(top, text="−", width=3, command=lambda: self.set_zoom(self.zoom / 1.25)).pack(side="left", padx=(12, 0))
        ttk.Button(top, text="พอดีจอ", command=self.zoom_fit).pack(side="left")
        ttk.Button(top, text="+", width=3, command=lambda: self.set_zoom(self.zoom * 1.25)).pack(side="left")
        ttk.Button(top, text="ลบหน้านี้", command=self.delete_page).pack(side="right")
        ttk.Button(top, text="หมุนหน้า ⟳", command=self.rotate_page).pack(side="right", padx=4)

        # ---------- แถบเครื่องมือวาด
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=6, pady=2)
        self.tool = tk.StringVar(value="select")
        for key, label in TOOLS:
            ttk.Radiobutton(bar, text=label, value=key, variable=self.tool,
                            command=self.on_tool).pack(side="left", padx=(0, 8))

        # ---------- แถบรูปแบบตัวอักษร / ย้อนกลับ
        bar2 = ttk.Frame(parent)
        bar2.pack(fill="x", padx=6, pady=2)
        ttk.Label(bar2, text="ฟอนต์:").pack(side="left")
        self.v_font = tk.StringVar(value=self.fonts[0][0] if self.fonts else "")
        self.cb_font = ttk.Combobox(bar2, textvariable=self.v_font, values=[f[0] for f in self.fonts], width=24,
                                    state="readonly", height=25)
        self.cb_font.pack(side="left", padx=(2, 0))
        threading.Thread(target=self._load_fonts, daemon=True).start()
        self.v_size = tk.IntVar(value=16)
        self.v_size.trace_add("write", self.on_style_change)
        self.v_font.trace_add("write", self.on_style_change)
        ttk.Spinbox(bar2, from_=6, to=72, textvariable=self.v_size, width=4).pack(side="left", padx=4)
        self.color = (0, 0, 0)
        self.sw = tk.Label(bar2, width=2, bg="#000000", relief="solid", bd=1, cursor="hand2")
        self.sw.pack(side="left", padx=(4, 2))
        self.sw.bind("<Button-1>", lambda e: self.pick_color())
        for name, rgb in COLORS:
            b = tk.Label(bar2, text=name, fg=_hex(rgb), cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, c=rgb: self.set_color(c))
        ttk.Separator(bar2, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar2, text=f"ย้อนกลับ ({mod_txt}+Z)", command=self.undo).pack(side="left")
        ttk.Button(bar2, text="ลบที่เลือก (Delete)", command=self.delete_selected).pack(side="left", padx=6)
        self.btn_pii = ttk.Button(bar2, text="หาข้อมูลส่วนบุคคล...", command=self.auto_redact, state="disabled")
        self.btn_pii.pack(side="left")

        self.lbl = ttk.Label(parent, foreground="#555",
                             text="เปิดไฟล์ PDF หรือรูปภาพเพื่อเริ่มแก้ไข (ไฟล์ต้นฉบับจะไม่ถูกแก้)")
        self.lbl.pack(fill="x", padx=8, pady=(2, 4))

        # ---------- พื้นที่แสดงหน้า
        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.cv = tk.Canvas(wrap, bg="#8a8f98", highlightthickness=0)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.cv.yview)
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.cv.xview)
        self.cv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.cv.pack(side="left", fill="both", expand=True)
        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_motion)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.cv.bind("<Double-Button-1>", self.on_double)
        self.cv.bind("<Configure>", lambda e: self.fit and self.doc and self.zoom_fit())
        self.cv.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units"))
        self.cv.bind("<Button-4>", lambda e: self.cv.yview_scroll(-3, "units"))
        self.cv.bind("<Button-5>", lambda e: self.cv.yview_scroll(3, "units"))
        self.cv.create_text(40, 40, anchor="nw", fill="white", font=("Tahoma", 12), tags="hint",
                            text="กด \"เปิดไฟล์...\" หรือลากไฟล์ PDF / รูปภาพมาวางที่นี่")
        if has_dnd and dnd_files:
            try:
                self.cv.drop_target_register(dnd_files)
                self.cv.dnd_bind("<<Drop>>", lambda e: self.open_path(self.cv.tk.splitlist(e.data)[0]))
            except Exception:  # noqa: BLE001
                pass
        self.parent.after(150, self.poll)

    # ================================================================ ฟอนต์
    def _load_fonts(self):
        try:
            allf = pdf_ops.system_fonts()
        except Exception:  # noqa: BLE001
            return
        if allf:
            self.q.put(("fonts", allf))

    def font_info(self, name):
        """(ชื่อแสดง, ชื่อ Tk, path) จากชื่อที่เลือก รองรับชื่อแบบเก่า/ไม่มีคำนำหน้า"""
        for f in self.fonts:
            if f[0] == name:
                return f
        bare = (name or "").replace("ไทย · ", "")
        for f in self.fonts:
            if f[1] == bare:
                return f
        return self.fonts[0] if self.fonts else None

    # ================================================================ ไฟล์
    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        return messagebox.askyesno("แก้ไข PDF", "มีการแก้ไขที่ยังไม่ได้บันทึก ต้องการทิ้งการแก้ไขหรือไม่?")

    def pick_file(self):
        p = filedialog.askopenfilename(
            title="เปิดไฟล์เพื่อแก้ไข",
            filetypes=[("PDF และรูปภาพ", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"), ("ทุกไฟล์", "*.*")])
        if p:
            self.open_path(p)

    def open_path(self, p):
        if self.busy:
            return
        if not self.confirm_discard():
            return
        try:
            doc = pdf_ops.open_as_pdf(p)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("แก้ไข PDF", f"เปิดไฟล์ไม่ได้\n{e}")
            return
        if self.doc:
            self.doc.close()
        self.doc, self.path = doc, Path(p)
        self.page, self.items, self.undo_stack, self.sel, self.dirty = 0, [], [], None, False
        self.btn_save.configure(state="normal")
        self.btn_pii.configure(state="normal")
        self.cv.delete("hint")
        self.fit = True
        self.zoom_fit()
        self.status(f"เปิด {self.path.name} ({self.doc.page_count} หน้า) — เลือกเครื่องมือด้านบนแล้วคลิก/ลากบนหน้า")

    def save(self):
        if not self.doc:
            return
        default = self.path.with_name(f"{self.path.stem}_แก้ไข.pdf")
        p = filedialog.asksaveasfilename(title="บันทึกเป็นไฟล์ใหม่", defaultextension=".pdf",
                                         initialdir=str(default.parent), initialfile=default.name,
                                         filetypes=[("PDF", "*.pdf")])
        if not p:
            return
        if Path(p).resolve() == self.path.resolve():
            messagebox.showwarning("แก้ไข PDF", "กรุณาบันทึกเป็นชื่อใหม่ ไม่ทับไฟล์ต้นฉบับ")
            return
        items = []
        for it in self.items:
            it = dict(it)
            if it["type"] == "text":
                fi = self.font_info(it.get("fontname"))
                it["font"] = fi[2] if fi else None
            items.append(it)
        try:
            pdf_ops.save_with_items(self.doc, items, p)
        except PermissionError:
            messagebox.showerror("แก้ไข PDF", "บันทึกไม่ได้ ไฟล์อาจเปิดค้างอยู่ในโปรแกรมอื่น")
            return
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("แก้ไข PDF", f"บันทึกไม่สำเร็จ\n{type(e).__name__}: {e}")
            return
        self.dirty = False
        n_black = sum(i["type"] == "black" for i in self.items)
        extra = f"\nส่วนที่คาดดำ {n_black} จุดถูกลบข้อมูลออกจริงในไฟล์ใหม่" if n_black else ""
        if messagebox.askyesno("แก้ไข PDF", f"บันทึกแล้ว: {Path(p).name}{extra}\n\nเปิดไฟล์เลยไหม?"):
            pdf_ops.open_file(p)
        self.status(f"✓ บันทึกแล้ว {p}")

    # ================================================================ หน้า / ซูม
    def status(self, s):
        self.lbl.config(text=s)

    def goto(self, p):
        if not self.doc or not (0 <= p < self.doc.page_count):
            return
        self.page, self.sel = p, None
        if self.fit:
            self.zoom_fit()
        else:
            self.render()

    def zoom_fit(self):
        if not self.doc:
            return
        self.fit = True
        w = max(200, self.cv.winfo_width() - 2 * PAD)
        self.zoom = max(0.2, w / self.doc[self.page].rect.width)
        self.render()

    def set_zoom(self, z):
        if not self.doc:
            return
        self.fit = False
        self.zoom = min(5.0, max(0.2, z))
        self.render()

    def render(self):
        page = self.doc[self.page]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(self.zoom, self.zoom), alpha=False)
        self.img = tk.PhotoImage(data=base64.b64encode(pix.tobytes("png")).decode())
        self.cv.delete("all")
        self.cv.create_rectangle(PAD + 3, PAD + 3, PAD + pix.width + 3, PAD + pix.height + 3,
                                 fill="#5d6169", outline="")
        self.cv.create_image(PAD, PAD, anchor="nw", image=self.img, tags="page")
        self.cv.configure(scrollregion=(0, 0, pix.width + 2 * PAD, pix.height + 2 * PAD))
        self.lbl_page.config(text=f"หน้า {self.page + 1} / {self.doc.page_count}")
        self.draw_items()

    # พิกัด pt <-> canvas
    def to_cv(self, x, y):
        o = self.doc[self.page].rect
        return (x - o.x0) * self.zoom + PAD, (y - o.y0) * self.zoom + PAD

    def to_pt(self, cx, cy):
        o = self.doc[self.page].rect
        return (cx - PAD) / self.zoom + o.x0, (cy - PAD) / self.zoom + o.y0

    def tk_font(self, it):
        fi = self.font_info(it.get("fontname"))
        return (fi[1] if fi else "Tahoma", -max(4, int(round(it["size"] * self.zoom))))

    def draw_items(self):
        self.cv.delete("item")
        for idx, it in enumerate(self.items):
            if it["page"] != self.page:
                continue
            tag = ("item", f"it{idx}")
            x0, y0 = self.to_cv(*it["rect"][:2])
            x1, y1 = self.to_cv(*it["rect"][2:])
            t = it["type"]
            if t in ("white", "erase"):
                self.cv.create_rectangle(x0, y0, x1, y1, fill="white", outline="#c8c8c8", dash=(2, 2), tags=tag)
            elif t == "highlight":
                self.cv.create_rectangle(x0, y0, x1, y1, fill="#ffe24d", stipple="gray50", outline="", tags=tag)
            elif t == "black":
                if it.get("auto"):
                    self.cv.create_rectangle(x0, y0, x1, y1, fill="black", stipple="gray50",
                                             outline="#e02020", width=2, tags=tag)
                else:
                    self.cv.create_rectangle(x0, y0, x1, y1, fill="black", outline="", tags=tag)
            elif t == "text":
                tid = self.cv.create_text(x0, y0, anchor="nw", text=it["text"], fill=_hex(it["color"]),
                                          font=self.tk_font(it), tags=tag)
                bx = self.cv.bbox(tid)
                if bx:  # จำขนาดจริงไว้ใช้คลิกเลือก
                    p0 = self.to_pt(bx[0], bx[1])
                    p1 = self.to_pt(bx[2], bx[3])
                    it["rect"] = [it["rect"][0], it["rect"][1], p1[0], p1[1]] if p0 else it["rect"]
            if idx == self.sel:
                bx = self.cv.bbox(f"it{idx}")
                if bx:
                    self.cv.create_rectangle(bx[0] - 3, bx[1] - 3, bx[2] + 3, bx[3] + 3, outline="#1a73e8",
                                             dash=(4, 2), width=2, tags=("item", f"it{idx}", "selbox"))
                    if t != "text":  # จุดจับมุมขวาล่าง ลากเพื่อปรับขนาด
                        self.cv.create_rectangle(x1 - 5, y1 - 5, x1 + 5, y1 + 5, fill="#1a73e8",
                                                 outline="white", tags=("item", f"it{idx}", "handle"))

    # ================================================================ แก้ไข
    def push_undo(self, with_doc=False):
        self.undo_stack.append((copy.deepcopy(self.items), self.doc.tobytes() if with_doc else None, self.page))
        del self.undo_stack[:-30]
        self.dirty = True

    def undo(self):
        if not self.undo_stack or self.busy:
            return
        items, docbytes, page = self.undo_stack.pop()
        self.items = items
        if docbytes is not None:
            self.doc.close()
            self.doc = pymupdf.open("pdf", docbytes)
        self.page, self.sel = min(page, self.doc.page_count - 1), None
        self.render()
        self.status("ย้อนกลับแล้ว")

    def delete_selected(self):
        if self.sel is None:
            return
        self.push_undo()
        self.items.pop(self.sel)
        self.sel = None
        self.draw_items()

    def on_tool(self):
        self.sel = None
        if self.doc:
            self.draw_items()
        cursors = {"hand": "fleur", "grab": "crosshair", "text": "xterm", "edit": "xterm"}
        self.cv.configure(cursor=cursors.get(self.tool.get(), ""))
        tips = {"hand": "ลากเพื่อเลื่อนดูหน้า (หรือใช้ล้อเมาส์)",
                "grab": "ลากคลุมส่วนที่ต้องการ แล้วจะได้ข้อความพร้อมคัดลอกให้ทันที (ตารางวางใน Excel ได้)",
                "select": "คลิกเลือก · ลากย้าย · ลากมุมขวาล่างปรับขนาด · ดับเบิลคลิกแก้ข้อความ · Delete ลบ",
                "text": "คลิกที่ว่างเพื่อพิมพ์ใหม่ · คลิกข้อความที่วางไว้เพื่อลากย้าย · ดับเบิลคลิกเพื่อแก้ · ลูกศรขยับทีละนิด",
                "edit": "คลิกบรรทัดข้อความเดิมในเอกสารเพื่อแก้ (ภาพสแกนจะใช้ OCR อ่านให้ก่อน)",
                "white": "ลากเป็นกรอบเพื่อปิดทับด้วยสีขาว (ใช้ลบรอยหรือข้อความเดิมก่อนพิมพ์ทับ)",
                "highlight": "ลากเป็นกรอบเพื่อไฮไลต์",
                "black": "ลากเป็นกรอบเพื่อคาดดำ ข้อมูลใต้กล่องจะถูกลบจริงตอนบันทึก"}
        self.status(tips[self.tool.get()])

    def hit(self, cx, cy):
        x, y = self.to_pt(cx, cy)
        for idx in range(len(self.items) - 1, -1, -1):
            it = self.items[idx]
            if it["page"] != self.page:
                continue
            x0, y0, x1, y1 = it["rect"]
            if x0 - 3 <= x <= x1 + 3 and y0 - 3 <= y <= y1 + 3:
                return idx
        return None

    def on_press(self, e):
        if not self.doc or self.busy:
            return
        self.cv.focus_set()
        cx, cy = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        tool = self.tool.get()
        # จุดจับปรับขนาดของกล่องที่เลือกอยู่ (ใช้ได้ทุกเครื่องมือ)
        if self.sel is not None and self.sel < len(self.items):
            it = self.items[self.sel]
            if it["page"] == self.page and it["type"] != "text":
                hx, hy = self.to_cv(*it["rect"][2:])
                if abs(cx - hx) <= 8 and abs(cy - hy) <= 8:
                    self.push_undo()
                    self.drag = ("resize", cx, cy, cx, cy)
                    return
        if tool == "hand":
            self.cv.scan_mark(e.x, e.y)
            self.drag = ("pan", e.x, e.y, e.x, e.y)
            return
        if tool == "grab":
            self.drag = ("grab", cx, cy, cx, cy)
            self.cv.create_rectangle(cx, cy, cx, cy, outline="#0f9d58", dash=(4, 2), width=2, tags="rubber")
            return
        idx = self.hit(cx, cy)
        # คลิกโดนสิ่งที่วางไว้: เลือกและลากย้ายได้ทันที
        if idx is not None and (tool == "select" or idx == self.sel
                                or (tool in ("text", "edit") and self.items[idx]["type"] == "text")):
            self.sel = idx
            self.sync_toolbar(idx)
            self.draw_items()
            self.drag = ("move", cx, cy, cx, cy)
            return
        self.sel = None
        if tool == "select":
            self.draw_items()
        elif tool == "text":
            self.draw_items()
            self.add_text(*self.to_pt(cx, cy))
        elif tool == "edit":
            self.draw_items()
            self.edit_existing(*self.to_pt(cx, cy))
        else:
            self.draw_items()
            self.drag = ("box", cx, cy, cx, cy)
            self.cv.create_rectangle(cx, cy, cx, cy, outline="#1a73e8", dash=(4, 2), width=2, tags="rubber")

    # ---------- ซิงก์แถบเครื่องมือกับข้อความที่เลือก
    def sync_toolbar(self, idx):
        it = self.items[idx]
        if it["type"] != "text":
            return
        self._syncing = True
        try:
            fi = self.font_info(it.get("fontname"))
            if fi:
                self.v_font.set(fi[0])
            self.v_size.set(int(it.get("size", 16)))
            self.color = tuple(it.get("color", (0, 0, 0)))
            self.sw.configure(bg=_hex(self.color))
        finally:
            self._syncing = False

    def on_style_change(self, *_):
        if getattr(self, "_syncing", False) or self.sel is None or self.sel >= len(self.items):
            return
        it = self.items[self.sel]
        if it["type"] != "text":
            return
        try:
            size = max(6, min(72, int(self.v_size.get())))
        except (tk.TclError, ValueError):
            return
        if it.get("fontname") == self.v_font.get() and it.get("size") == size:
            return
        self.push_undo()
        it["fontname"], it["size"] = self.v_font.get(), size
        self.draw_items()

    def nudge(self, dx, dy):
        if self.sel is None or self.sel >= len(self.items):
            return
        self.push_undo()
        x0, y0, x1, y1 = self.items[self.sel]["rect"]
        self.items[self.sel]["rect"] = [x0 + dx, y0 + dy, x1 + dx, y1 + dy]
        self.draw_items()

    # ---------- ลากดึงข้อความ
    def grab_text(self, rect):
        page = self.doc[self.page]
        unrot = pymupdf.Rect(rect * page.derotation_matrix).normalize() if page.rotation else rect
        existing = page.get_text("text", clip=unrot, sort=True).strip()
        if len(existing.replace(" ", "").replace("\n", "")) >= 2:
            self.show_grabbed(existing, "ข้อความเดิมใน PDF")
            return
        # ภาพสแกน: ตัดเฉพาะส่วนที่ลาก แล้ว OCR (หาตารางให้ด้วย)
        from PIL import Image
        dpi = 300
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        k, o = dpi / 72.0, page.rect
        box = (max(0, int((rect.x0 - o.x0) * k)), max(0, int((rect.y0 - o.y0) * k)),
               min(img.width, int((rect.x1 - o.x0) * k) + 1), min(img.height, int((rect.y1 - o.y0) * k) + 1))
        crop = img.crop(box)
        pad = Image.new("RGB", (crop.width + 40, crop.height + 40), "white")
        pad.paste(crop, (20, 20))
        self.busy = True
        self.status("กำลังอ่านข้อความในกรอบ...")

        def work():
            try:
                res = self.ocr_image(pad)
                try:
                    import thai_tables as tables
                    text = tables.blocks_to_text(tables.blocks_from_ocr(pad, res.get("lines", [])))
                except Exception:  # noqa: BLE001
                    text = res.get("text", "")
                self.q.put(("grab", text or res.get("text", "")))
            except Exception as e:  # noqa: BLE001
                self.q.put(("grab_err", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def show_grabbed(self, text, source="OCR"):
        text = text.strip()
        if not text:
            self.status("ไม่พบตัวหนังสือในกรอบที่ลาก")
            return
        root = self.parent.winfo_toplevel()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        top = tk.Toplevel(self.parent)
        top.title("ข้อความที่ดึงได้")
        top.transient(root)
        ttk.Label(top, foreground="#0b7a3e",
                  text=f"✓ คัดลอกให้แล้ว ({source}) — วางที่อื่นได้เลย  ·  ถ้าเป็นตาราง วางใน Excel จะแยกช่องให้เอง"
                  ).pack(anchor="w", padx=10, pady=(10, 4))
        t = tk.Text(top, width=70, height=14, wrap="word", font=("Tahoma", 11), padx=6, pady=4)
        t.pack(fill="both", expand=True, padx=10)
        t.insert("1.0", text)

        def copy_again():
            root.clipboard_clear()
            root.clipboard_append(t.get("1.0", "end").strip())
            root.update()

        r = ttk.Frame(top)
        r.pack(fill="x", padx=10, pady=10)
        ttk.Button(r, text="ปิด", command=top.destroy).pack(side="right")
        ttk.Button(r, text="คัดลอกอีกครั้ง", command=copy_again).pack(side="right", padx=6)
        top.bind("<Escape>", lambda e: top.destroy())
        self.status("✓ ดึงข้อความแล้วและคัดลอกให้แล้ว")

    # ---------- แก้ข้อความเดิมในเอกสาร
    def page_lines(self):
        """บรรทัดข้อความเดิมของหน้า [(rect pt ที่มองเห็น, text, size, color)] จำไว้ต่อหน้า"""
        key = (self.page, self.doc[self.page].rotation, id(self.doc))
        cache = getattr(self, "_line_cache", {})
        if key in cache:
            return cache[key]
        page = self.doc[self.page]
        out = []
        d = page.get_text("dict")
        n_chars = sum(len(s["text"]) for b in d.get("blocks", []) for ln in b.get("lines", []) for s in ln["spans"])
        if n_chars >= 30:
            for b in d.get("blocks", []):
                for ln in b.get("lines", []):
                    t = "".join(s["text"] for s in ln["spans"]).strip()
                    if not t:
                        continue
                    r = pymupdf.Rect(ln["bbox"])
                    if page.rotation:
                        r = pymupdf.Rect(r * page.rotation_matrix).normalize()
                    sp = ln["spans"][0]
                    c = sp.get("color", 0)
                    out.append((r, t, round(sp.get("size", 14)), (((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255)))
        else:
            from PIL import Image
            self.status("กำลังอ่านข้อความในหน้านี้ด้วย OCR...")
            self.cv.configure(cursor="watch")
            self.cv.update_idletasks()
            try:
                pix = page.get_pixmap(dpi=200, alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                res = self.ocr_image(img)
            finally:
                self.cv.configure(cursor="")
            k, o = 72.0 / 200, page.rect
            for ln in res.get("lines", []):
                xs = [p[0] * k + o.x0 for p in ln["box"]]
                ys = [p[1] * k + o.y0 for p in ln["box"]]
                r = pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
                out.append((r, ln["text"].strip(), max(6, round(r.height / 1.45)), (0, 0, 0)))
        cache[key] = out
        self._line_cache = cache
        return out

    def edit_existing(self, x, y):
        try:
            lines = self.page_lines()
        except Exception as e:  # noqa: BLE001
            self.status(f"อ่านข้อความไม่สำเร็จ: {e}")
            return
        hit = [ln for ln in lines if ln[0].x0 - 2 <= x <= ln[0].x1 + 2 and ln[0].y0 - 2 <= y <= ln[0].y1 + 2]
        if not hit:
            self.status("ไม่พบข้อความตรงจุดนี้ — คลิกให้ตรงบรรทัดที่ต้องการแก้")
            return
        r, text, size, color = min(hit, key=lambda ln: ln[0].width * ln[0].height)
        # แสดงกรอบบรรทัดที่จะแก้ระหว่างพิมพ์
        a, b = self.to_cv(r.x0, r.y0), self.to_cv(r.x1, r.y1)
        self.cv.create_rectangle(a[0] - 2, a[1] - 2, b[0] + 2, b[1] + 2, outline="#e8710a", width=2, tags="editbox")
        s = self.text_dialog(text, "แก้ข้อความเดิม")
        self.cv.delete("editbox")
        if s is None or s == text:
            return
        self.push_undo()
        pad = max(1.0, r.height * 0.08)
        self.items.append({"type": "erase", "page": self.page,
                           "rect": [r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad]})
        if s.strip():
            self.items.append({"type": "text", "page": self.page, "rect": [r.x0, r.y0, r.x1, r.y1],
                               "text": s, "size": size, "color": color, "fontname": self.v_font.get()})
            self.sel = len(self.items) - 1
        self.draw_items()
        self.status("แก้แล้ว — ลากย้ายข้อความใหม่ได้ ข้อความเดิมจะถูกลบจริงตอนบันทึก")

    def on_motion(self, e):
        if not self.drag:
            return
        cx, cy = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        kind, sx, sy, lx, ly = self.drag
        if kind == "pan":
            self.cv.scan_dragto(e.x, e.y, gain=1)
            return
        if kind == "move":
            self.cv.move(f"it{self.sel}", cx - lx, cy - ly)
        elif kind == "resize":
            it = self.items[self.sel]
            x1, y1 = self.to_pt(cx, cy)
            it["rect"] = [it["rect"][0], it["rect"][1], max(x1, it["rect"][0] + 2), max(y1, it["rect"][1] + 2)]
            self.draw_items()
        else:
            self.cv.coords("rubber", sx, sy, cx, cy)
        self.drag = (kind, sx, sy, cx, cy)

    def on_release(self, e):
        if not self.drag:
            return
        kind, sx, sy, cx, cy = self.drag
        self.drag = None
        if kind == "pan":
            return
        if kind == "grab":
            self.cv.delete("rubber")
            if abs(cx - sx) < 4 or abs(cy - sy) < 4:
                return
            x0, y0 = self.to_pt(min(sx, cx), min(sy, cy))
            x1, y1 = self.to_pt(max(sx, cx), max(sy, cy))
            self.grab_text(pymupdf.Rect(x0, y0, x1, y1))
            return
        if kind == "resize":
            self.draw_items()
            return
        if kind == "move":
            dx, dy = (cx - sx) / self.zoom, (cy - sy) / self.zoom
            if abs(dx) + abs(dy) > 0.5:
                self.push_undo()
                it = self.items[self.sel]
                x0, y0, x1, y1 = it["rect"]
                it["rect"] = [x0 + dx, y0 + dy, x1 + dx, y1 + dy]
                it.pop("auto", None) if it["type"] != "black" else None
            self.draw_items()
            return
        self.cv.delete("rubber")
        if abs(cx - sx) < 4 or abs(cy - sy) < 4:
            return
        x0, y0 = self.to_pt(min(sx, cx), min(sy, cy))
        x1, y1 = self.to_pt(max(sx, cx), max(sy, cy))
        self.push_undo()
        self.items.append({"type": self.tool.get(), "page": self.page, "rect": [x0, y0, x1, y1]})
        self.draw_items()

    def on_double(self, e):
        if not self.doc:
            return
        idx = self.hit(self.cv.canvasx(e.x), self.cv.canvasy(e.y))
        if idx is not None and self.items[idx]["type"] == "text":
            self.edit_text(idx)

    # ---------- ข้อความ
    def pick_color(self):
        c = colorchooser.askcolor(color=_hex(self.color), title="เลือกสีตัวอักษร")
        if c and c[0]:
            self.set_color(tuple(v / 255 for v in c[0]))

    def set_color(self, rgb):
        self.color = rgb
        self.sw.configure(bg=_hex(rgb))
        if self.sel is not None and self.items[self.sel]["type"] == "text":
            self.push_undo()
            self.items[self.sel]["color"] = rgb
            self.draw_items()

    def text_dialog(self, init="", title="พิมพ์ข้อความ"):
        top = tk.Toplevel(self.parent)
        top.title(title)
        top.transient(self.parent.winfo_toplevel())
        top.grab_set()
        ttk.Label(top, text="พิมพ์ข้อความ (Enter = ขึ้นบรรทัดใหม่, Ctrl+Enter = ตกลง)").pack(anchor="w", padx=10, pady=(10, 4))
        fi = self.font_info(self.v_font.get())
        fam = fi[1] if fi else "Tahoma"
        txt = tk.Text(top, width=46, height=5, font=(fam, 14), wrap="word", undo=True)
        txt.pack(fill="both", expand=True, padx=10)
        txt.insert("1.0", init)
        txt.focus_set()
        res = {"v": None}

        def ok(_=None):
            res["v"] = txt.get("1.0", "end").rstrip("\n")
            top.destroy()
            return "break"

        row = ttk.Frame(top)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Button(row, text="ตกลง", command=ok).pack(side="right")
        ttk.Button(row, text="ยกเลิก", command=top.destroy).pack(side="right", padx=6)
        txt.bind("<Control-Return>", ok)
        txt.bind("<Command-Return>", ok)
        top.bind("<Escape>", lambda e: top.destroy())
        top.wait_window()
        return res["v"]

    def add_text(self, x, y):
        s = self.text_dialog()
        if not s or not s.strip():
            return
        try:
            size = max(6, min(72, int(self.v_size.get())))
        except (tk.TclError, ValueError):
            size = 16
        self.push_undo()
        self.items.append({"type": "text", "page": self.page, "rect": [x, y, x + 10, y + size],
                           "text": s, "size": size, "color": self.color, "fontname": self.v_font.get()})
        self.sel = len(self.items) - 1
        self.draw_items()
        self.status("วางข้อความแล้ว — เปลี่ยนเป็นเครื่องมือ \"เลือก / ย้าย\" เพื่อลากย้ายได้")

    def edit_text(self, idx):
        it = self.items[idx]
        s = self.text_dialog(it["text"], "แก้ไขข้อความ")
        if s is None:
            return
        self.push_undo()
        if not s.strip():
            self.items.pop(idx)
            self.sel = None
        else:
            it["text"] = s
            try:
                it["size"] = max(6, min(72, int(self.v_size.get())))
            except (tk.TclError, ValueError):
                pass
            it["fontname"] = self.v_font.get()
        self.draw_items()

    # ---------- หน้า
    def rotate_page(self):
        if not self.doc or self.busy:
            return
        self._line_cache = {}
        self.push_undo(with_doc=True)
        page = self.doc[self.page]
        H = page.rect.height
        page.set_rotation((page.rotation + 90) % 360)
        for it in self.items:
            if it["page"] == self.page:
                x0, y0, x1, y1 = it["rect"]
                if it["type"] == "text":
                    it["rect"] = [H - y0 - (y1 - y0), x0, H - y0, x0 + (x1 - x0)]
                else:
                    it["rect"] = [H - y1, x0, H - y0, x1]
        self.goto(self.page)

    def delete_page(self):
        if not self.doc or self.busy:
            return
        self._line_cache = {}
        if self.doc.page_count <= 1:
            messagebox.showinfo("แก้ไข PDF", "เอกสารเหลือหน้าเดียว ลบไม่ได้")
            return
        if not messagebox.askyesno("แก้ไข PDF", f"ลบหน้า {self.page + 1} ออกจากไฟล์ที่จะบันทึก?"):
            return
        self.push_undo(with_doc=True)
        p = self.page
        self.doc.delete_page(p)
        self.items = [it for it in self.items if it["page"] != p]
        for it in self.items:
            if it["page"] > p:
                it["page"] -= 1
        self.goto(min(p, self.doc.page_count - 1))

    # ---------- หาข้อมูลส่วนบุคคล
    def auto_redact(self):
        if not self.doc or self.busy:
            return
        top = tk.Toplevel(self.parent)
        top.title("หาข้อมูลส่วนบุคคลอัตโนมัติ")
        top.transient(self.parent.winfo_toplevel())
        top.grab_set()
        f = ttk.Frame(top, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="ค้นหาแล้ววางกล่องคาดดำให้อัตโนมัติ (ตรวจและแก้ได้ก่อนบันทึก)").pack(anchor="w", pady=(0, 8))
        v_id, v_ph = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="เลขบัตรประชาชน 13 หลัก (ตรวจเลขถูกต้องตามหลักตรวจสอบ)", variable=v_id).pack(anchor="w")
        ttk.Checkbutton(f, text="เบอร์โทรศัพท์", variable=v_ph).pack(anchor="w")
        ttk.Label(f, text="คำอื่น ๆ เช่น ชื่อ-นามสกุล, HN (คั่นด้วยเครื่องหมาย ,)").pack(anchor="w", pady=(8, 2))
        v_kw = tk.StringVar()
        ttk.Entry(f, textvariable=v_kw, width=50).pack(fill="x")
        v_scope = tk.StringVar(value="all")
        r = ttk.Frame(f)
        r.pack(anchor="w", pady=(8, 0))
        ttk.Radiobutton(r, text="ทุกหน้า", value="all", variable=v_scope).pack(side="left")
        ttk.Radiobutton(r, text="เฉพาะหน้านี้", value="one", variable=v_scope).pack(side="left", padx=8)
        res = {}

        def go():
            res.update(kinds={k for k, v in (("id", v_id), ("phone", v_ph)) if v.get()},
                       kws=[k.strip() for k in v_kw.get().split(",") if k.strip()], scope=v_scope.get())
            top.destroy()

        b = ttk.Frame(f)
        b.pack(fill="x", pady=(12, 0))
        ttk.Button(b, text="เริ่มค้นหา", command=go).pack(side="right")
        ttk.Button(b, text="ยกเลิก", command=top.destroy).pack(side="right", padx=6)
        top.wait_window()
        if not res or (not res["kinds"] and not res["kws"]):
            return
        pages = list(range(self.doc.page_count)) if res["scope"] == "all" else [self.page]
        self.start_pii_scan(res["kinds"], res["kws"], pages)

    def start_pii_scan(self, kinds, kws, pages):
        res = {"kinds": kinds, "kws": kws}
        data = self.doc.tobytes()
        self.busy = True
        self.btn_pii.configure(state="disabled")
        self.status("กำลังค้นหา... (หน้าที่เป็นภาพสแกนจะใช้ OCR อาจใช้เวลาสักครู่)")

        def work():
            found = []
            try:
                d = pymupdf.open("pdf", data)
                for i, p in enumerate(pages):
                    self.q.put(("progress", f"กำลังค้นหา หน้า {p + 1} ({i + 1}/{len(pages)})..."))
                    for rect, kind in pdf_ops.find_pii_on_page(d[p], res["kinds"], res["kws"],
                                                               ocr_func=self.ocr_image):
                        found.append((p, rect, kind))
                d.close()
                self.q.put(("pii", found))
            except Exception as e:  # noqa: BLE001
                self.q.put(("pii_err", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "grab":
                    self.busy = False
                    self.show_grabbed(val)
                elif kind == "grab_err":
                    self.busy = False
                    self.status(f"อ่านข้อความไม่สำเร็จ: {val}")
                elif kind == "fonts":
                    cur = self.font_info(self.v_font.get())
                    self.fonts = val
                    self.cb_font.configure(values=[f[0] for f in val])
                    match = self.font_info(cur[0]) if cur else None
                    if match:
                        self.v_font.set(match[0])
                elif kind == "progress":
                    self.status(val)
                elif kind == "pii":
                    self.busy = False
                    self.btn_pii.configure(state="normal")
                    if not val:
                        self.status("ไม่พบข้อมูลที่ค้นหา")
                        continue
                    self.push_undo()
                    counts = {}
                    for p, rect, k in val:
                        self.items.append({"type": "black", "page": p, "rect": rect, "auto": True, "kind": k})
                        counts[k] = counts.get(k, 0) + 1
                    first = min(p for p, _, _ in val)
                    self.goto(first)
                    summary = ", ".join(f"{k} {n}" for k, n in counts.items())
                    pages = sorted({p + 1 for p, _, _ in val})
                    self.status(f"พบ {len(val)} จุด ({summary}) ในหน้า {', '.join(map(str, pages[:10]))}"
                                f"{'...' if len(pages) > 10 else ''} — กล่องขอบแดง = ยังไม่บันทึก ตรวจดู/ปรับแล้วกดบันทึก")
                elif kind == "pii_err":
                    self.busy = False
                    self.btn_pii.configure(state="normal")
                    self.status(f"ค้นหาไม่สำเร็จ: {val}")
        except queue.Empty:
            pass
        self.parent.after(150, self.poll)

    # ================================================================ เครื่องมือ PDF (ทำกับเอกสารที่เปิดอยู่ แล้วแสดงผลให้ดูก่อนบันทึก)
    def _need_doc(self):
        if not self.doc or self.busy:
            if not self.doc:
                messagebox.showinfo("เครื่องมือ PDF", "เปิดไฟล์ก่อน (หรือดับเบิลคลิกชื่อไฟล์ในแท็บแปลงไฟล์)")
            return False
        return True

    def _after_tool(self, msg, goto=None):
        self._line_cache = {}
        self.dirty = True
        self.goto(self.page if goto is None else goto)
        self.status(msg + "  —  ดูผลได้เลย ถ้าไม่ชอบกด Ctrl+Z  ·  พอใจแล้วกด \"บันทึกเป็น...\"")

    def tool_insert(self):
        if not self.doc:
            self.pick_file()
            return
        if self.busy:
            return
        ps = filedialog.askopenfilenames(
            title="เลือกไฟล์ที่จะแทรกต่อท้าย (เลือกได้หลายไฟล์ เรียงตามชื่อ)",
            filetypes=[("PDF และรูปภาพ", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp")])
        if not ps:
            return
        self.push_undo(with_doc=True)
        first_new = self.doc.page_count
        try:
            for p in sorted(ps):
                src = pdf_ops.open_as_pdf(p)
                self.doc.insert_pdf(src)
                src.close()
        except Exception as e:  # noqa: BLE001
            self.undo()
            messagebox.showerror("เครื่องมือ PDF", f"แทรกไฟล์ไม่สำเร็จ\n{e}")
            return
        self._after_tool(f"✓ แทรก {len(ps)} ไฟล์ต่อท้ายแล้ว (เริ่มที่หน้า {first_new + 1}) รวม {self.doc.page_count} หน้า",
                         goto=first_new)

    def tool_keep_pages(self):
        if not self._need_doc():
            return
        n = self.doc.page_count
        spec = simpledialog.askstring("เก็บเฉพาะบางหน้า",
                                      f"เอกสารมี {n} หน้า ระบุหน้าที่จะเก็บ เช่น 1-3, 5, 8-  (8- = ถึงหน้าสุดท้าย)",
                                      parent=self.parent)
        if not spec:
            return
        try:
            pages = pdf_ops.parse_ranges(spec, n)
        except ValueError as e:
            messagebox.showerror("เก็บเฉพาะบางหน้า", str(e))
            return
        self.push_undo(with_doc=True)
        self.doc.select(pages)
        new_items = []
        for new_i, old_i in enumerate(pages):
            for it in self.items:
                if it["page"] == old_i:
                    it2 = dict(it, page=new_i)
                    new_items.append(it2)
        self.items, self.sel = new_items, None
        self._after_tool(f"✓ เหลือ {len(pages)} หน้า", goto=0)

    def tool_split(self):
        if not self._need_doc():
            return
        d = filedialog.askdirectory(title="เลือกโฟลเดอร์ที่จะเก็บไฟล์แยกหน้า", initialdir=str(self.path.parent))
        if not d:
            return
        work = pymupdf.open("pdf", self.doc.tobytes())
        font_map = {f[0]: f[2] for f in self.fonts}
        items = [dict(it, font=font_map.get(it.get("fontname"))) if it["type"] == "text" else it for it in self.items]
        pdf_ops.apply_items(work, items)
        out = pdf_ops.unique_path(Path(d) / f"{self.path.stem}_แยกหน้า")
        out.mkdir(parents=True)
        width = len(str(work.page_count))
        for i in range(work.page_count):
            one = pymupdf.open()
            one.insert_pdf(work, from_page=i, to_page=i)
            one.save(str(out / f"{self.path.stem}_หน้า{str(i + 1).zfill(width)}.pdf"), garbage=3, deflate=True)
            one.close()
        work.close()
        if messagebox.askyesno("แยกทุกหน้า", f"แยกเป็น {self.doc.page_count} ไฟล์แล้ว ที่\n{out}\n\nเปิดโฟลเดอร์ไหม?"):
            pdf_ops.open_file(out)

    def tool_compress(self):
        if not self._need_doc():
            return
        self.status("กำลังบีบอัด...")
        self.cv.configure(cursor="watch")
        self.cv.update_idletasks()
        try:
            before = pdf_ops.doc_size(self.doc)
            self.push_undo(with_doc=True)
            pdf_ops.compress_doc(self.doc)
            after = pdf_ops.doc_size(self.doc)
        finally:
            self.cv.configure(cursor="")
        self._after_tool(f"✓ บีบอัดแล้ว {before / 1e6:.1f} MB → {after / 1e6:.1f} MB (ขนาดโดยประมาณ)")

    def _dialog(self, title):
        top = tk.Toplevel(self.parent)
        top.title(title)
        top.transient(self.parent.winfo_toplevel())
        top.grab_set()
        f = ttk.Frame(top, padding=12)
        f.pack(fill="both", expand=True)
        return top, f

    def _font_row(self, f, row):
        ttk.Label(f, text="ฟอนต์:").grid(row=row, column=0, sticky="w", pady=3)
        v = tk.StringVar(value=self.v_font.get())
        ttk.Combobox(f, textvariable=v, values=[x[0] for x in self.fonts], width=28, state="readonly",
                     height=25).grid(row=row, column=1, sticky="w", pady=3)
        return v

    def tool_watermark(self):
        if not self._need_doc():
            return
        top, f = self._dialog("ใส่ลายน้ำ")
        ttk.Label(f, text="ข้อความ:").grid(row=0, column=0, sticky="w", pady=3)
        v_text = tk.StringVar(value="สำเนา")
        ttk.Combobox(f, textvariable=v_text, width=30,
                     values=["สำเนา", "สำเนาถูกต้อง", "ใช้ภายในเท่านั้น", "ลับ", "เอกสารประกอบการตรวจสอบ",
                             "ห้ามเผยแพร่"]).grid(row=0, column=1, sticky="w", pady=3)
        v_font = self._font_row(f, 1)
        opts = [("ความเข้ม:", ["จางมาก", "จาง", "ปานกลาง", "เข้ม"], "จาง"),
                ("ขนาด:", ["เล็ก", "กลาง", "ใหญ่"], "ใหญ่"),
                ("แนว:", ["เอียง", "แนวนอน"], "เอียง")]
        vs = []
        for i, (lab, vals, dv) in enumerate(opts, start=2):
            ttk.Label(f, text=lab).grid(row=i, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=dv)
            ttk.Combobox(f, textvariable=v, values=vals, width=10, state="readonly").grid(row=i, column=1, sticky="w", pady=3)
            vs.append(v)
        v_red = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="สีแดง (ไม่ติ๊ก = สีเทา)", variable=v_red).grid(row=5, column=1, sticky="w", pady=3)
        res = {}

        def ok():
            res.update(text=v_text.get().strip(), font=self.font_info(v_font.get()),
                       op={"จางมาก": 0.08, "จาง": 0.15, "ปานกลาง": 0.25, "เข้ม": 0.4}[vs[0].get()],
                       sc={"เล็ก": 0.45, "กลาง": 0.65, "ใหญ่": 0.85}[vs[1].get()],
                       ang=45 if vs[2].get() == "เอียง" else 0,
                       color=(0.8, 0, 0) if v_red.get() else (0.45, 0.45, 0.45))
            top.destroy()

        b = ttk.Frame(f)
        b.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(b, text="ยกเลิก", command=top.destroy).pack(side="right")
        ttk.Button(b, text="ใส่ลายน้ำ", command=ok).pack(side="right", padx=6)
        top.wait_window()
        if not res or not res["text"]:
            return
        self.push_undo(with_doc=True)
        pdf_ops.watermark_doc(self.doc, res["text"], res["font"][2] if res["font"] else None,
                              scale=res["sc"], opacity=res["op"], angle=res["ang"], color=res["color"])
        self._after_tool(f"✓ ใส่ลายน้ำ \"{res['text']}\" ทุกหน้าแล้ว")

    def tool_pagenum(self):
        if not self._need_doc():
            return
        top, f = self._dialog("เลขหน้า / ข้อความหัว-ท้ายกระดาษ")
        ttk.Label(f, text="ข้อความ:").grid(row=0, column=0, sticky="w", pady=3)
        v_tpl = tk.StringVar(value="หน้า {n} / {total}")
        ttk.Combobox(f, textvariable=v_tpl, width=34,
                     values=["หน้า {n} / {total}", "หน้า {n}", "{n}", "- {n} -", "หน้า {n} จาก {total} หน้า",
                             "เอกสารประกอบการตรวจสอบ หน้า {n}"]).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(f, text="{n} = เลขหน้า, {total} = จำนวนหน้าทั้งหมด (พิมพ์ข้อความเองได้)",
                  foreground="#666").grid(row=1, column=1, sticky="w")
        ttk.Label(f, text="ตำแหน่ง:").grid(row=2, column=0, sticky="w", pady=3)
        pos = {"ล่าง กลาง": "bottom-center", "ล่าง ขวา": "bottom-right", "ล่าง ซ้าย": "bottom-left",
               "บน กลาง": "top-center", "บน ขวา": "top-right", "บน ซ้าย": "top-left"}
        v_pos = tk.StringVar(value="ล่าง กลาง")
        ttk.Combobox(f, textvariable=v_pos, values=list(pos), width=12, state="readonly").grid(
            row=2, column=1, sticky="w", pady=3)
        v_font = self._font_row(f, 3)
        ttk.Label(f, text="ขนาดตัวอักษร:").grid(row=4, column=0, sticky="w", pady=3)
        v_size = tk.IntVar(value=14)
        ttk.Spinbox(f, from_=6, to=36, textvariable=v_size, width=5).grid(row=4, column=1, sticky="w", pady=3)
        ttk.Label(f, text="เริ่มนับที่:").grid(row=5, column=0, sticky="w", pady=3)
        v_start = tk.IntVar(value=1)
        ttk.Spinbox(f, from_=0, to=9999, textvariable=v_start, width=6).grid(row=5, column=1, sticky="w", pady=3)
        v_thai, v_skip = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="ใช้เลขไทย (๑ ๒ ๓)", variable=v_thai).grid(row=6, column=1, sticky="w")
        ttk.Checkbutton(f, text="ไม่ใส่หน้าแรก (หน้าปก)", variable=v_skip).grid(row=7, column=1, sticky="w")
        res = {}

        def ok():
            try:
                size, start = int(v_size.get()), int(v_start.get())
            except (tk.TclError, ValueError):
                size, start = 14, 1
            res.update(tpl=v_tpl.get(), pos=pos[v_pos.get()], font=self.font_info(v_font.get()),
                       size=size, start=start, thai=v_thai.get(), skip=v_skip.get())
            top.destroy()

        b = ttk.Frame(f)
        b.grid(row=8, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(b, text="ยกเลิก", command=top.destroy).pack(side="right")
        ttk.Button(b, text="ใส่", command=ok).pack(side="right", padx=6)
        top.wait_window()
        if not res or not res["tpl"].strip():
            return
        self.push_undo(with_doc=True)
        pdf_ops.page_numbers_doc(self.doc, res["tpl"], res["pos"], res["font"][2] if res["font"] else None,
                                 res["size"], res["start"], res["thai"], res["skip"])
        self._after_tool("✓ ใส่เลขหน้า/ข้อความหัว-ท้ายแล้ว")

    def key(self, name):
        """เรียกจาก app เมื่อแท็บนี้แสดงอยู่"""
        if name == "undo":
            self.undo()
        elif name == "delete":
            self.delete_selected()
        else:
            step = 10 if name.endswith("10") else 1
            d = {"left": (-step, 0), "right": (step, 0), "up": (0, -step), "down": (0, step)}
            self.nudge(*d[name.rstrip("10")])

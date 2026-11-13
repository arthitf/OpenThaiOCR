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
TOOLS = [("select", "เลือก / ย้าย"), ("text", "พิมพ์ข้อความ"), ("white", "กล่องขาวปิดทับ"),
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

        # ---------- แถบบน: ไฟล์ / หน้า / ซูม
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=6, pady=(8, 2))
        ttk.Button(top, text="เปิดไฟล์...", command=self.pick_file).pack(side="left")
        self.btn_save = ttk.Button(top, text="บันทึกเป็น...", command=self.save, state="disabled")
        self.btn_save.pack(side="left", padx=(4, 12))
        ttk.Button(top, text="◀", width=3, command=lambda: self.goto(self.page - 1)).pack(side="left")
        self.lbl_page = ttk.Label(top, text="หน้า - / -", width=12, anchor="center")
        self.lbl_page.pack(side="left")
        ttk.Button(top, text="▶", width=3, command=lambda: self.goto(self.page + 1)).pack(side="left")
        ttk.Button(top, text="−", width=3, command=lambda: self.set_zoom(self.zoom / 1.25)).pack(side="left", padx=(12, 0))
        ttk.Button(top, text="พอดีจอ", command=self.zoom_fit).pack(side="left")
        ttk.Button(top, text="+", width=3, command=lambda: self.set_zoom(self.zoom * 1.25)).pack(side="left")
        ttk.Button(top, text="ลบหน้านี้", command=self.delete_page).pack(side="right")
        ttk.Button(top, text="หมุนหน้า ⟳", command=self.rotate_page).pack(side="right", padx=4)

        # ---------- แถบเครื่องมือ
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=6, pady=2)
        self.tool = tk.StringVar(value="select")
        for key, label in TOOLS:
            ttk.Radiobutton(bar, text=label, value=key, variable=self.tool,
                            command=self.on_tool).pack(side="left", padx=(0, 6))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self.v_font = tk.StringVar(value=self.fonts[0][0] if self.fonts else "")
        self.cb_font = ttk.Combobox(bar, textvariable=self.v_font, values=[f[0] for f in self.fonts], width=22,
                                    state="readonly", height=25)
        self.cb_font.pack(side="left")
        threading.Thread(target=self._load_fonts, daemon=True).start()
        self.v_size = tk.IntVar(value=16)
        ttk.Spinbox(bar, from_=6, to=72, textvariable=self.v_size, width=4).pack(side="left", padx=4)
        self.color = (0, 0, 0)
        self.sw = tk.Label(bar, width=2, bg="#000000", relief="solid", bd=1, cursor="hand2")
        self.sw.pack(side="left", padx=(4, 2))
        self.sw.bind("<Button-1>", lambda e: self.pick_color())
        for name, rgb in COLORS:
            b = tk.Label(bar, text=name, fg=_hex(rgb), cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, c=rgb: self.set_color(c))

        bar2 = ttk.Frame(parent)
        bar2.pack(fill="x", padx=6, pady=(2, 4))
        self.btn_pii = ttk.Button(bar2, text="หาข้อมูลส่วนบุคคลอัตโนมัติ...", command=self.auto_redact,
                                  state="disabled")
        self.btn_pii.pack(side="left")
        ttk.Button(bar2, text=f"ย้อนกลับ ({mod_txt}+Z)", command=self.undo).pack(side="left", padx=6)
        ttk.Button(bar2, text="ลบที่เลือก (Delete)", command=self.delete_selected).pack(side="left")
        self.lbl = ttk.Label(bar2, foreground="#555",
                             text="เปิดไฟล์ PDF หรือรูปภาพเพื่อเริ่มแก้ไข (ไฟล์ต้นฉบับจะไม่ถูกแก้)")
        self.lbl.pack(side="left", padx=10)

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
            if t == "white":
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
        tips = {"select": "คลิกเลือก · ลากย้าย · ลากมุมขวาล่างปรับขนาด · ดับเบิลคลิกแก้ข้อความ · Delete ลบ",
                "text": "คลิกตรงจุดที่ต้องการแล้วพิมพ์ข้อความ",
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
        if tool == "select":
            if self.sel is not None and self.items[self.sel]["type"] != "text":
                hx, hy = self.to_cv(*self.items[self.sel]["rect"][2:])
                if abs(cx - hx) <= 8 and abs(cy - hy) <= 8:
                    self.push_undo()
                    self.drag = ("resize", cx, cy, cx, cy)
                    return
            self.sel = self.hit(cx, cy)
            self.draw_items()
            if self.sel is not None:
                self.drag = ("move", cx, cy, cx, cy)
        elif tool == "text":
            idx = self.hit(cx, cy)
            if idx is not None and self.items[idx]["type"] == "text":
                self.edit_text(idx)
            else:
                self.add_text(*self.to_pt(cx, cy))
        else:
            self.drag = ("box", cx, cy, cx, cy)
            self.cv.create_rectangle(cx, cy, cx, cy, outline="#1a73e8", dash=(4, 2), width=2, tags="rubber")

    def on_motion(self, e):
        if not self.drag:
            return
        cx, cy = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        kind, sx, sy, lx, ly = self.drag
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
        if not self.doc or self.tool.get() != "select":
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
                if kind == "fonts":
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

    def key(self, name):
        """เรียกจาก app เมื่อแท็บนี้แสดงอยู่"""
        if name == "undo":
            self.undo()
        elif name == "delete":
            self.delete_selected()


class ToolsTab:
    def __init__(self, parent, editor: EditorTab, select_editor, has_dnd=False, dnd_files=None):
        self.parent = parent
        self.editor = editor
        self.select_editor = select_editor
        self.files: list[Path] = []
        self.q: queue.Queue = queue.Queue()

        f = ttk.LabelFrame(parent, text=" ไฟล์ (PDF หรือรูปภาพ) — เรียงลำดับได้ด้วยปุ่มขึ้น/ลง ")
        f.pack(fill="both", expand=True, padx=6, pady=(8, 4))
        self.lst = tk.Listbox(f, selectmode="extended", activestyle="none", height=10)
        self.lst.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(f, orient="vertical", command=self.lst.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=8)
        self.lst.configure(yscrollcommand=sb.set)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        b = ttk.Frame(f)
        b.grid(row=0, column=2, sticky="n", padx=8, pady=8)
        for t, c in (("เพิ่มไฟล์...", self.add_files), ("▲ ขึ้น", lambda: self.move(-1)), ("▼ ลง", lambda: self.move(1)),
                     ("ลบที่เลือก", self.remove), ("ล้างทั้งหมด", self.clear)):
            ttk.Button(b, text=t, command=c, width=14).pack(fill="x", pady=2)
        self.lst.bind("<Double-Button-1>", lambda e: self.edit())
        if has_dnd and dnd_files:
            try:
                self.lst.drop_target_register(dnd_files)
                self.lst.dnd_bind("<<Drop>>", lambda e: self.add(self.lst.tk.splitlist(e.data)))
            except Exception:  # noqa: BLE001
                pass

        a = ttk.LabelFrame(parent, text=" ทำอะไร ")
        a.pack(fill="x", padx=6, pady=4)
        rows = [
            ("รวมเป็นไฟล์เดียว", self.merge, "รวมทุกไฟล์ในรายการตามลำดับ (รูปภาพจะกลายเป็นหน้า PDF)"),
            ("ดึงบางหน้า...", self.extract, "เลือก 1 ไฟล์ แล้วระบุหน้า เช่น 1-3, 5, 8-"),
            ("แยกทุกหน้า", self.split, "แยกไฟล์ที่เลือก ออกเป็นไฟล์ละ 1 หน้า"),
            ("บีบอัดขนาด", self.compress, "ลดขนาดไฟล์ที่เลือก (ย่อรูปที่ละเอียดเกินเหลือ 150 dpi)"),
            ("ใส่ลายน้ำ...", self.watermark, "ข้อความเอียงจาง ๆ กลางทุกหน้า เช่น สำเนา, ใช้ภายใน"),
            ("เลขหน้า / หัว-ท้าย...", self.page_numbers, "ใส่เลขหน้า (เลขไทยได้) หรือข้อความหัว/ท้ายกระดาษทุกหน้า"),
            ("แก้ไขไฟล์นี้", self.edit, "เปิดไฟล์ที่เลือกในแท็บ \"แก้ไข PDF\""),
        ]
        for i, (t, c, d) in enumerate(rows):
            ttk.Button(a, text=t, command=c, width=18).grid(row=i, column=0, sticky="w", padx=8, pady=2)
            ttk.Label(a, text=d, foreground="#555").grid(row=i, column=1, sticky="w", padx=4)
        self.lbl = ttk.Label(parent, foreground="#555", text="")
        self.lbl.pack(anchor="w", padx=8, pady=(2, 8))
        self.parent.after(150, self.poll)

    def refresh(self):
        self.lst.delete(0, "end")
        for p in self.files:
            self.lst.insert("end", str(p))

    def add(self, paths):
        have = {str(p).lower() for p in self.files}
        for p in paths:
            p = Path(p)
            if p.is_file() and p.suffix.lower() in ({".pdf"} | pdf_ops.IMG_EXT) and str(p).lower() not in have:
                self.files.append(p)
        self.refresh()

    def add_files(self):
        ps = filedialog.askopenfilenames(
            title="เลือกไฟล์", filetypes=[("PDF และรูปภาพ", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp")])
        if ps:
            self.add(ps)

    def move(self, d):
        sel = list(self.lst.curselection())
        if len(sel) != 1:
            return
        i = sel[0]
        j = i + d
        if 0 <= j < len(self.files):
            self.files[i], self.files[j] = self.files[j], self.files[i]
            self.refresh()
            self.lst.selection_set(j)

    def remove(self):
        for i in reversed(self.lst.curselection()):
            self.files.pop(i)
        self.refresh()

    def clear(self):
        self.files.clear()
        self.refresh()

    def selected(self, need_one=False):
        sel = [self.files[i] for i in self.lst.curselection()]
        if not sel and len(self.files) == 1:
            sel = list(self.files)
        if not sel:
            messagebox.showinfo("เครื่องมือ PDF", "กรุณาคลิกเลือกไฟล์ในรายการก่อน")
            return []
        if need_one and len(sel) != 1:
            messagebox.showinfo("เครื่องมือ PDF", "กรุณาเลือกไฟล์เดียว")
            return []
        return sel

    def run(self, label, fn):
        self.lbl.config(text=f"กำลัง{label}...")

        def work():
            try:
                self.q.put(("ok", fn()))
            except Exception as e:  # noqa: BLE001
                self.q.put(("err", f"{label}ไม่สำเร็จ: {type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "ok":
                    msg, target = val
                    self.lbl.config(text=msg)
                    if target and messagebox.askyesno("เครื่องมือ PDF", f"{msg}\n\nเปิดดูเลยไหม?"):
                        pdf_ops.open_file(target)
                else:
                    self.lbl.config(text=val)
                    messagebox.showerror("เครื่องมือ PDF", val)
        except queue.Empty:
            pass
        self.parent.after(150, self.poll)

    def merge(self):
        if len(self.files) < 2:
            messagebox.showinfo("เครื่องมือ PDF", "เพิ่มไฟล์อย่างน้อย 2 ไฟล์")
            return
        p = filedialog.asksaveasfilename(title="บันทึกไฟล์ที่รวมแล้ว", defaultextension=".pdf",
                                         initialdir=str(self.files[0].parent), initialfile="รวมไฟล์.pdf",
                                         filetypes=[("PDF", "*.pdf")])
        if not p:
            return
        files = list(self.files)
        self.run("รวมไฟล์", lambda: (f"✓ รวม {len(files)} ไฟล์ ได้ {pdf_ops.merge(files, p)} หน้า: {p}", p))

    def extract(self):
        sel = self.selected(need_one=True)
        if not sel:
            return
        src = sel[0]
        spec = simpledialog.askstring("ดึงบางหน้า", "ระบุหน้า เช่น 1-3, 5, 8-  (8- = ตั้งแต่หน้า 8 จนจบ)",
                                      parent=self.parent)
        if not spec:
            return
        safe = spec.replace(" ", "").replace(",", "_")
        out = pdf_ops.unique_path(src.with_name(f"{src.stem}_หน้า{safe}.pdf"))
        self.run("ดึงหน้า", lambda: (f"✓ ได้ {pdf_ops.extract_pages(src, spec, out)} หน้า: {out}", out))

    def split(self):
        sel = self.selected()
        if not sel:
            return

        def job():
            outs = []
            for s in sel:
                d = pdf_ops.unique_path(s.with_name(f"{s.stem}_แยกหน้า"))
                n = pdf_ops.split_each_page(s, d)
                outs.append((d, n))
            return (f"✓ แยกเสร็จ {sum(n for _, n in outs)} ไฟล์ ที่ {outs[0][0]}"
                    + (f" และอีก {len(outs) - 1} โฟลเดอร์" if len(outs) > 1 else ""), outs[0][0])
        self.run("แยกหน้า", job)

    def compress(self):
        sel = self.selected()
        if not sel:
            return

        def job():
            lines, last = [], None
            for s in sel:
                out = pdf_ops.unique_path(s.with_name(f"{s.stem}_บีบอัด.pdf"))
                a, b = pdf_ops.compress(s, out)
                lines.append(f"{s.name}: {a / 1e6:.1f} MB → {b / 1e6:.1f} MB")
                last = out
            return "✓ " + "  |  ".join(lines), last
        self.run("บีบอัด", job)

    # ---------- ลายน้ำ / เลขหน้า
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
        fonts = self.editor.fonts
        v = tk.StringVar(value=fonts[0][0] if fonts else "")
        ttk.Combobox(f, textvariable=v, values=[x[0] for x in fonts], width=28, state="readonly",
                     height=25).grid(row=row, column=1, sticky="w", pady=3)
        return v

    def watermark(self):
        sel = self.selected()
        if not sel:
            return
        top, f = self._dialog("ใส่ลายน้ำ")
        ttk.Label(f, text="ข้อความ:").grid(row=0, column=0, sticky="w", pady=3)
        v_text = tk.StringVar(value="สำเนา")
        ttk.Combobox(f, textvariable=v_text, width=30,
                     values=["สำเนา", "สำเนาถูกต้อง", "ใช้ภายในเท่านั้น", "ลับ", "เอกสารประกอบการตรวจสอบ",
                             "ห้ามเผยแพร่"]).grid(row=0, column=1, sticky="w", pady=3)
        v_font = self._font_row(f, 1)
        ttk.Label(f, text="ความเข้ม:").grid(row=2, column=0, sticky="w", pady=3)
        v_op = tk.StringVar(value="จาง")
        ttk.Combobox(f, textvariable=v_op, values=["จางมาก", "จาง", "ปานกลาง", "เข้ม"], width=10,
                     state="readonly").grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(f, text="ขนาด:").grid(row=3, column=0, sticky="w", pady=3)
        v_sc = tk.StringVar(value="ใหญ่")
        ttk.Combobox(f, textvariable=v_sc, values=["เล็ก", "กลาง", "ใหญ่"], width=10,
                     state="readonly").grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(f, text="แนว:").grid(row=4, column=0, sticky="w", pady=3)
        v_ang = tk.StringVar(value="เอียง")
        ttk.Combobox(f, textvariable=v_ang, values=["เอียง", "แนวนอน"], width=10,
                     state="readonly").grid(row=4, column=1, sticky="w", pady=3)
        v_red = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="สีแดง (ไม่ติ๊ก = สีเทา)", variable=v_red).grid(row=5, column=1, sticky="w", pady=3)
        res = {}

        def ok():
            res.update(text=v_text.get().strip(), font=self.editor.font_info(v_font.get()),
                       op={"จางมาก": 0.08, "จาง": 0.15, "ปานกลาง": 0.25, "เข้ม": 0.4}[v_op.get()],
                       sc={"เล็ก": 0.45, "กลาง": 0.65, "ใหญ่": 0.85}[v_sc.get()],
                       ang=45 if v_ang.get() == "เอียง" else 0,
                       color=(0.8, 0, 0) if v_red.get() else (0.45, 0.45, 0.45))
            top.destroy()

        b = ttk.Frame(f)
        b.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(b, text="ยกเลิก", command=top.destroy).pack(side="right")
        ttk.Button(b, text="ใส่ลายน้ำ", command=ok).pack(side="right", padx=6)
        top.wait_window()
        if not res or not res["text"]:
            return

        def job():
            last = None
            for s in sel:
                out = pdf_ops.unique_path(s.with_name(f"{s.stem}_ลายน้ำ.pdf"))
                pdf_ops.add_watermark(s, out, res["text"], res["font"][2] if res["font"] else None,
                                      scale=res["sc"], opacity=res["op"], angle=res["ang"], color=res["color"])
                last = out
            return f"✓ ใส่ลายน้ำแล้ว {len(sel)} ไฟล์: {last}", last
        self.run("ใส่ลายน้ำ", job)

    def page_numbers(self):
        sel = self.selected()
        if not sel:
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
        v_thai = tk.BooleanVar(value=False)
        v_skip = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="ใช้เลขไทย (๑ ๒ ๓)", variable=v_thai).grid(row=6, column=1, sticky="w")
        ttk.Checkbutton(f, text="ไม่ใส่หน้าแรก (หน้าปก)", variable=v_skip).grid(row=7, column=1, sticky="w")
        res = {}

        def ok():
            try:
                size, start = int(v_size.get()), int(v_start.get())
            except (tk.TclError, ValueError):
                size, start = 14, 1
            res.update(tpl=v_tpl.get(), pos=pos[v_pos.get()], font=self.editor.font_info(v_font.get()),
                       size=size, start=start, thai=v_thai.get(), skip=v_skip.get())
            top.destroy()

        b = ttk.Frame(f)
        b.grid(row=8, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(b, text="ยกเลิก", command=top.destroy).pack(side="right")
        ttk.Button(b, text="ใส่", command=ok).pack(side="right", padx=6)
        top.wait_window()
        if not res or not res["tpl"].strip():
            return

        def job():
            last = None
            for s in sel:
                out = pdf_ops.unique_path(s.with_name(f"{s.stem}_เลขหน้า.pdf"))
                pdf_ops.add_page_numbers(s, out, res["tpl"], res["pos"], res["font"][2] if res["font"] else None,
                                         res["size"], res["start"], res["thai"], res["skip"])
                last = out
            return f"✓ ใส่แล้ว {len(sel)} ไฟล์: {last}", last
        self.run("ใส่เลขหน้า", job)

    def edit(self):
        sel = self.selected(need_one=True)
        if sel:
            self.select_editor()
            self.editor.open_path(sel[0])

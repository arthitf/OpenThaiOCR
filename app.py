# -*- coding: utf-8 -*-
"""
OpenThai OCR - โปรแกรมแปลง PDF/รูปสแกน เป็นข้อความภาษาไทย (ออฟไลน์)
ใช้โมเดล iapp/OpenThai2.0-OCR-Text (Apache-2.0)
- ลากไฟล์มาวางในหน้าต่าง หรือบนไอคอนโปรแกรม
- กด Ctrl+V (Mac: Cmd+V) เพื่อวางภาพหน้าจอแล้วอ่านทันที
"""
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    import ocr_core

    APP = "OpenThai OCR"
    VERSION = "1.4"
    AUTHOR = "arthit.f"
    IS_MAC = sys.platform == "darwin"
    MOD = "Command" if IS_MAC else "Control"
    MOD_TXT = "Cmd" if IS_MAC else "Ctrl"
    UI_FONT = ("Tahoma", 10)
    TEXT_FONT = ("Tahoma", 11)
    LOW_CONF = 0.90

    # ลากไฟล์มาวาง (ถ้ามีไลบรารี tkinterdnd2)
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        root = TkinterDnD.Tk()
        HAS_DND = True
    except Exception:  # noqa: BLE001
        root = tk.Tk()
        HAS_DND = False

    root.title(f"{APP} — แปลงเอกสารสแกนเป็นข้อความ (ออฟไลน์)")
    root.geometry("1000x780")
    root.minsize(780, 620)
    root.option_add("*Font", UI_FONT)
    style = ttk.Style()
    for w in ("TLabel", "TButton", "TCheckbutton", "TRadiobutton", "TLabelframe.Label",
              "TCombobox", "TNotebook.Tab"):
        style.configure(w, font=UI_FONT)
    style.configure("TNotebook.Tab", padding=(14, 4))

    files: list[Path] = []
    results: list[dict] = []          # [{"name", "path", "pages"}]
    msgq: queue.Queue = queue.Queue()
    state = {"running": False, "stop": False, "last_dirs": set(), "clip_busy": False}

    # ---------------- footer (อยู่ล่างสุดเสมอ)
    footer = ttk.Frame(root)
    footer.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
    ttk.Label(footer, text=f"พัฒนาโดย {AUTHOR}", foreground="#777").pack(side="left")
    ttk.Label(footer, text=f"{APP} v{VERSION}  ·  โมเดล OCR: iApp Technology",
              foreground="#999").pack(side="right")

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=(8, 4))
    tab_run = ttk.Frame(nb)
    tab_txt = ttk.Frame(nb)
    nb.add(tab_run, text="  แปลงไฟล์  ")
    nb.add(tab_txt, text="  ข้อความ  ")
    tab_edit = ttk.Frame(nb)
    nb.add(tab_edit, text="  แก้ไข / เครื่องมือ PDF  ")
    import pdf_editor
    _dnd = DND_FILES if HAS_DND else None
    editor = pdf_editor.EditorTab(tab_edit, ocr_core.ocr_image, HAS_DND, _dnd, MOD, MOD_TXT)

    def active_tab():
        cur = nb.select()
        return {str(tab_edit): "edit"}.get(cur, "ocr")

    def open_in_editor(path):
        if path:
            nb.select(tab_edit)
            editor.open_path(path)

    # ================================================================ แท็บ 1: แปลงไฟล์
    hint = "ลากไฟล์มาวางที่นี่ได้" if HAS_DND else "เลือกได้หลายไฟล์พร้อมกัน"
    f_in = ttk.LabelFrame(tab_run, text=f" 1. เลือกไฟล์ (PDF, JPG, PNG, TIFF) — {hint} ")
    f_in.pack(fill="both", expand=True, padx=6, pady=(8, 4))
    lst = tk.Listbox(f_in, height=6, selectmode="extended", activestyle="none")
    sb = ttk.Scrollbar(f_in, orient="vertical", command=lst.yview)
    lst.configure(yscrollcommand=sb.set)
    lst.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
    sb.grid(row=0, column=1, sticky="ns", pady=8)
    btns = ttk.Frame(f_in)
    btns.grid(row=0, column=2, sticky="n", padx=8, pady=8)
    f_in.columnconfigure(0, weight=1)
    f_in.rowconfigure(0, weight=1)
    lst.bind("<Double-Button-1>", lambda e: lst.curselection() and open_in_editor(files[lst.curselection()[0]]))
    lbl_count = ttk.Label(f_in, text="ยังไม่ได้เลือกไฟล์ (ดับเบิลคลิกชื่อไฟล์เพื่อแก้ไข)", foreground="#555")
    lbl_count.grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

    def refresh_list():
        lst.delete(0, "end")
        for f in files:
            lst.insert("end", str(f))
        lbl_count.config(text=f"{len(files)} ไฟล์" if files else "ยังไม่ได้เลือกไฟล์")

    def add_paths(paths):
        new = ocr_core.collect_inputs(paths)
        have = {str(f).lower() for f in files}
        added = [f for f in new if str(f).lower() not in have]
        files.extend(added)
        refresh_list()
        return len(added)

    def pick_files():
        ps = filedialog.askopenfilenames(
            title="เลือกไฟล์ (เลือกได้หลายไฟล์)",
            filetypes=[("PDF และรูปภาพ", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
                       ("ทุกไฟล์", "*.*")])
        if ps:
            add_paths(ps)

    def pick_folder():
        d = filedialog.askdirectory(title="เลือกโฟลเดอร์ (รวมโฟลเดอร์ย่อย)")
        if d:
            add_paths([d])

    def remove_sel():
        for i in reversed(lst.curselection()):
            files.pop(i)
        refresh_list()

    def clear_all():
        files.clear()
        refresh_list()

    for txt, cmd in (("เพิ่มไฟล์...", pick_files), ("เพิ่มโฟลเดอร์...", pick_folder),
                     ("ลบที่เลือก", remove_sel), ("ล้างทั้งหมด", clear_all)):
        ttk.Button(btns, text=txt, command=cmd, width=16).pack(fill="x", pady=2)

    f_opt = ttk.LabelFrame(tab_run, text=" 2. บันทึกผลเป็นไฟล์ (ไม่ติ๊กเลย = แสดงข้อความอย่างเดียว) ")
    f_opt.pack(fill="x", padx=6, pady=4)
    v_pdf, v_txt, v_docx = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
    row = ttk.Frame(f_opt)
    row.pack(fill="x", padx=8, pady=(6, 2))
    ttk.Checkbutton(row, text="PDF ค้นหาข้อความได้", variable=v_pdf).pack(side="left", padx=(0, 16))
    ttk.Checkbutton(row, text="ข้อความ (.txt)", variable=v_txt).pack(side="left", padx=(0, 16))
    ttk.Checkbutton(row, text="Word (.docx)", variable=v_docx).pack(side="left", padx=(0, 16))
    v_xlsx = tk.BooleanVar(value=True)
    ttk.Checkbutton(row, text="ตาราง → Excel (.xlsx)", variable=v_xlsx).pack(side="left")

    v_mode = tk.StringVar(value="beside")
    v_outdir = tk.StringVar()
    r1 = ttk.Frame(f_opt)
    r1.pack(fill="x", padx=8, pady=2)
    ttk.Radiobutton(r1, text="เก็บในโฟลเดอร์ OCR_output ข้างไฟล์ต้นฉบับ",
                    variable=v_mode, value="beside").pack(side="left")
    r2 = ttk.Frame(f_opt)
    r2.pack(fill="x", padx=8, pady=(2, 6))
    ttk.Radiobutton(r2, text="เก็บที่:", variable=v_mode, value="folder").pack(side="left")
    ttk.Entry(r2, textvariable=v_outdir).pack(side="left", fill="x", expand=True, padx=6)

    def pick_out():
        d = filedialog.askdirectory(title="เลือกโฟลเดอร์เก็บผลลัพธ์")
        if d:
            v_outdir.set(d)
            v_mode.set("folder")

    ttk.Button(r2, text="เลือก...", command=pick_out).pack(side="left")

    f_adv = ttk.LabelFrame(tab_run, text=" 3. ตั้งค่า ")
    f_adv.pack(fill="x", padx=6, pady=4)
    ra = ttk.Frame(f_adv)
    ra.pack(fill="x", padx=8, pady=6)
    ttk.Label(ra, text="ความละเอียด:").pack(side="left")
    v_dpi = tk.StringVar(value="200")
    ttk.Combobox(ra, textvariable=v_dpi, values=["200", "300"], width=5,
                 state="readonly").pack(side="left", padx=(4, 2))
    ttk.Label(ra, text="dpi (ตัวหนังสือเล็กมากใช้ 300)", foreground="#555").pack(side="left", padx=(0, 16))
    ncpu = os.cpu_count() or 2
    ttk.Label(ra, text="ใช้ CPU:").pack(side="left")
    v_workers = tk.IntVar(value=max(1, min(4, ncpu // 2)))
    ttk.Spinbox(ra, from_=1, to=max(1, ncpu), textvariable=v_workers, width=4).pack(side="left", padx=4)
    ttk.Label(ra, text="core", foreground="#555").pack(side="left")
    v_tables = tk.BooleanVar(value=True)
    ttk.Checkbutton(f_adv, variable=v_tables,
                    text="หาตาราง: ทำเป็นตารางใน Word/Excel และคัดลอกไปวางใน Excel แล้วแยกช่องได้"
                    ).pack(anchor="w", padx=8, pady=(0, 2))
    v_textlayer = tk.BooleanVar(value=True)
    ttk.Checkbutton(f_adv, variable=v_textlayer,
                    text="ถ้าหน้าใน PDF มีข้อความอยู่แล้ว (ไม่ใช่ภาพสแกน) ใช้ข้อความเดิม ไม่ต้อง OCR"
                    ).pack(anchor="w", padx=8, pady=(0, 6))

    f_run = ttk.Frame(tab_run)
    f_run.pack(fill="x", padx=6, pady=4)
    btn_start = ttk.Button(f_run, text="▶  เริ่มแปลง", width=16)
    btn_start.pack(side="left")
    btn_stop = ttk.Button(f_run, text="หยุด", width=8, state="disabled")
    btn_stop.pack(side="left", padx=6)
    btn_open = ttk.Button(f_run, text="เปิดโฟลเดอร์ผลลัพธ์", state="disabled")
    btn_open.pack(side="right")
    pbar = ttk.Progressbar(tab_run, mode="determinate")
    pbar.pack(fill="x", padx=6)
    lbl_status = ttk.Label(tab_run, text="พร้อม", foreground="#555")
    lbl_status.pack(anchor="w", padx=6, pady=(2, 0))
    logbox = tk.Text(tab_run, height=6, wrap="word", state="disabled", bg="#fafafa", relief="flat")
    logbox.pack(fill="both", expand=True, padx=6, pady=(4, 6))

    # ================================================================ แท็บ 2: ข้อความ
    bar = ttk.Frame(tab_txt)
    bar.pack(fill="x", padx=6, pady=(8, 4))
    btn_copy_all = ttk.Button(bar, text="คัดลอกทั้งหมด")
    btn_copy_all.pack(side="left")
    btn_paste = ttk.Button(bar, text=f"วางภาพหน้าจอ ({MOD_TXT}+V)")
    btn_paste.pack(side="left", padx=6)
    btn_save = ttk.Button(bar, text="บันทึกเป็น...")
    btn_save.pack(side="left")
    btn_edit_res = ttk.Button(bar, text="แก้ไขไฟล์นี้")
    btn_edit_res.pack(side="left", padx=6)
    btn_clear_res = ttk.Button(bar, text="ล้างรายการ")
    btn_clear_res.pack(side="right")
    lbl_txt_status = ttk.Label(tab_txt, foreground="#555",
                               text=f"แคปหน้าจอแล้วกด {MOD_TXT}+V เพื่ออ่านทันที  ·  "
                                    "แถบสีเหลือง = บรรทัดที่ควรตรวจ (ความมั่นใจต่ำกว่า 90%)")
    lbl_txt_status.pack(anchor="w", padx=8, pady=(0, 4))

    pw = ttk.PanedWindow(tab_txt, orient="horizontal")
    pw.pack(fill="both", expand=True, padx=6, pady=(0, 6))
    left = ttk.Frame(pw)
    right = ttk.Frame(pw)
    pw.add(left, weight=1)
    pw.add(right, weight=4)
    res_list = tk.Listbox(left, activestyle="none", exportselection=False)
    res_list.pack(fill="both", expand=True)
    txt = tk.Text(right, wrap="word", font=TEXT_FONT, undo=True, padx=8, pady=6)
    tsb = ttk.Scrollbar(right, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=tsb.set)
    tsb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)
    txt.tag_configure("low", background="#fff3b0")
    txt.tag_configure("pagehead", foreground="#888")

    def build_text(pages, with_heads=True):
        parts = []
        multi = len(pages) > 1
        for p in pages:
            body = (p.get("text") or "").strip()
            if p.get("error"):
                body = f"[อ่านหน้านี้ไม่ได้: {p['error']}]"
            parts.append((f"— หน้า {p['index'] + 1} —" if (multi and with_heads) else None, body))
        return parts

    def show_result(i):
        if i < 0 or i >= len(results):
            return
        r = results[i]
        txt.delete("1.0", "end")
        for head, body in build_text(r["pages"]):
            if head:
                txt.insert("end", head + "\n", "pagehead")
            txt.insert("end", body + "\n\n")
        # ไฮไลต์บรรทัดความมั่นใจต่ำ
        for p in r["pages"]:
            for ln in p.get("lines", []):
                if ln["confidence"] < LOW_CONF and ln["text"].strip():
                    start = "1.0"
                    while True:
                        pos = txt.search(ln["text"].strip(), start, stopindex="end")
                        if not pos:
                            break
                        end = f"{pos}+{len(ln['text'].strip())}c"
                        txt.tag_add("low", pos, end)
                        start = end
        txt.mark_set("insert", "1.0")
        txt.see("1.0")

    def add_result(name, path, pages, select=True):
        results.append({"name": name, "path": path, "pages": pages})
        res_list.insert("end", name)
        if select:
            res_list.selection_clear(0, "end")
            res_list.selection_set("end")
            res_list.see("end")
            show_result(len(results) - 1)

    def copy_text(s):
        root.clipboard_clear()
        root.clipboard_append(s)
        root.update()  # ให้ข้อความยังอยู่ในคลิปบอร์ดหลังปิดโปรแกรม

    def copy_all():
        s = txt.get("1.0", "end").strip()
        if not s:
            lbl_txt_status.config(text="ยังไม่มีข้อความให้คัดลอก")
            return
        copy_text(s)
        lbl_txt_status.config(text=f"✓ คัดลอกแล้ว {len(s):,} ตัวอักษร — นำไปวางที่อื่นได้เลย")

    def save_as():
        s = txt.get("1.0", "end").strip()
        if not s:
            return
        sel = res_list.curselection()
        base = Path(results[sel[0]]["name"]).stem if sel else "ocr"
        p = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=f"{base}.txt",
                                         filetypes=[("ข้อความ", "*.txt")])
        if p:
            Path(p).write_text(s + "\n", encoding="utf-8-sig")
            lbl_txt_status.config(text=f"✓ บันทึกแล้ว: {p}")

    def clear_results():
        results.clear()
        res_list.delete(0, "end")
        txt.delete("1.0", "end")

    res_list.bind("<<ListboxSelect>>", lambda e: res_list.curselection() and show_result(res_list.curselection()[0]))
    btn_copy_all.configure(command=copy_all)
    btn_save.configure(command=save_as)
    btn_clear_res.configure(command=clear_results)

    def edit_result():
        sel = res_list.curselection()
        if sel and results[sel[0]]["path"]:
            open_in_editor(results[sel[0]]["path"])
        else:
            lbl_txt_status.config(text="เลือกไฟล์ในรายการด้านซ้ายก่อน (ภาพหน้าจอแก้ไขไม่ได้)")
    btn_edit_res.configure(command=edit_result)

    # ---------------- วางจากคลิปบอร์ด
    def paste_clipboard(event=None):
        if event is not None and active_tab() != "ocr":
            return None  # อยู่ในแท็บแก้ไข/เครื่องมือ ไม่ต้อง OCR คลิปบอร์ด
        # ถ้าโฟกัสอยู่ในช่องพิมพ์ ให้ทำงานวางข้อความตามปกติ
        if event is not None and isinstance(event.widget, (tk.Entry, ttk.Entry)):
            return None
        try:
            from PIL import ImageGrab
            data = ImageGrab.grabclipboard()
        except Exception as e:  # noqa: BLE001
            lbl_txt_status.config(text=f"อ่านคลิปบอร์ดไม่ได้: {e}")
            return "break"
        if isinstance(data, list):  # คัดลอกไฟล์มาจาก Explorer/Finder
            n = add_paths(data)
            nb.select(tab_run)
            lbl_status.config(text=f"เพิ่มไฟล์จากคลิปบอร์ด {n} ไฟล์")
            return "break"
        if data is None:
            if event is None or event.widget is not txt:
                nb.select(tab_txt)
                lbl_txt_status.config(text=f"ในคลิปบอร์ดไม่มีรูปภาพ — แคปหน้าจอก่อน "
                                           f"({'Cmd+Ctrl+Shift+4' if IS_MAC else 'Win+Shift+S'}) แล้วกด {MOD_TXT}+V")
                return "break"
            return None  # วางข้อความในช่องข้อความตามปกติ
        if state["running"] or state["clip_busy"]:
            lbl_txt_status.config(text="กำลังประมวลผลอยู่ รอสักครู่แล้วลองใหม่")
            return "break"
        if not (ocr_core.model_dir() / "rec.onnx").exists():
            messagebox.showerror(APP, f"ไม่พบไฟล์โมเดลที่\n{ocr_core.model_dir()}")
            return "break"
        state["clip_busy"] = True
        nb.select(tab_txt)
        lbl_txt_status.config(text="กำลังอ่านภาพจากคลิปบอร์ด...")
        img = data.copy()

        def work():
            try:
                t0 = time.time()
                page = ocr_core.ocr_image(img)
                msgq.put(("clip", (page, time.time() - t0)))
            except Exception as e:  # noqa: BLE001
                msgq.put(("clip_err", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()
        return "break"

    btn_paste.configure(command=paste_clipboard)
    root.bind_all(f"<{MOD}-v>", paste_clipboard)
    root.bind_all(f"<{MOD}-V>", paste_clipboard)
    if sys.platform == "win32":
        # ตอนแป้นพิมพ์เป็นภาษาไทย Ctrl+V จะไม่ตรงกับ <Control-v> จึงดักด้วยรหัสปุ่ม V แทน
        def ctrl_key(event):
            if event.keycode == 86 and event.keysym.lower() != "v":
                if isinstance(event.widget, (tk.Text, tk.Entry, ttk.Entry)):
                    try:
                        event.widget.event_generate("<<Paste>>")
                    except tk.TclError:
                        pass
                return paste_clipboard(event)
            if event.keycode == 90 and event.keysym.lower() != "z":  # Ctrl+Z ตอนแป้นภาษาไทย
                return editor_key(event, "undo")
            return None
        root.bind_all("<Control-KeyPress>", ctrl_key, add="+")

    def editor_key(event, name):
        if active_tab() != "edit" or isinstance(event.widget, (tk.Text, tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox)):
            return None
        editor.key(name)
        return "break"
    root.bind_all(f"<{MOD}-z>", lambda e: editor_key(e, "undo"))
    root.bind_all(f"<{MOD}-Z>", lambda e: editor_key(e, "undo"))
    root.bind_all("<Delete>", lambda e: editor_key(e, "delete"))
    for _k in ("Left", "Right", "Up", "Down"):
        root.bind_all(f"<{_k}>", lambda e, k=_k.lower(): editor_key(e, k))
        root.bind_all(f"<Shift-{_k}>", lambda e, k=_k.lower(): editor_key(e, k + "10"))
    root.bind_all("<BackSpace>", lambda e: editor_key(e, "delete") if IS_MAC else None)

    # ---------------- ลากไฟล์มาวาง
    if HAS_DND:
        def on_drop(event):
            paths = root.tk.splitlist(event.data)
            if active_tab() == "edit":
                editor.open_path(paths[0])
                return
            n = add_paths(paths)
            nb.select(tab_run)
            lbl_status.config(text=f"เพิ่ม {n} ไฟล์" if n else "ไม่มีไฟล์ที่รองรับ (PDF/รูปภาพ)")
        for w in (root, lst, f_in, txt, res_list):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", on_drop)
            except Exception:  # noqa: BLE001
                pass

    # ---------------- คิวข้อความจาก thread
    def log(msg):
        msgq.put(("log", msg))

    def poll():
        try:
            while True:
                kind, val = msgq.get_nowait()
                if kind == "log":
                    logbox.configure(state="normal")
                    logbox.insert("end", val + "\n")
                    logbox.see("end")
                    logbox.configure(state="disabled")
                elif kind == "progress":
                    done, total = val
                    pbar.configure(maximum=total, value=done)
                    lbl_status.config(text=f"ประมวลผลแล้ว {done}/{total} หน้า")
                elif kind == "result":
                    src, pages = val
                    add_result(src.name, str(src), pages, select=len(results) == 0)
                elif kind == "clip":
                    page, sec = val
                    state["clip_busy"] = False
                    name = f"ภาพหน้าจอ {time.strftime('%H:%M:%S')}"
                    add_result(name, None, [page])
                    s = (page.get("text") or "").strip()
                    if s:
                        copy_text(s)
                        low = sum(ln["confidence"] < LOW_CONF for ln in page["lines"])
                        extra = f"  ·  ควรตรวจ {low} บรรทัด (แถบเหลือง)" if low else ""
                        lbl_txt_status.config(
                            text=f"✓ อ่านเสร็จใน {sec:.1f} วินาที และคัดลอกให้แล้ว — วางที่อื่นได้เลย{extra}")
                    else:
                        lbl_txt_status.config(text="ไม่พบตัวหนังสือในภาพ")
                elif kind == "clip_err":
                    state["clip_busy"] = False
                    lbl_txt_status.config(text=f"อ่านภาพไม่สำเร็จ: {val}")
                elif kind == "done":
                    finish(val)
        except queue.Empty:
            pass
        root.after(100, poll)

    def set_running(on):
        state["running"] = on
        btn_start.configure(state="disabled" if on else "normal")
        btn_stop.configure(state="normal" if on else "disabled")

    def finish(result):
        set_running(False)
        status, outputs, n_files = result
        dirs = {str(Path(o).parent) for o in outputs}
        state["last_dirs"] = dirs
        btn_open.configure(state="normal" if dirs else "disabled")
        if status == "ok":
            msg = f"เสร็จแล้ว — อ่าน {n_files} ไฟล์"
            msg += f", บันทึก {len(outputs)} ไฟล์" if outputs else " (ดูข้อความได้ที่แท็บ \"ข้อความ\")"
            lbl_status.config(text=msg)
            log("— เสร็จสิ้น —")
            if results:
                nb.select(tab_txt)
        elif status == "cancel":
            lbl_status.config(text="หยุดแล้ว")
            log("— ผู้ใช้สั่งหยุด (ไฟล์ที่เสร็จก่อนหน้านี้บันทึกไว้แล้ว) —")
        else:
            lbl_status.config(text="เกิดข้อผิดพลาด ดูรายละเอียดด้านล่าง")

    def open_out():
        for d in sorted(state["last_dirs"])[:3]:
            if sys.platform == "win32":
                os.startfile(d)  # noqa: S606
            elif IS_MAC:
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])

    def start():
        if not files:
            messagebox.showwarning(APP, "กรุณาเลือกไฟล์ก่อน")
            return
        if state["clip_busy"]:
            messagebox.showinfo(APP, "กำลังอ่านภาพจากคลิปบอร์ดอยู่ รอสักครู่")
            return
        formats = {k for k, v in (("pdf", v_pdf), ("txt", v_txt), ("docx", v_docx), ("xlsx", v_xlsx)) if v.get()}
        out_dir = None
        if formats and v_mode.get() == "folder":
            if not v_outdir.get().strip():
                messagebox.showwarning(APP, "กรุณาเลือกโฟลเดอร์เก็บผลลัพธ์")
                return
            out_dir = Path(v_outdir.get().strip())
        if not (ocr_core.model_dir() / "rec.onnx").exists():
            messagebox.showerror(APP, f"ไม่พบไฟล์โมเดลที่\n{ocr_core.model_dir()}")
            return
        try:
            workers = max(1, int(v_workers.get()))
        except (tk.TclError, ValueError):
            workers = 1
        state["stop"] = False
        set_running(True)
        logbox.configure(state="normal")
        logbox.delete("1.0", "end")
        logbox.configure(state="disabled")
        lbl_status.config(text="กำลังโหลดโมเดล...")
        if not formats:
            log("ไม่ได้เลือกรูปแบบไฟล์ — อ่านอย่างเดียว ดูผลได้ที่แท็บ \"ข้อความ\"")
        batch = list(files)
        args = dict(files=batch, out_mode=v_mode.get(), out_dir=out_dir, formats=formats,
                    dpi=int(v_dpi.get()), use_text_layer=v_textlayer.get(), workers=workers,
                    find_tables=v_tables.get())

        def job():
            outputs = []
            try:
                outputs = ocr_core.run_batch(
                    **args, log=log,
                    progress=lambda d, t: msgq.put(("progress", (d, t))),
                    stop_flag=lambda: state["stop"],
                    on_result=lambda src, pages: msgq.put(("result", (src, pages))))
                msgq.put(("done", ("ok", outputs, len(batch))))
            except ocr_core.Cancelled:
                msgq.put(("done", ("cancel", outputs, len(batch))))
            except Exception as e:  # noqa: BLE001
                log(f"✗ ผิดพลาด: {type(e).__name__}: {e}")
                log(traceback.format_exc())
                msgq.put(("done", ("error", outputs, len(batch))))

        threading.Thread(target=job, daemon=True).start()

    def stop():
        state["stop"] = True
        lbl_status.config(text="กำลังหยุด... (รอหน้าที่กำลังทำอยู่ให้เสร็จ)")

    def on_close():
        if state["running"] and not messagebox.askyesno(APP, "กำลังประมวลผลอยู่ ต้องการปิดโปรแกรมหรือไม่?"):
            return
        if editor.dirty and not messagebox.askyesno(APP, "มีการแก้ไข PDF ที่ยังไม่ได้บันทึก ต้องการปิดโปรแกรมหรือไม่?"):
            return
        state["stop"] = True
        root.destroy()
        os._exit(0)  # ปิด worker process ที่ค้างอยู่ทั้งหมด

    btn_start.configure(command=start)
    btn_stop.configure(command=stop)
    btn_open.configure(command=open_out)
    root.protocol("WM_DELETE_WINDOW", on_close)

    # ไฟล์ที่ลากมาวางบนไอคอนโปรแกรม
    if len(sys.argv) > 1:
        add_paths(sys.argv[1:])

    log(f"{APP} v{VERSION} — พัฒนาโดย {AUTHOR}")
    log("โปรแกรมทำงานออฟไลน์ทั้งหมด เอกสารไม่ถูกส่งออกนอกเครื่อง")
    log(f"เคล็ดลับ: แคปหน้าจอแล้วกด {MOD_TXT}+V เพื่ออ่านข้อความทันที"
        + ("  ·  ลากไฟล์มาวางในหน้าต่างได้" if HAS_DND else ""))
    root.after(100, poll)
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()  # จำเป็นสำหรับ exe ที่ใช้หลาย process
    main()

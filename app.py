# -*- coding: utf-8 -*-
"""
OpenThai OCR - โปรแกรมแปลง PDF/รูปสแกน เป็นข้อความภาษาไทย (ออฟไลน์)
ใช้โมเดล iapp/OpenThai2.0-OCR-Text (Apache-2.0)
ลากไฟล์ไปวางบนไอคอน OpenThaiOCR.exe เพื่อเปิดพร้อมไฟล์ได้
"""
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    import ocr_core

    APP = "OpenThai OCR"
    VERSION = "1.1"
    AUTHOR = "arthit.f"
    UI_FONT = ("Tahoma", 10)

    root = tk.Tk()
    root.title(f"{APP} — แปลงเอกสารสแกนเป็นข้อความ (ออฟไลน์)")
    root.geometry("760x640")
    root.minsize(640, 560)
    root.option_add("*Font", UI_FONT)
    style = ttk.Style()
    for w in ("TLabel", "TButton", "TCheckbutton", "TRadiobutton", "TLabelframe.Label", "TCombobox"):
        style.configure(w, font=UI_FONT)

    files: list[Path] = []
    msgq: queue.Queue = queue.Queue()
    state = {"running": False, "stop": False, "last_dirs": set()}

    # ---------------- ไฟล์ต้นฉบับ
    f_in = ttk.LabelFrame(root, text=" 1. เลือกไฟล์ (PDF, JPG, PNG, TIFF) ")
    f_in.pack(fill="both", expand=True, padx=10, pady=(10, 5))
    lst = tk.Listbox(f_in, height=7, selectmode="extended", activestyle="none")
    sb = ttk.Scrollbar(f_in, orient="vertical", command=lst.yview)
    lst.configure(yscrollcommand=sb.set)
    lst.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
    sb.grid(row=0, column=1, sticky="ns", pady=8)
    btns = ttk.Frame(f_in)
    btns.grid(row=0, column=2, sticky="n", padx=8, pady=8)
    f_in.columnconfigure(0, weight=1)
    f_in.rowconfigure(0, weight=1)
    lbl_count = ttk.Label(f_in, text="ยังไม่ได้เลือกไฟล์", foreground="#555")
    lbl_count.grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

    def refresh_list():
        lst.delete(0, "end")
        for f in files:
            lst.insert("end", str(f))
        lbl_count.config(text=f"{len(files)} ไฟล์" if files else "ยังไม่ได้เลือกไฟล์")

    def add_paths(paths):
        new = ocr_core.collect_inputs(paths)
        have = {str(f).lower() for f in files}
        files.extend(f for f in new if str(f).lower() not in have)
        refresh_list()

    def pick_files():
        ps = filedialog.askopenfilenames(
            title="เลือกไฟล์",
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

    # ---------------- ตัวเลือก
    f_opt = ttk.LabelFrame(root, text=" 2. ผลลัพธ์ ")
    f_opt.pack(fill="x", padx=10, pady=5)
    v_pdf, v_txt, v_docx = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
    row = ttk.Frame(f_opt)
    row.pack(fill="x", padx=8, pady=(6, 2))
    ttk.Checkbutton(row, text="PDF ค้นหาข้อความได้", variable=v_pdf).pack(side="left", padx=(0, 16))
    ttk.Checkbutton(row, text="ข้อความ (.txt)", variable=v_txt).pack(side="left", padx=(0, 16))
    ttk.Checkbutton(row, text="Word (.docx)", variable=v_docx).pack(side="left")

    v_mode = tk.StringVar(value="beside")
    v_outdir = tk.StringVar()
    r1 = ttk.Frame(f_opt)
    r1.pack(fill="x", padx=8, pady=2)
    ttk.Radiobutton(r1, text="เก็บในโฟลเดอร์ OCR_output ข้างไฟล์ต้นฉบับ",
                    variable=v_mode, value="beside").pack(side="left")
    r2 = ttk.Frame(f_opt)
    r2.pack(fill="x", padx=8, pady=(2, 6))
    ttk.Radiobutton(r2, text="เก็บที่:", variable=v_mode, value="folder").pack(side="left")
    ent = ttk.Entry(r2, textvariable=v_outdir)
    ent.pack(side="left", fill="x", expand=True, padx=6)

    def pick_out():
        d = filedialog.askdirectory(title="เลือกโฟลเดอร์เก็บผลลัพธ์")
        if d:
            v_outdir.set(d)
            v_mode.set("folder")

    ttk.Button(r2, text="เลือก...", command=pick_out).pack(side="left")

    f_adv = ttk.LabelFrame(root, text=" 3. ตั้งค่า ")
    f_adv.pack(fill="x", padx=10, pady=5)
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
    v_textlayer = tk.BooleanVar(value=True)
    ttk.Checkbutton(f_adv, variable=v_textlayer,
                    text="ถ้าหน้าใน PDF มีข้อความอยู่แล้ว (ไม่ใช่ภาพสแกน) ใช้ข้อความเดิม ไม่ต้อง OCR"
                    ).pack(anchor="w", padx=8, pady=(0, 6))

    # ---------------- เริ่มงาน + บันทึก
    f_run = ttk.Frame(root)
    f_run.pack(fill="x", padx=10, pady=5)
    btn_start = ttk.Button(f_run, text="▶  เริ่มแปลง", width=16)
    btn_start.pack(side="left")
    btn_stop = ttk.Button(f_run, text="หยุด", width=8, state="disabled")
    btn_stop.pack(side="left", padx=6)
    btn_open = ttk.Button(f_run, text="เปิดโฟลเดอร์ผลลัพธ์", state="disabled")
    btn_open.pack(side="right")
    pbar = ttk.Progressbar(root, mode="determinate")
    pbar.pack(fill="x", padx=10)
    lbl_status = ttk.Label(root, text="พร้อม", foreground="#555")
    lbl_status.pack(anchor="w", padx=10, pady=(2, 0))
    footer = ttk.Frame(root)
    footer.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
    ttk.Label(footer, text=f"พัฒนาโดย {AUTHOR}", foreground="#777").pack(side="left")
    ttk.Label(footer, text=f"{APP} v{VERSION}  ·  โมเดล OCR: iApp Technology",
              foreground="#999").pack(side="right")
    logbox = tk.Text(root, height=8, wrap="word", state="disabled", bg="#fafafa", relief="flat")
    logbox.pack(fill="both", expand=True, padx=10, pady=(4, 6))

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
        status, outputs = result
        dirs = {str(Path(o).parent) for o in outputs}
        state["last_dirs"] = dirs
        btn_open.configure(state="normal" if dirs else "disabled")
        if status == "ok":
            lbl_status.config(text=f"เสร็จแล้ว — ได้ไฟล์ผลลัพธ์ {len(outputs)} ไฟล์")
            log("— เสร็จสิ้น —")
        elif status == "cancel":
            lbl_status.config(text="หยุดแล้ว")
            log("— ผู้ใช้สั่งหยุด (ไฟล์ที่เสร็จก่อนหน้านี้บันทึกไว้แล้ว) —")
        else:
            lbl_status.config(text="เกิดข้อผิดพลาด ดูรายละเอียดด้านล่าง")

    def open_out():
        for d in sorted(state["last_dirs"])[:3]:
            if sys.platform == "win32":
                os.startfile(d)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])

    def start():
        if not files:
            messagebox.showwarning(APP, "กรุณาเลือกไฟล์ก่อน")
            return
        formats = {k for k, v in (("pdf", v_pdf), ("txt", v_txt), ("docx", v_docx)) if v.get()}
        if not formats:
            messagebox.showwarning(APP, "กรุณาเลือกรูปแบบผลลัพธ์อย่างน้อย 1 แบบ")
            return
        out_dir = None
        if v_mode.get() == "folder":
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
        args = dict(files=list(files), out_mode=v_mode.get(), out_dir=out_dir, formats=formats,
                    dpi=int(v_dpi.get()), use_text_layer=v_textlayer.get(), workers=workers)

        def job():
            outputs = []
            try:
                outputs = ocr_core.run_batch(
                    **args, log=log,
                    progress=lambda d, t: msgq.put(("progress", (d, t))),
                    stop_flag=lambda: state["stop"])
                msgq.put(("done", ("ok", outputs)))
            except ocr_core.Cancelled:
                msgq.put(("done", ("cancel", outputs)))
            except Exception as e:  # noqa: BLE001
                log(f"✗ ผิดพลาด: {type(e).__name__}: {e}")
                log(traceback.format_exc())
                msgq.put(("done", ("error", outputs)))

        threading.Thread(target=job, daemon=True).start()

    def stop():
        state["stop"] = True
        lbl_status.config(text="กำลังหยุด... (รอหน้าที่กำลังทำอยู่ให้เสร็จ)")

    def on_close():
        if state["running"] and not messagebox.askyesno(APP, "กำลังประมวลผลอยู่ ต้องการปิดโปรแกรมหรือไม่?"):
            return
        state["stop"] = True
        root.destroy()
        os._exit(0)  # ปิด worker process ที่ค้างอยู่ทั้งหมด

    btn_start.configure(command=start)
    btn_stop.configure(command=stop)
    btn_open.configure(command=open_out)
    root.protocol("WM_DELETE_WINDOW", on_close)

    # ไฟล์ที่ลากมาวางบนไอคอน exe
    if len(sys.argv) > 1:
        add_paths(sys.argv[1:])

    log(f"{APP} v{VERSION} — พัฒนาโดย {AUTHOR}")
    log("โปรแกรมทำงานออฟไลน์ทั้งหมด เอกสารไม่ถูกส่งออกนอกเครื่อง")
    log("โมเดล: iapp/OpenThai2.0-OCR-Text (Apache-2.0) อ่านได้เฉพาะตัวพิมพ์ไทย/อังกฤษ")
    root.after(100, poll)
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()  # จำเป็นสำหรับ exe ที่ใช้หลาย process
    main()

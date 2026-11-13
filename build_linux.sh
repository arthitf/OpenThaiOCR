#!/bin/bash
# สร้างโปรแกรม OpenThai OCR สำหรับ Linux (x86_64)
# วิธีใช้: เปิด Terminal ในโฟลเดอร์นี้ แล้วพิมพ์   bash build_linux.sh
# ต้องมี Python 3.10+ ที่มี Tkinter (Ubuntu/Debian: sudo apt install python3-venv python3-tk)
set -euo pipefail
cd "$(dirname "$0")"

echo "=========================================================="
echo "  สร้างโปรแกรม OpenThai OCR สำหรับ Linux"
echo "=========================================================="

# ---- 1) หา Python ที่มี Tkinter
PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import tkinter" >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "[ผิดพลาด] ไม่พบ Python ที่มี Tkinter"
  echo "  Ubuntu/Debian: sudo apt install python3-venv python3-tk"
  echo "  Fedora:        sudo dnf install python3-tkinter"
  exit 1
fi
echo "[1/4] ใช้ $($PY --version) ($(uname -m))"

# ---- 2) virtual environment
[ -x .venv/bin/python ] || "$PY" -m venv .venv
VPY=.venv/bin/python
echo "[2/4] ติดตั้งไลบรารี..."
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -r requirements.txt --quiet

# ---- 3) โมเดล
echo "[3/4] โหลดโมเดล iapp/OpenThai2.0-OCR-Text..."
if [ ! -f model/rec.onnx ]; then
  "$VPY" -c "from huggingface_hub import snapshot_download as s; s('iapp/OpenThai2.0-OCR-Text', local_dir='model', allow_patterns=['*.onnx','*.yml','config.json','rec_dict.txt','runtime/*','NOTICE','README.md','SHA256SUMS'])"
fi
[ -f model/runtime/line_engine.py ] || { echo "[ผิดพลาด] โหลดโมเดลไม่ครบ ลบโฟลเดอร์ model แล้วรันใหม่"; exit 1; }

# ---- 4) สร้างโปรแกรม
echo "[4/4] กำลังสร้างโปรแกรม..."
"$VPY" -m PyInstaller --noconfirm --clean --onedir --windowed \
  --name OpenThaiOCR \
  --paths model \
  --hidden-import runtime.line_engine \
  --hidden-import runtime.thai_norm \
  --hidden-import onnxruntime \
  --hidden-import thai_tables --hidden-import pdf_ops --hidden-import pdf_editor \
  --hidden-import PIL._tkinter_finder \
  --collect-data docx \
  --collect-all tkinterdnd2 \
  --exclude-module openvino --exclude-module torch --exclude-module matplotlib \
  --exclude-module pandas --exclude-module scipy --exclude-module IPython \
  --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide6 \
  --add-data "model/det.onnx:model" \
  --add-data "model/det.yml:model" \
  --add-data "model/rec.onnx:model" \
  --add-data "model/rec_dict.txt:model" \
  --add-data "model/config.json:model" \
  --add-data "model/NOTICE:model" \
  app.py

cp README_TH.txt dist/OpenThaiOCR/ 2>/dev/null || true

# ---- ตัวติดตั้ง: ใส่เมนูโปรแกรม + คำสั่ง openthaiocr (ติดตั้งเฉพาะผู้ใช้ ไม่ต้อง sudo)
cat > dist/OpenThaiOCR/install.sh <<'INSTALL'
#!/bin/bash
# ติดตั้ง OpenThai OCR ให้ผู้ใช้คนนี้ (ไม่ต้องใช้ sudo)
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/OpenThaiOCR"
if [ "$SRC" != "$DEST" ]; then
  rm -rf "$DEST"; mkdir -p "$DEST"; cp -a "$SRC/." "$DEST/"
fi
chmod +x "$DEST/OpenThaiOCR"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$DEST/OpenThaiOCR" "$HOME/.local/bin/openthaiocr"
cat > "$HOME/.local/share/applications/openthaiocr.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=OpenThai OCR
Comment=อ่านข้อความภาษาไทยจากภาพและ PDF · แก้ไข PDF (by arthit.f)
Exec="$DEST/OpenThaiOCR" %F
Terminal=false
Categories=Office;Graphics;
MimeType=application/pdf;image/png;image/jpeg;image/tiff;
EOF
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
echo "ติดตั้งเสร็จแล้ว — หา 'OpenThai OCR' ในเมนูโปรแกรม หรือพิมพ์ openthaiocr ใน Terminal"
command -v xclip >/dev/null 2>&1 || command -v wl-paste >/dev/null 2>&1 || \
  echo "หมายเหตุ: ถ้าจะใช้ 'วางภาพหน้าจอ' ให้ติดตั้ง xclip (sudo apt install xclip) หรือ wl-clipboard"
fc-list :lang=th 2>/dev/null | grep -q . || \
  echo "หมายเหตุ: ไม่พบฟอนต์ไทยในเครื่อง แนะนำ sudo apt install fonts-thai-tlwg"
INSTALL

cat > dist/OpenThaiOCR/uninstall.sh <<'UNINSTALL'
#!/bin/bash
rm -rf "$HOME/.local/share/OpenThaiOCR" "$HOME/.local/bin/openthaiocr" "$HOME/.local/share/applications/openthaiocr.desktop"
echo "ลบ OpenThai OCR แล้ว"
UNINSTALL
chmod +x dist/OpenThaiOCR/install.sh dist/OpenThaiOCR/uninstall.sh

rm -f dist/OpenThaiOCR_linux_x64.tar.gz
tar -C dist -czf dist/OpenThaiOCR_linux_x64.tar.gz OpenThaiOCR

echo
echo "ขนาดโปรแกรม: $(du -sh dist/OpenThaiOCR | cut -f1)   tar.gz: $(du -sh dist/OpenThaiOCR_linux_x64.tar.gz | cut -f1)"
echo "=========================================================="
echo "  เสร็จแล้ว!  ไฟล์แจก: dist/OpenThaiOCR_linux_x64.tar.gz"
echo "  ผู้ใช้: แตกไฟล์ แล้วรัน  bash OpenThaiOCR/install.sh"
echo "=========================================================="

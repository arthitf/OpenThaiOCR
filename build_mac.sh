#!/bin/bash
# สร้างโปรแกรม OpenThai OCR สำหรับ Mac ชิป M (Apple Silicon)
# วิธีใช้: เปิด Terminal ในโฟลเดอร์นี้ แล้วพิมพ์   bash build_mac.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "=========================================================="
echo "  สร้างโปรแกรม OpenThai OCR (.app) สำหรับ Mac ชิป M"
echo "=========================================================="

# ---- 1) หา Python ที่มี Tkinter (แนะนำ Python 3.12 จาก python.org)
PY=""
for c in python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import tkinter" >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "[ผิดพลาด] ไม่พบ Python ที่มี Tkinter"
  echo "  ติดตั้ง Python 3.12 จาก https://www.python.org/downloads/macos/"
  echo "  (ถ้าใช้ Homebrew: brew install python-tk@3.12)"
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

# ---- 4) สร้าง .app
echo "[4/4] กำลังสร้าง .app..."
"$VPY" -m PyInstaller --noconfirm --clean --onedir --windowed \
  --name OpenThaiOCR \
  --osx-bundle-identifier com.arthitf.openthaiocr \
  --paths model \
  --hidden-import runtime.line_engine \
  --hidden-import runtime.thai_norm \
  --hidden-import onnxruntime \
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

# zip สำหรับแจก (ใช้ ditto เพื่อให้ .app ไม่เสีย)
rm -f dist/OpenThaiOCR_mac.zip
ditto -c -k --keepParent dist/OpenThaiOCR.app dist/OpenThaiOCR_mac.zip
cp README_TH.txt dist/ 2>/dev/null || true

echo
echo "ขนาด .app: $(du -sh dist/OpenThaiOCR.app | cut -f1)   ขนาด zip: $(du -sh dist/OpenThaiOCR_mac.zip | cut -f1)"
echo "=========================================================="
echo "  เสร็จแล้ว!  โปรแกรม: dist/OpenThaiOCR.app"
echo "  ไฟล์สำหรับแจก:   dist/OpenThaiOCR_mac.zip"
echo "=========================================================="
[ -n "${CI:-}" ] || open dist

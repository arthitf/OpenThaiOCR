@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Build OpenThai OCR

echo ==========================================================
echo   สร้างโปรแกรม OpenThai OCR (.exe) สำหรับ Windows
echo   ต้องต่ออินเทอร์เน็ตครั้งแรกเพื่อโหลดไลบรารีและโมเดล
echo ==========================================================
echo.

rem ---- 1) หา Python (แนะนำ 3.12)
set "PY="
py -3.12 -c "1" >nul 2>&1 && set "PY=py -3.12"
if not defined PY py -3.11 -c "1" >nul 2>&1 && set "PY=py -3.11"
if not defined PY py -3 -c "1" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "1" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo [ผิดพลาด] ไม่พบ Python  กรุณาติดตั้ง Python 3.12 จาก https://www.python.org/downloads/
  echo           ตอนติดตั้งให้ติ๊ก "Add python.exe to PATH"
  goto :fail
)
echo [1/4] ใช้ Python:
%PY% --version

rem ---- 2) สร้าง virtual environment แยก ไม่ยุ่งกับ Python หลักของเครื่อง
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || goto :fail
)
set "VPY=.venv\Scripts\python.exe"
echo.
echo [2/4] ติดตั้งไลบรารี (ครั้งแรกใช้เวลาหลายนาที)...
"%VPY%" -m pip install --upgrade pip --quiet || goto :fail
"%VPY%" -m pip install -r requirements.txt --quiet || goto :fail

rem ---- 3) โหลดโมเดลจาก Hugging Face (ประมาณ 5 MB)
echo.
echo [3/4] โหลดโมเดล iapp/OpenThai2.0-OCR-Text...
if not exist "model\rec.onnx" (
  "%VPY%" -c "from huggingface_hub import snapshot_download as s; s('iapp/OpenThai2.0-OCR-Text', local_dir='model', allow_patterns=['*.onnx','*.yml','config.json','rec_dict.txt','runtime/*','NOTICE','README.md','SHA256SUMS'])" || goto :fail
) else (
  echo       มีโมเดลอยู่แล้ว ข้ามขั้นตอนนี้ ^(ลบโฟลเดอร์ model เพื่อโหลดใหม่^)
)
if not exist "model\runtime\line_engine.py" (
  echo [ผิดพลาด] โหลดโมเดลไม่ครบ ลองลบโฟลเดอร์ model แล้วรันใหม่
  goto :fail
)

rem ---- 4) สร้าง exe (แบบโฟลเดอร์ เปิดเร็ว และ antivirus เตือนน้อยกว่าแบบไฟล์เดียว)
echo.
echo [4/4] กำลังสร้าง exe...
"%VPY%" -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name OpenThaiOCR ^
  --version-file version_info.txt ^
  --paths model ^
  --hidden-import runtime.line_engine ^
  --hidden-import runtime.thai_norm ^
  --hidden-import onnxruntime ^
  --collect-data docx ^
  --collect-all tkinterdnd2 ^
  --exclude-module openvino ^
  --exclude-module torch ^
  --exclude-module matplotlib ^
  --exclude-module pandas ^
  --exclude-module scipy ^
  --exclude-module IPython ^
  --exclude-module PyQt5 ^
  --exclude-module PyQt6 ^
  --exclude-module PySide6 ^
  --add-data "model\det.onnx;model" ^
  --add-data "model\det.yml;model" ^
  --add-data "model\rec.onnx;model" ^
  --add-data "model\rec_dict.txt;model" ^
  --add-data "model\config.json;model" ^
  --add-data "model\NOTICE;model" ^
  app.py || goto :fail

copy /y "README_TH.txt" "dist\OpenThaiOCR\" >nul 2>&1

rem ---- ลดขนาด: ตัดส่วนอ่านวิดีโอของ OpenCV ที่โปรแกรมนี้ไม่ใช้
del /q "dist\OpenThaiOCR\_internal\cv2\opencv_videoio_ffmpeg*.dll" >nul 2>&1

echo.
echo ---- ไฟล์ที่ใหญ่ที่สุด 12 อันดับ ----
powershell -NoProfile -Command "Get-ChildItem 'dist\OpenThaiOCR' -Recurse -File | Sort-Object Length -Descending | Select-Object -First 12 @{n='MB';e={[math]::Round($_.Length/1MB,1)}},Name | Format-Table -AutoSize"
powershell -NoProfile -Command "$s=(Get-ChildItem 'dist\OpenThaiOCR' -Recurse -File | Measure-Object Length -Sum).Sum/1MB; 'Folder size: {0:N0} MB' -f $s"

echo.
echo ---- สร้างไฟล์ zip สำหรับแจก ----
powershell -NoProfile -Command "Compress-Archive -Path 'dist\OpenThaiOCR' -DestinationPath 'dist\OpenThaiOCR_win.zip' -Force; 'Zip size: {0:N0} MB' -f ((Get-Item 'dist\OpenThaiOCR_win.zip').Length/1MB)"

echo.
echo ---- สร้างตัวติดตั้ง (Setup.exe) ----
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo   ไม่พบ Inno Setup ในเครื่อง  ข้ามขั้นตอนนี้
  echo   ถ้าต้องการไฟล์ติดตั้ง: ติดตั้ง Inno Setup 6 จาก https://jrsoftware.org/isdl.php แล้วรัน build.bat ใหม่
  goto :done
)
"%ISCC%" /Q setup.iss || goto :fail
echo   ได้ตัวติดตั้งแล้ว: dist\OpenThaiOCR_Setup_v*.exe

:done
echo.
echo ==========================================================
echo   เสร็จแล้ว!  โปรแกรมอยู่ที่  dist\OpenThaiOCR\OpenThaiOCR.exe
echo   ไฟล์ zip สำหรับแจก:        dist\OpenThaiOCR_win.zip
if defined ISCC echo   ตัวติดตั้งสำหรับแจก:       dist\OpenThaiOCR_Setup_v*.exe
echo ==========================================================
if defined CI exit /b 0
explorer "dist"
pause
exit /b 0

:fail
echo.
echo *** สร้างไม่สำเร็จ ดูข้อความผิดพลาดด้านบน ***
if defined CI exit /b 1
pause
exit /b 1

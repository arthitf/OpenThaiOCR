# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('model/det.onnx', 'model'), ('model/det.yml', 'model'), ('model/rec.onnx', 'model'), ('model/rec_dict.txt', 'model'), ('model/config.json', 'model'), ('model/NOTICE', 'model')]
datas += collect_data_files('docx')


a = Analysis(
    ['app.py'],
    pathex=['model'],
    binaries=[],
    datas=datas,
    hiddenimports=['runtime.line_engine', 'runtime.thai_norm', 'onnxruntime'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['openvino', 'torch', 'matplotlib', 'pandas', 'scipy', 'IPython', 'PyQt5', 'PyQt6', 'PySide6'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OpenThaiOCR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OpenThaiOCR',
)

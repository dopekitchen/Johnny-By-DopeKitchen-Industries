# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/setup/Downloads/Jarvis By Dopekitchen Enterprises/assets', 'assets')]
binaries = []
hiddenimports = ['win32com.client', 'pythoncom', 'pynput.keyboard._win32', 'pynput.mouse._win32', 'pystray._win32', 'ctranslate2']
datas += collect_data_files('faster_whisper')
datas += collect_data_files('segments')
binaries += collect_dynamic_libs('ctranslate2')
binaries += collect_dynamic_libs('onnxruntime')
hiddenimports += collect_submodules('comtypes')
tmp_ret = collect_all('pyttsx3')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('kokoro_onnx')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('espeakng_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('phonemizer')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('language_tags')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['../installer/launcher.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'transformers', 'cv2', 'scipy', 'matplotlib', 'pandas'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Johnny',
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
    icon=['C:/Users/setup/Downloads/Jarvis By Dopekitchen Enterprises/assets/johnny.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Johnny',
)

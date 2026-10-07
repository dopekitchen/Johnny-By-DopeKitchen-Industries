# Builds a standalone Johnny.exe (PyInstaller) and, if Inno Setup is installed, JohnnySetup.exe.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File installer\build_exe.ps1
# Accepts an optional -Version flag (e.g. "1.2.3") injected by the CI release workflow.
# Uses a clean build venv so unrelated packages in your global Python (torch, etc.) are never bundled.
param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$venv = Join-Path $Root "build\venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "Creating clean build environment..." -ForegroundColor Cyan
    py -3.12 -m venv $venv
    if ($LASTEXITCODE -ne 0) { python -m venv $venv }
}
$py = "$venv\Scripts\python.exe"
& $py -m pip install -q --disable-pip-version-check pyinstaller -r requirements.txt faster-whisper sounddevice numpy kokoro-onnx
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
& $py installer\make_assets.py

& $py -m PyInstaller --noconfirm --clean --windowed --name Johnny `
    --distpath dist --workpath build\pyinstaller --specpath build `
    --icon "$Root\assets\johnny.ico" `
    --add-data "$Root\assets;assets" `
    --collect-all pyttsx3 --collect-submodules comtypes --hidden-import win32com.client --hidden-import pythoncom `
    --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 `
    --hidden-import pystray._win32 `
    --collect-data faster_whisper --collect-binaries ctranslate2 --hidden-import ctranslate2 `
    --collect-all kokoro_onnx --collect-all espeakng_loader --collect-all phonemizer --collect-all language_tags `
    --collect-data segments --collect-binaries onnxruntime `
    --exclude-module torch --exclude-module transformers --exclude-module cv2 --exclude-module scipy `
    --exclude-module matplotlib --exclude-module pandas `
    installer\launcher.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built dist\Johnny\Johnny.exe" -ForegroundColor Green

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    if ($Version -ne "") {
        & $iscc /Q /DAppVersion=$Version installer\johnny.iss
    } else {
        & $iscc /Q installer\johnny.iss
    }
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Built dist\JohnnySetup.exe" -ForegroundColor Green
} else {
    Write-Host "Inno Setup not found - install it (winget install JRSoftware.InnoSetup) to produce JohnnySetup.exe." -ForegroundColor Yellow
}

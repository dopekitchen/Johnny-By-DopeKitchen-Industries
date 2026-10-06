# Johnny by DopeKitchen Industries - installer
# Copies Johnny to %LOCALAPPDATA%\Programs\Johnny, builds a private Python environment,
# makes sure Ollama is present, then launches the graphical setup wizard.
$ErrorActionPreference = "Stop"
$Src = Split-Path -Parent $PSScriptRoot
$Dest = Join-Path $env:LOCALAPPDATA "Programs\Johnny"

function Say($msg, $color = "Cyan") { Write-Host "  $msg" -ForegroundColor $color }

Clear-Host
Write-Host ""
Write-Host "     ======================================" -ForegroundColor Cyan
Write-Host "       J O H N N Y   -   AI Butler Setup" -ForegroundColor Cyan
Write-Host "       by DopeKitchen Industries" -ForegroundColor DarkCyan
Write-Host "     ======================================" -ForegroundColor Cyan
Write-Host ""

if (-not [Environment]::Is64BitOperatingSystem) { Say "Johnny requires 64-bit Windows." Red; exit 1 }

# ---- Python ----------------------------------------------------------------
function Find-Python {
    foreach ($cmd in @("py -3.12", "py -3.11", "py -3.13", "py -3.10", "python")) {
        try {
            $parts = $cmd.Split(" ")
            $v = & $parts[0] $parts[1..9] -c "import sys;print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(1[0-3])$') { return $cmd }
        } catch {}
    }
    return $null
}

Say "Checking for Python 3.10+..."
$py = Find-Python
if (-not $py) {
    Say "Python not found. Installing Python 3.12 with winget..." Yellow
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $py = Find-Python
    if (-not $py) { Say "Couldn't install Python automatically. Install it from python.org, then run this again." Red; exit 1 }
}
Say "Using $py" Green

# ---- Ollama ----------------------------------------------------------------
Say "Checking for Ollama..."
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue) -or (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")
if (-not $ollama) {
    Say "Ollama not found. Installing with winget..." Yellow
    try {
        winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
    } catch {
        Say "winget failed - the setup wizard will help you download Ollama instead." Yellow
    }
} else { Say "Ollama found." Green }

# ---- Copy files ------------------------------------------------------------
Say "Installing to $Dest ..."
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Dest*" } | Stop-Process -Force
foreach ($item in @("johnny", "assets", "requirements.txt", "README.md")) {
    $p = Join-Path $Src $item
    if (Test-Path $p) { Copy-Item $p -Destination $Dest -Recurse -Force }
}

# ---- Virtual environment ---------------------------------------------------
$venv = Join-Path $Dest "venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Say "Creating private Python environment..."
    $parts = $py.Split(" ")
    & $parts[0] $parts[1..9] -m venv $venv
}
Say "Installing components (this takes a minute)..."
& "$venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
& "$venv\Scripts\python.exe" -m pip install -r "$Dest\requirements.txt" --disable-pip-version-check -q
if ($LASTEXITCODE -ne 0) { Say "Package install failed - check your internet connection." Red; exit 1 }

# ---- Uninstaller entry -----------------------------------------------------
$uninst = @"
`$ErrorActionPreference='SilentlyContinue'
Get-Process pythonw | Where-Object { `$_.Path -like '$Dest*' } | Stop-Process -Force
Remove-Item "`$env:USERPROFILE\Desktop\Johnny.lnk","`$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Johnny.lnk","`$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Johnny.lnk" -Force
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Johnny' -Recurse -Force
`$keep = Read-Host 'Also delete your Johnny memory, skills and settings? (y/N)'
if (`$keep -eq 'y') { Remove-Item "`$env:APPDATA\Johnny" -Recurse -Force }
Set-Location `$env:TEMP
Remove-Item '$Dest' -Recurse -Force
Write-Host 'Johnny has been uninstalled. (Ollama and its models were left in place.)'
"@
Set-Content -Path "$Dest\uninstall.ps1" -Value $uninst -Encoding UTF8
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Johnny"
New-Item -Path $key -Force | Out-Null
Set-ItemProperty $key DisplayName "Johnny AI Butler"
Set-ItemProperty $key Publisher "DopeKitchen Industries"
Set-ItemProperty $key DisplayVersion "1.0.0"
Set-ItemProperty $key InstallLocation $Dest
Set-ItemProperty $key DisplayIcon "$Dest\assets\johnny.ico"
Set-ItemProperty $key UninstallString "powershell -ExecutionPolicy Bypass -File `"$Dest\uninstall.ps1`""
Set-ItemProperty $key NoModify 1

Say "Files installed. Launching the setup wizard..." Green
Start-Process -FilePath "$venv\Scripts\pythonw.exe" -ArgumentList "-m johnny --setup" -WorkingDirectory $Dest
Start-Sleep -Seconds 2

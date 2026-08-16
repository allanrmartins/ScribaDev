# Build do instalador Windows (#142): PyInstaller (spec definitivo) + Inno Setup.
#
# Requisitos: venv do app em %LOCALAPPDATA%\ScribaDev\venv com `pyinstaller`
# instalado (pip install pyinstaller), e Inno Setup 6 (ISCC) instalado
# (winget install -e --id JRSoftware.InnoSetup --scope user).
#
# Uso:  powershell -ExecutionPolicy Bypass -File installer\windows\build.ps1 [-OutDir <pasta>]
# Saída: <OutDir>\ScribaDev-<versao>-setup.exe  (default: installer\windows\out)

param(
    [string]$OutDir = "",
    [string]$Python = ""   # CI (#145): python do runner; vazio = venv do app local
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = (Resolve-Path (Join-Path $here "..\..")).Path
if (-not $OutDir) { $OutDir = Join-Path $here "out" }

if ($Python) { $py = $Python }
else { $py = Join-Path $env:LOCALAPPDATA "ScribaDev\venv\Scripts\python.exe" }
if (-not (Test-Path $py)) { throw "python nao encontrado em $py (rode o setup.ps1 primeiro, ou passe -Python)" }

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if ($iscc) { $iscc = $iscc.Source }
elseif (Test-Path "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") { $iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" }
elseif (Test-Path "C:\Program Files (x86)\Inno Setup 6\ISCC.exe") { $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
else { throw "ISCC (Inno Setup 6) nao encontrado - winget install -e --id JRSoftware.InnoSetup" }

# versao de scriba/__init__.py (fonte unica)
$verLine = Select-String -Path (Join-Path $repo "scriba\__init__.py") -Pattern '__version__\s*=\s*"([^"]+)"'
$version = $verLine.Matches[0].Groups[1].Value
Write-Host "== ScribaDev v$version"

# 1) PyInstaller (spec com os DOIS exes: CLI console + tray windowed)
$work = Join-Path $here "build"
$dist = Join-Path $here "dist"
Write-Host "== PyInstaller (dist enxuto CPU-first)..."
& $py -m PyInstaller (Join-Path $here "scribadev.spec") --noconfirm --workpath $work --distpath $dist
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou ($LASTEXITCODE)" }

# 2) Inno Setup
New-Item -ItemType Directory -Force $OutDir | Out-Null
Write-Host "== ISCC..."
& $iscc /Qp "/DAppVersion=$version" "/DDistDir=$(Join-Path $dist 'scribadev')" "/O$OutDir" (Join-Path $here "scribadev.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC falhou ($LASTEXITCODE)" }

$setup = Join-Path $OutDir "ScribaDev-$version-setup.exe"
$mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host "== OK: $setup ($mb MB)"

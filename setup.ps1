# ScribaDev - instalacao no Windows 11
# Uso: powershell -ExecutionPolicy Bypass -File .\setup.ps1
# (sem acentos neste arquivo de proposito: PowerShell 5.1 le .ps1 sem BOM como ANSI)

$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$AppDir = Join-Path $env:LOCALAPPDATA "ScribaDev"
$VenvDir = Join-Path $AppDir "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

Write-Host ""
Write-Host "=== ScribaDev - setup ===" -ForegroundColor Cyan
Write-Host ""

# --- 1) Localizar Python 3.12+ via py launcher -------------------------------
$pyTag = $null
foreach ($v in @("3.14", "3.13", "3.12")) {
    cmd /c "py -$v -V >nul 2>&1"
    if ($LASTEXITCODE -eq 0) { $pyTag = $v; break }
}
if (-not $pyTag) {
    Write-Host "Python 3.12+ nao encontrado." -ForegroundColor Red
    Write-Host "Instale com:  winget install Python.Python.3.12"
    exit 1
}
Write-Host "[1/6] Python $pyTag encontrado"

# --- 2) Criar venv fora do OneDrive ------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Write-Host "[2/6] Criando venv em $VenvDir ..."
    New-Item -ItemType Directory -Force $AppDir | Out-Null
    & py "-$pyTag" -m venv $VenvDir
} else {
    Write-Host "[2/6] Venv ja existe: $VenvDir"
}

# --- 3) Detectar GPU NVIDIA ---------------------------------------------------
$gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
$installTarget = "."
if ($gpu) {
    $installTarget = ".[cuda]"
    Write-Host "[3/6] GPU NVIDIA detectada ($($gpu[0].Name)) - instalando suporte CUDA (~1.3 GB)"
} else {
    Write-Host "[3/6] Sem GPU NVIDIA - transcricao rodara em CPU"
}

# --- 4) Instalar dependencias --------------------------------------------------
Write-Host "[4/6] Instalando dependencias (pode demorar alguns minutos)..."
& $VenvPython -m pip install --upgrade pip --quiet
Push-Location $ProjectDir
try {
    & $VenvPython -m pip install -e $installTarget
} finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) { Write-Host "Falha no pip install." -ForegroundColor Red; exit 1 }

# --- 5) Shim 'scribadev' no PATH (WindowsApps ja esta no PATH do usuario) ---------
$shimDir = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
if (Test-Path $shimDir) {
    $shim = Join-Path $shimDir "scribadev.cmd"
    "@echo off`r`n`"%LOCALAPPDATA%\ScribaDev\venv\Scripts\scribadev.exe`" %*" | Out-File -FilePath $shim -Encoding ascii
    Write-Host "[5/6] Comando 'scribadev' disponivel no PATH ($shim)"
} else {
    Write-Host "[5/6] Pasta WindowsApps nao encontrada; use o caminho completo: $VenvDir\Scripts\scribadev.exe" -ForegroundColor Yellow
}

# --- 5b) Atalhos na Area de Trabalho e no menu Iniciar ------------------------
Write-Host "[5/6] Criando atalhos (Area de Trabalho + menu Iniciar)..."
& (Join-Path $VenvDir "Scripts\scribadev.exe") shortcut

# --- 6) Pre-baixar o modelo Whisper -------------------------------------------
Write-Host "[6/6] Baixando/validando o modelo Whisper (primeira vez ~1.6 GB)..."
& $VenvPython -m scriba.predownload
if ($LASTEXITCODE -ne 0) {
    Write-Host "Download do modelo falhou (sera tentado de novo na primeira transcricao)." -ForegroundColor Yellow
}

# --- Diagnostico ----------------------------------------------------------------
Write-Host ""
& (Join-Path $VenvDir "Scripts\scribadev.exe") doctor
Write-Host ""
Write-Host "Setup concluido. Proximos passos:" -ForegroundColor Cyan
Write-Host "  - Abra pelo atalho 'ScribaDev' na Area de Trabalho (ou no menu Iniciar)"
Write-Host "  scribadev run            # ou inicie pelo terminal (icone na bandeja)"
Write-Host "  scribadev autostart on   # inicia junto com o Windows"
Write-Host "(abra um NOVO terminal se o comando 'scribadev' nao for reconhecido)"

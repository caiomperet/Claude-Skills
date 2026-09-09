# Gera dist\netmon-setup.exe a partir do código-fonte, em um PC Windows.
# Requisitos: Python 3.10+ e Inno Setup 6 (https://jrsoftware.org/isdl.php).
#   powershell -ExecutionPolicy Bypass -File .\deploy\build-windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

python -m pip install --quiet --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean --onedir --windowed --name netmon netmon_gui.py

$version = python -c "import netmon; print(netmon.VERSION)"
$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 não encontrado. Instale em https://jrsoftware.org/isdl.php" }
& $iscc "/DMyAppVersion=$version" deploy\netmon.iss
Write-Host "Instalador gerado em dist\netmon-setup.exe"

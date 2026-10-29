<#
Dağıtılacak zip'i üretir: dist\ClipLean-<sürüm>-windows.zip

Kullanım:
    .\build.ps1 [-Ffmpeg C:\yol\ffmpeg.exe]

Sürüm numarası cliplean\__init__.py dosyasından okunur.
-Ffmpeg verilmezse PATH'teki ffmpeg kullanılır. libsvtav1 ve libopus içermesi
gerekir; gyan.dev'in "full" sürümü uygundur ("essentials" sürümünde libsvtav1 yok).
#>
param(
    [string]$Ffmpeg = ""
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Version = [regex]::Match((Get-Content cliplean\__init__.py -Raw), '__version__ = "([^"]+)"').Groups[1].Value
if (-not $Version) { throw "Sürüm numarası okunamadı" }

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    python -m venv .venv
}
& $python -m pip install -q -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Paketler kurulamadı" }

& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Testler başarısız" }

if (-not $Ffmpeg) { $Ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source }
$encoders = & $Ffmpeg -hide_banner -encoders 2>$null | Out-String
foreach ($enc in "libsvtav1", "libopus") {
    if ($encoders -notmatch $enc) { throw "$Ffmpeg içinde $enc yok" }
}
$license = Join-Path (Split-Path (Split-Path $Ffmpeg)) "LICENSE"
if (-not (Test-Path $license)) { throw "ffmpeg lisans dosyası bulunamadı: $license" }

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name ClipLean --icon assets\icon.ico `
    --add-data "assets\icon.ico;assets" `
    --collect-all tkinterdnd2 `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller başarısız" }

$app = "dist\ClipLean"
$internal = "$app\_internal"
New-Item -ItemType Directory -Force "$internal\ffmpeg" | Out-Null
Copy-Item $Ffmpeg "$internal\ffmpeg\ffmpeg.exe"
Copy-Item $license "$internal\ffmpeg\LICENSE.txt"
Copy-Item LICENSE "$app\LICENSE.txt"
Copy-Item OKUBENI.txt "$app\OKUBENI.txt"

$zip = "dist\ClipLean-$Version-windows.zip"
Compress-Archive -Path $app -DestinationPath $zip -CompressionLevel Optimal
$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Hazır: $zip ($size MB)"
Write-Host "SHA256: $((Get-FileHash $zip -Algorithm SHA256).Hash)"

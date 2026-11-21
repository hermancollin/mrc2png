# Build dist\mrc2png-windows-x64.exe (a single-file app).
# Usage (PowerShell, from anywhere):
#   scripts\build_windows.ps1 [-Python <path-to-python.exe>]
# Default Python: .venv\py312\Scripts\python.exe in the repository.
param(
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not $Python) { $Python = ".venv\py312\Scripts\python.exe" }

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $Python -m PyInstaller --noconfirm --clean mrc2png.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Move-Item dist\mrc2png.exe dist\mrc2png-windows-x64.exe

# TODO(signing): sign with a code-signing certificate so SmartScreen stops
# warning users, e.g.:
#   signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f cert.pfx /p $env:CERT_PASSWORD dist\mrc2png-windows-x64.exe

Write-Host "Built dist\mrc2png-windows-x64.exe"

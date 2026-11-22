param(
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) {
        throw $Message
    }
}

if (-not (Test-Path ".venv")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py "-$PythonVersion" -m venv .venv
    } else {
        python -m venv .venv
    }
    Assert-LastExitCode "Could not create .venv. Install 64-bit Python $PythonVersion first."
}
. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
Assert-LastExitCode "pip upgrade failed."
python -m pip install -r requirements.txt
Assert-LastExitCode "Runtime dependency installation failed."
python -m pip install "pyinstaller>=6.10,<7"
Assert-LastExitCode "PyInstaller installation failed."

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name ChatroomClient `
    --paths . `
    packaging\windows_client.py
Assert-LastExitCode "PyInstaller build failed."

$envFile = "dist\ChatroomClient\.env"
if (-not (Test-Path $envFile)) {
    Copy-Item "packaging\windows-client.env.example" $envFile
}

Write-Host ""
Write-Host "Build finished: dist\ChatroomClient\ChatroomClient.exe"
Write-Host "Edit dist\ChatroomClient\.env and set CLIENT_HOST to the server LAN IP."

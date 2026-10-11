param(
    [ValidateSet("cpu", "cuda")][string]$Device = "cpu",
    [switch]$Minimal,
    [switch]$SkipExifTool,
    [ValidateSet("none", "cpu", "directml")][string]$Onnx = "none"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program (exit $LASTEXITCODE)" }
}

$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    & $launcher.Source -3.11 -c "import sys; print(sys.version)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Invoke-Checked $launcher.Source @("-3.11", "-m", "venv", ".venv")
    } else {
        Invoke-Checked $launcher.Source @("-3", "-m", "venv", ".venv")
    }
} else {
    $pythonCommand = Get-Command python -ErrorAction Stop
    Invoke-Checked $pythonCommand.Source @("-m", "venv", ".venv")
}
$pythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Invoke-Checked $pythonExe @("-c", "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer is required'")
Invoke-Checked $pythonExe @("-m", "pip", "install", "--upgrade", "pip")
if (-not $Minimal) {
    $index = if ($Device -eq "cuda") { "https://download.pytorch.org/whl/cu128" } else { "https://download.pytorch.org/whl/cpu" }
    Invoke-Checked $pythonExe @("-m", "pip", "install", "torch", "torchvision", "--index-url", $index)
    Invoke-Checked $pythonExe @("-m", "pip", "install", "-e", ".[ai,raw]")
} else {
    Invoke-Checked $pythonExe @("-m", "pip", "install", "-e", ".[raw]")
}
if ($Onnx -eq "cpu") { Invoke-Checked $pythonExe @("-m", "pip", "install", ".[onnx]") }
if ($Onnx -eq "directml") { Invoke-Checked $pythonExe @("-m", "pip", "install", ".[directml]") }

if (-not $SkipExifTool -and -not (Get-Command exiftool -ErrorAction SilentlyContinue)) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        & $winget.Source install --id OliverBetz.ExifTool --exact --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "ExifTool installation failed. Install it manually and configure exiftool_path."
        }
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    } else {
        Write-Warning "Install ExifTool from https://exiftool.org/ and configure exiftool_path."
    }
}
if (-not (Test-Path "config.json")) {
    Invoke-Checked $pythonExe @("-m", "photo_sort_ai", "init", "--preset", $(if ($Minimal) { "light" } else { "balanced" }))
}
Invoke-Checked $pythonExe @("-m", "photo_sort_ai", "doctor")
Write-Host "Setup complete. Open start.bat, choose your photo folder, and save the settings."

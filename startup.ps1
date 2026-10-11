param([switch]$Remove)
$ErrorActionPreference = "Stop"
$startupDirectory = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDirectory "Photo Sort AI Watch.lnk"
if ($Remove) {
    Remove-Item $shortcutPath -ErrorAction SilentlyContinue
    Write-Host "Automatic startup removed."
    exit
}
$pythonWindowless = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
$configPath = Join-Path $PSScriptRoot "config.json"
if (-not (Test-Path $pythonWindowless) -or -not (Test-Path $configPath)) {
    throw "Run install.bat and save your settings before enabling startup."
}
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonWindowless
$shortcut.Arguments = '-m photo_sort_ai --config "' + $configPath + '" watch'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Save()
Write-Host "Folder watching will start at your next Windows sign-in. Logs are stored in the configured state_dir."

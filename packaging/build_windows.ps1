# Build Viper IDE on Windows: PyInstaller bundle, portable zip, and Inno Setup installer.
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 [-SkipInstaller] [-SelfTest]
# CI signs the app between two stages: -BundleOnly (PyInstaller + self test), then -PackageOnly
# (portable zip + installer from the existing dist\ViperIDE).
# Keep this file plain ASCII with CRLF line endings and a UTF-8 BOM (Windows PowerShell 5.1).
param(
    [switch]$SkipInstaller,
    [switch]$SelfTest,
    [switch]$BundleOnly,
    [switch]$PackageOnly
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host 'Creating build virtual environment...'
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed' }
}
$py = Join-Path $root '.venv\Scripts\python.exe'
& $py -m pip install --disable-pip-version-check -q -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

$version = (& $py -c "import viper_ide; print(viper_ide.__version__)").Trim()
Write-Host "Building Viper IDE $version"

if (-not $PackageOnly) {
& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\ViperIDE.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
}
if (-not (Test-Path 'dist\ViperIDE\ViperIDE.exe')) { throw 'dist\ViperIDE\ViperIDE.exe not found' }

if ($SelfTest -and -not $PackageOnly) {
    $result = Join-Path $root 'dist\selftest.json'
    if (Test-Path $result) { Remove-Item $result }
    $env:QT_QPA_PLATFORM = 'offscreen'
    $p = Start-Process -FilePath 'dist\ViperIDE\ViperIDE.exe' -ArgumentList @('--selftest', $result) -Wait -PassThru
    Remove-Item Env:\QT_QPA_PLATFORM
    Write-Host "Self test exit code: $($p.ExitCode)"
    if (Test-Path $result) { Get-Content $result }
    if ($p.ExitCode -ne 0) { throw 'Self test failed' }
}

if ($BundleOnly) { exit 0 }

# The privacy statement, with CRLF line endings, for the installer's info page and the portable zip.
New-Item -ItemType Directory -Force 'build' | Out-Null
$privacy = Join-Path $root 'build\PRIVACY.txt'
Set-Content -Path $privacy -Encoding ASCII -Value (Get-Content 'PRIVACY.md')

# Portable zip: the same bundle plus portable.txt, which keeps settings and data in Data\ beside the exe.
$portableDir = Join-Path $root 'build\portable\ViperIDE'
if (Test-Path (Split-Path $portableDir)) { Remove-Item (Split-Path $portableDir) -Recurse -Force }
New-Item -ItemType Directory -Force (Split-Path $portableDir) | Out-Null
Copy-Item 'dist\ViperIDE' $portableDir -Recurse
Copy-Item $privacy (Join-Path $portableDir 'PRIVACY.txt')
Set-Content -Path (Join-Path $portableDir 'portable.txt') -Encoding ASCII -Value @(
    'Viper IDE portable. While this file is here, settings, downloaded Pythons, venvs and tools',
    'are kept in the Data folder next to ViperIDE.exe. Delete it to use the per-user folders instead.')
$pathsJson = Join-Path $root 'build\portable-paths.json'
$p = Start-Process -FilePath (Join-Path $portableDir 'ViperIDE.exe') -ArgumentList @('--paths', $pathsJson) -Wait -PassThru
$paths = Get-Content $pathsJson -Raw | ConvertFrom-Json
Write-Host "Portable data folder: $($paths.data)"
if ($p.ExitCode -ne 0 -or -not $paths.portable -or -not $paths.data.StartsWith($portableDir)) {
    throw 'Portable copy does not keep its data beside the exe'
}
Remove-Item (Join-Path $portableDir 'Data') -Recurse -Force -ErrorAction SilentlyContinue
$zip = Join-Path $root "dist\ViperIDE_Portable_$version.zip"
if (Test-Path $zip) { Remove-Item $zip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($portableDir, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
Get-Item $zip | Format-List Name, Length

if (-not $SkipInstaller) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) not found' }
    & $iscc "/DAppVersion=$version" 'packaging\ViperIDE.iss'
    if ($LASTEXITCODE -ne 0) { throw 'ISCC failed' }
    Get-Item "dist\ViperIDE_Setup_$version.exe" | Format-List Name, Length
}

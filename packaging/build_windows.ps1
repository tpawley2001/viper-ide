# Build Viper IDE on Windows: PyInstaller bundle + Inno Setup installer.
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 [-SkipInstaller] [-SelfTest]
# Keep this file plain ASCII with CRLF line endings and a UTF-8 BOM (Windows PowerShell 5.1).
param(
    [switch]$SkipInstaller,
    [switch]$SelfTest
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

& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\ViperIDE.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

if ($SelfTest) {
    $result = Join-Path $root 'dist\selftest.json'
    if (Test-Path $result) { Remove-Item $result }
    $env:QT_QPA_PLATFORM = 'offscreen'
    $p = Start-Process -FilePath 'dist\ViperIDE\ViperIDE.exe' -ArgumentList @('--selftest', $result) -Wait -PassThru
    Remove-Item Env:\QT_QPA_PLATFORM
    Write-Host "Self test exit code: $($p.ExitCode)"
    if (Test-Path $result) { Get-Content $result }
    if ($p.ExitCode -ne 0) { throw 'Self test failed' }
}

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

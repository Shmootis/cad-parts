# Builds dist\CAD-Parts-Setup-<version>.exe: the app window (CAD Parts.exe) first, then the installer.
# Version comes from version.json. Needs uv (for PyInstaller) and Inno Setup 6.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$version = (Get-Content -Raw version.json | ConvertFrom-Json).version
Write-Host "CAD Parts $version"

# the app's self-checks first: never build a release that fails them
& uv run --python 3.12 python checks.py
if ($LASTEXITCODE) { throw "checks.py failed: fix it before building a release" }

& cmd /c "`"$PSScriptRoot\build-exe.cmd`""
if ($LASTEXITCODE) { throw "building CAD Parts.exe failed" }
Remove-Item -ErrorAction SilentlyContinue 'CAD Parts.old.exe'

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 isn't installed (winget install JRSoftware.InnoSetup)" }
& $iscc /Q "/DAppVersion=$version" installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

$out = Join-Path $PSScriptRoot "dist\CAD-Parts-Setup-$version.exe"
Write-Host "Built $out ($([math]::Round((Get-Item $out).Length / 1MB, 1)) MB)"

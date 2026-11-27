# Show an STL in the single CAD Parts viewer Blender window (starts Blender if it isn't open).
# Usage: powershell -File show.ps1 -Path <file.stl> -Blender <blender.exe> -State <folder for trigger/pid/log>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Blender,
    [Parameter(Mandatory = $true)][string]$State
)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$full = (Resolve-Path -LiteralPath $Path).Path
New-Item -ItemType Directory -Force -Path $State | Out-Null
Set-Content -LiteralPath (Join-Path $State "load.txt") -Value $full -Encoding utf8

$running = $null
$pidFile = Join-Path $State "viewer.pid"
if (Test-Path -LiteralPath $pidFile) {
    $id = 0   # an empty or corrupt viewer.pid just means "no viewer running"
    if ([int]::TryParse(("" + (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)).Trim(), [ref]$id) -and $id -gt 0) {
        $running = Get-Process -Id $id -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -eq "blender" }
    }
}

if ($running) {
    $null = (New-Object -ComObject WScript.Shell).AppActivate($running.Id)
    "reloaded in existing Blender (pid $($running.Id))"
} else {
    $env:CADPARTS_VIEWER_DIR = $State   # inherited by Blender: viewer.py keeps its trigger/pid/log there
    Start-Process -FilePath $Blender -ArgumentList "--python `"$(Join-Path $here 'viewer.py')`""
    "started new Blender viewer"
}

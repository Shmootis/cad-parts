@echo off
rem Starts the CAD Parts dashboard in the background (if it isn't running) and opens it in your browser.
rem The page needs the server's launch key from %LOCALAPPDATA%\CAD Parts\server.key, as http://127.0.0.1:8765/?k=KEY
rem Folder and key file are passed through environment variables, so paths with apostrophes work.
cd /d "%~dp0"
set "CADPARTS_DIR=%~dp0"
powershell -NoProfile -Command "$d=$env:CADPARTS_DIR; $kf=Join-Path $env:LOCALAPPDATA 'CAD Parts\server.key'; function Up { try { $r=[Net.WebRequest]::Create('http://127.0.0.1:8765/api/ping'); $r.Proxy=$null; $r.Timeout=1000; $r.GetResponse().Close(); $true } catch { $false } }; if (-not (Up)) { Start-Process -WindowStyle Hidden -WorkingDirectory $d -FilePath uv -ArgumentList 'run','--python','3.12','python','server.py'; $t=0; while (-not (Up) -and $t -lt 600) { Start-Sleep -Milliseconds 500; $t++ } }; if (-not (Up)) { 'CAD Parts did not start.'; exit 1 }; $key=$null; for ($i=0; $i -lt 40 -and -not $key; $i++) { $key=Get-Content -LiteralPath $kf -Raw -ErrorAction SilentlyContinue; if ($key) { $key=$key.Trim() } else { Start-Sleep -Milliseconds 250 } }; if (-not $key) { 'CAD Parts is running, but its launch key is missing: ' + $kf; exit 1 }; Start-Process ('http://127.0.0.1:8765/?k=' + [uri]::EscapeDataString($key))"
if errorlevel 1 pause

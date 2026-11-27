@echo off
rem Stops the CAD Parts dashboard server (port 8765) and its build123d worker (port 8766).
powershell -NoProfile -Command "try { Invoke-RestMethod -Method Post -TimeoutSec 5 http://127.0.0.1:8765/api/quit | Out-Null; 'Stopped the dashboard.' } catch { }; Start-Sleep -Seconds 1; $n=0; Get-NetTCPConnection -LocalPort 8765,8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; $n++ }; if ($n) { 'Stopped ' + $n + ' process(es).' } else { 'Nothing else was running.' }"
timeout /t 2 >nul

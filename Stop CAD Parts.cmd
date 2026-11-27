@echo off
rem Stops the CAD Parts dashboard server.
powershell -NoProfile -Command "try { Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/quit | Out-Null; 'Stopped.' } catch { 'It was not running.' }"
timeout /t 2 >nul

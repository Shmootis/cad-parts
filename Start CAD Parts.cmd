@echo off
rem Starts the CAD Parts dashboard in the background and opens it in your browser.
cd /d "%~dp0"
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -WorkingDirectory '%~dp0' uv 'run --python 3.12 python server.py --open'"

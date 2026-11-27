@echo off
rem Rebuilds "CAD Parts.exe" (the desktop window) from launcher.py. Build files go to %TEMP%, only the exe lands here.
rem Works while the app is open: the running exe is renamed to "CAD Parts.old.exe" (delete it once the app is closed).
cd /d "%~dp0"
set B=%TEMP%\cadparts-exe-build
uv run --python 3.12 --with pywebview --with pyinstaller pyinstaller launcher.py --onefile --windowed --noconfirm ^
  --name "CAD Parts" --icon "%~dp0cadparts.ico" --distpath "%B%\dist" --workpath "%B%\work" --specpath "%B%" || exit /b 1
if exist "CAD Parts.old.exe" del "CAD Parts.old.exe" 2>nul
if exist "CAD Parts.exe" ren "CAD Parts.exe" "CAD Parts.old.exe"
copy /y "%B%\dist\CAD Parts.exe" "CAD Parts.exe" >nul && echo Built CAD Parts.exe

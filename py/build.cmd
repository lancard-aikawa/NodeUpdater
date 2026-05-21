@echo off
REM Build PypkgUpdater.exe as a single file via PyInstaller.
REM Prerequisite: py -m pip install --user pyinstaller
REM
REM Run from repository root so that shared/ and py/ packages resolve.

setlocal
cd /d "%~dp0\.."

py -m PyInstaller ^
  --onefile ^
  --noconsole ^
  --name PypkgUpdater ^
  --paths . ^
  --collect-submodules tkinter ^
  --collect-submodules shared ^
  --collect-submodules py ^
  py\main.py

if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

echo.
echo Output: dist\PypkgUpdater.exe
endlocal
